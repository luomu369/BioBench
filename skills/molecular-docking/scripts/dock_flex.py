#!/usr/bin/env python3
"""Flexible-residue docking with AutoDock Vina.

Vina's default is a rigid receptor. With --flex it also samples side-chain
torsions for a chosen set of receptor residues, which matters when the
pocket has to rearrange to accommodate a new ligand — a rigid dock scores
the ligand against a frozen pocket and can miss good poses that need a
side chain to move.

The flexible residues are written to a separate PDBQT; Vina keeps them
rigid in the main receptor file and merges the moving parts at search time.

Usage:
  python3 dock_flex.py --receptor 7XWQ.cif --ligand "CCO" \
      --center -3.3 -7.6 -39.7 --size 20 20 20 \
      --flex-cutoff 8.0 --flex-chain A --out flex_out

Exit codes: 0 ok, 1 docking failed, 2 bad input, 3 no flexible residues.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import gemmi

VINA = "/home/asus_gx10/tools/vina/vina"
DOCK_PY = "/home/asus_gx10/miniconda3/envs/docking/bin/python"
MK_RECEPTOR = "/home/asus_gx10/miniconda3/envs/docking/bin/mk_prepare_receptor.py"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def to_pdb(st, path):
    """Write a gemmi structure as PDB, dropping waters and altlocs."""
    st.setup_entities()
    st.remove_ligands_and_waters()
    st.remove_alternative_conformations()
    st.write_pdb(str(path))


def prepare_flex_receptor(pdb: Path, stem: Path, flex_spec: str):
    """Split a receptor into rigid + flexible PDBQT with meeko.

    meeko's mk_prepare_receptor.py writes the BRANCH/ENDBRANCH torsion-tree
    records Vina requires in the flex file; building the PDBQT by hand
    leaves them out and Vina rejects it with "Unknown or inappropriate tag
    found in flex residue".

    Returns (rigid_pdbqt, flex_pdbqt).
    """
    r = sh([DOCK_PY, str(MK_RECEPTOR), "--read_pdb", str(pdb),
            "-o", str(stem), "-p", "-a", "-f", flex_spec])
    rigid = stem.parent / (stem.name + "_rigid.pdbqt")
    flex = stem.parent / (stem.name + "_flex.pdbqt")
    if r.returncode != 0 or not rigid.is_file() or not flex.is_file():
        print(f"error: flexible receptor preparation failed\n"
              f"{(r.stdout + r.stderr)[-500:]}", file=sys.stderr)
        sys.exit(1)
    return rigid, flex


def pick_flex_residues(st, chain_id, center, cutoff):
    """Amino-acid residues of `chain_id` within `cutoff` of the centre.

    Distance is measured to the residue's *nearest atom*, not its centroid.
    A long side chain reaches into the box while its centroid stays well
    outside it — MET 479 in 7XWQ has its centroid 11.4 A from the pocket
    centre but its CB/CG sit right where the ligand goes. Judging by
    centroid leaves such residues rigid, and the ligand then clashes with
    a side chain Vina was never allowed to move.

    Non-polymer residues are skipped: a co-crystallised ligand sitting in
    the box is not a side chain and cannot be made flexible.
    """
    out = []
    for ch in st[0]:
        if ch.name != chain_id:
            continue
        for res in ch:
            if gemmi.find_tabulated_residue(res.name) is None:
                continue
            if not res.name.isalpha() or len(res.name) != 3:
                continue
            d = min(
                math.sqrt((a.pos.x - center[0]) ** 2
                          + (a.pos.y - center[1]) ** 2
                          + (a.pos.z - center[2]) ** 2)
                for a in res
            ) if len(res) else float("inf")
            if d < cutoff:
                out.append((res.seqid.num, res.name, d))
    return sorted(out, key=lambda r: r[2])


def read_pdbqt_model(path: Path, model: int = 1) -> list[dict]:
    """Atoms of one MODEL from a Vina PDBQT, as plain dicts."""
    atoms, in_model = [], False
    for line in path.read_text().splitlines():
        if line.startswith("MODEL"):
            in_model = (int(line.split()[1]) == model)
            continue
        if line.startswith("ENDMDL"):
            if in_model:
                break
            continue
        if not in_model:
            continue
        if line.startswith(("ATOM", "HETATM")):
            raw = line[76:78].strip()
            if raw.upper() == "A":
                raw = "C"  # aromatic carbon — not a real element symbol
            atoms.append({
                "name": line[12:16].strip(),
                "resn": line[17:20].strip(),
                "chain": line[21],
                "resi": line[22:27].strip(),
                "x": float(line[30:38]), "y": float(line[38:46]),
                "z": float(line[46:54]),
                "elem": raw or line[12:16].strip()[0],
            })
    return atoms


def build_complex(docked: Path, receptor_pdb: Path, out: Path,
                  flex_chain: str, ligand_chain: str = "L") -> None:
    """Write receptor + moved side chains + ligand as one PDB.

    The ligand is whatever Vina wrote with an empty chain ID and residue
    name UNL; everything else is a receptor residue whose coordinates Vina
    changed. Those residues are re-emitted over the receptor's own copies
    so the published complex matches the pose that was actually scored.
    """
    atoms = read_pdbqt_model(docked, model=1)
    moved: dict[str, list[dict]] = {}
    ligand: list[dict] = []
    for a in atoms:
        if a["resn"] == "UNL" or a["chain"] not in (flex_chain, " "):
            ligand.append(a)
        else:
            moved.setdefault(a["resi"], []).append(a)

    rec_lines = []
    for line in receptor_pdb.read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            if line[22:27].strip() in moved:
                continue  # replaced below with the moved coordinates
        if line.strip() == "END":
            continue
        rec_lines.append(line)

    serial = 0
    for line in rec_lines:
        if line.startswith(("ATOM", "HETATM")):
            serial = max(serial, int(line[6:11]))
    for resi in sorted(moved, key=lambda r: int(r)):
        for a in moved[resi]:
            serial += 1
            rec_lines.append(
                f"ATOM  {serial:5d} {a['name']:<4s} {a['resn']:>3s} "
                f"{flex_chain}{int(resi):4d}    "
                f"{a['x']:8.3f}{a['y']:8.3f}{a['z']:8.3f}"
                f"{'  1.00':>6s}{'  0.00':>6s}          "
                f"{a['elem']:>2s}"
            )

    lig_lines = [
        f"HETATM{i:5d} {a['name']:<4s} {'UNL':>3s} {ligand_chain}{1:4d}    "
        f"{a['x']:8.3f}{a['y']:8.3f}{a['z']:8.3f}"
        f"{'  1.00':>6s}{'  0.00':>6s}          "
        f"{a['elem']:>2s}"
        for i, a in enumerate(ligand, 1)
    ]

    out.write_text("\n".join(rec_lines + lig_lines) + "\nTER\nEND\n")
    print(f"== complex: {len(moved)} moved residues + "
          f"{len(ligand)} ligand atoms -> {out}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--receptor", required=True)
    ap.add_argument("--ligand", help="ligand SMILES")
    ap.add_argument("--ligand-file", help="ligand PDBQT/SDF")
    ap.add_argument("--center", nargs=3, type=float, required=True)
    ap.add_argument("--size", nargs=3, type=float, default=[20, 20, 20])
    ap.add_argument("--flex-chain", required=True,
                    help="chain to take flexible residues from")
    ap.add_argument("--flex-cutoff", type=float, default=8.0,
                    help="residues within this distance of the box centre "
                         "become flexible (default 8 A)")
    ap.add_argument("--flex-residues",
                    help="explicit comma-separated residue numbers, "
                         "overrides --flex-cutoff")
    ap.add_argument("--num-modes", type=int, default=5)
    ap.add_argument("--exhaustiveness", type=int, default=16)
    ap.add_argument("--cpu", type=int, default=0,
                    help="threads for Vina; 0 = all available cores. Vina is "
                         "CPU-only and its auto-detection often falls back "
                         "to a single thread, so this is passed explicitly.")
    ap.add_argument("--out", default="flex_dock_out")
    args = ap.parse_args()

    if not args.ligand and not args.ligand_file:
        ap.error("provide --ligand or --ligand-file")

    receptor = Path(args.receptor)
    if not receptor.is_file():
        print(f"error: receptor not found: {receptor}", file=sys.stderr)
        return 2

    work = Path(args.out)
    work.mkdir(parents=True, exist_ok=True)

    # ---- receptor: mmCIF -> PDB, then split into rigid + flexible ----
    st = gemmi.read_structure(str(receptor))
    st.setup_entities()

    if args.flex_residues:
        wanted = {int(x) for x in args.flex_residues.split(",")}
        flex = [(n, "?", 0.0) for n in sorted(wanted)]
    else:
        flex = pick_flex_residues(st, args.flex_chain, args.center,
                                  args.flex_cutoff)
    if not flex:
        print(f"error: no residues within {args.flex_cutoff} A of the box "
              f"centre on chain {args.flex_chain} — raise --flex-cutoff or "
              "pass --flex-residues", file=sys.stderr)
        return 3

    flex_nums = {n for n, _, _ in flex}
    print(f"== flexible residues ({len(flex)}): "
          f"{', '.join(f'{n}{nm}' for n, nm, _ in flex)}", file=sys.stderr)

    # meeko does the rigid/flexible split itself, given the whole receptor
    # and a list of flexible residues.
    rec_pdb = work / "receptor.pdb"
    to_pdb(st, rec_pdb)
    flex_spec = ",".join(f"{args.flex_chain}:{n}" for n in sorted(flex_nums))
    rec_pdbqt, flex_pdbqt = prepare_flex_receptor(
        rec_pdb, work / "receptor", flex_spec)

    # ---- ligand ----
    if args.ligand_file:
        lig_pdbqt = Path(args.ligand_file)
    else:
        lig_pdbqt = work / "ligand.pdbqt"
        r = sh([DOCK_PY, "-c", f"""
