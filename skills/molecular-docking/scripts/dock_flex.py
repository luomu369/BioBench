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
            pts = [(a.pos.x, a.pos.y, a.pos.z) for a in res]
            if not pts:
                continue
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
            mz = sum(p[2] for p in pts) / len(pts)
            d = math.sqrt((mx - center[0]) ** 2 + (my - center[1]) ** 2
                          + (mz - center[2]) ** 2)
            if d < cutoff:
                out.append((res.seqid.num, res.name, d))
    return sorted(out, key=lambda r: r[2])


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
    }
    (work / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