from meeko import MoleculePreparation
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles({args.ligand!r})
if mol is None:
    raise SystemExit('invalid SMILES')
mol = Chem.AddHs(mol)
if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
    raise SystemExit('could not generate 3D conformer')
AllChem.MMFFOptimizeMolecule(mol)

prep = MoleculePreparation()
prep.prepare(mol)
open({str(lig_pdbqt)!r}, 'w').write(prep.write_pdbqt_string())
"""])
        if r.returncode != 0 or not lig_pdbqt.is_file():
            print("error: ligand -> PDBQT failed\n"
                  f"{r.stderr[-400:]}", file=sys.stderr)
            return 1

    # ---- dock ----
    out_pdbqt = work / "docked_flex.pdbqt"
    cmd = [
        VINA,
        "--receptor", str(rec_pdbqt),
        "--flex", str(flex_pdbqt),
        "--ligand", str(lig_pdbqt),
        "--center_x", str(args.center[0]),
        "--center_y", str(args.center[1]),
        "--center_z", str(args.center[2]),
        "--size_x", str(args.size[0]),
        "--size_y", str(args.size[1]),
        "--size_z", str(args.size[2]),
        "--out", str(out_pdbqt),
        "--num_modes", str(args.num_modes),
        "--exhaustiveness", str(args.exhaustiveness),
        "--cpu", str(args.cpu),
    ]
    print("== docking (flexible receptor)", file=sys.stderr)
    r = sh(cmd)
    (work / "vina.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print(f"error: vina failed\n{(r.stdout + r.stderr)[-500:]}",
              file=sys.stderr)
        return 1

    modes = []
    for line in r.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+([-\d.eE+]+)\s+([-\d.]+)\s+([-\d.]+)", line)
        if m:
            modes.append({
                "mode": int(m.group(1)),
                "affinity_kcal_mol": float(m.group(2)),
                "rmsd_lb": float(m.group(3)),
                "rmsd_ub": float(m.group(4)),
            })

    # ---- viewable complex: receptor + moved side chains + ligand ----
    # A flexible run returns the ligand *and* the side chains it moved, but
    # rec_pdb still holds those residues in their original positions.
    # Publishing that receptor next to the new ligand shows the ligand
    # clashing with side chains Vina had already rotated away — the pose
    # looks impossible even though the docking was fine. Write the moved
    # residues back over the receptor's copies.
    complex_pdb = work / "complex.pdb"
    try:
        build_complex(out_pdbqt, rec_pdb, complex_pdb, args.flex_chain)
    except Exception as e:
        print(f"warning: could not build complex.pdb ({e})", file=sys.stderr)

    summary = {
        "receptor": str(receptor),
        "ligand": args.ligand or args.ligand_file,
        "center": list(args.center),
        "size": args.size,
        "flex_chain": args.flex_chain,
        "flex_residues": [f"{n}{nm}" for n, nm, _ in flex],
        "num_modes": len(modes),
        "best_affinity": modes[0]["affinity_kcal_mol"] if modes else None,
        "modes": modes,
        "output": str(out_pdbqt),
        "complex_pdb": str(complex_pdb) if complex_pdb.is_file() else None,
    }
    (work / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
