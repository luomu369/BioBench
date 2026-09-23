#!/usr/bin/env python3
"""Dock a small-molecule ligand into a protein receptor with AutoDock Vina.

Handles the full pipeline: receptor (mmCIF/PDB) -> PDBQT, ligand (SMILES) ->
PDBQT, docking, and a summary of the scored poses.

Usage:
  # Explicit box
  python3 dock.py --receptor 7XWO.cif --ligand "CCOc1ccc(...)cc1" \
      --center 12.5 -8.3 45.1 --size 20 20 20 --out results/

  # Auto-detect the box from a bound ligand in the structure
  python3 dock.py --receptor 7XWO.cif --ligand "CCO" --auto-box --out results/

  # Ligand from a file
  python3 dock.py --receptor r.cif --ligand-file mol.sdf --center 0 0 0 \
      --size 25 25 25 --out results/

Exit codes: 0 ok, 1 docking failed, 2 bad input.
"""

import argparse
import json
import re
import sys
from pathlib import Path

DOCK_PY = "/home/asus_gx10/miniconda3/envs/docking/bin/python"
VINA = "/home/asus_gx10/tools/vina/vina"
MK_RECEPTOR = "/home/asus_gx10/miniconda3/envs/docking/bin/mk_prepare_receptor.py"


def sh(cmd, **kw):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def prepare_receptor(receptor: Path, work: Path) -> Path:
    """mmCIF/PDB -> PDB -> PDBQT."""
    pdbqt = work / "receptor.pdbqt"
    if pdbqt.is_file():
        return pdbqt

    # mmCIF -> PDB with gemmi (Vina's tools only read PDB/PDBQT)
    if receptor.suffix in (".cif", ".mmcif"):
        pdb = work / "receptor.pdb"
        r = sh([DOCK_PY, "-c", f"""
import gemmi
st = gemmi.read_structure({str(receptor)!r})
st.setup_entities()
st.remove_ligands_and_waters()
st.remove_alternative_conformations()
st.write_pdb({str(pdb)!r})
"""])
        if r.returncode != 0:
            print(f"error: mmCIF -> PDB failed\n{r.stderr[-400:]}", file=sys.stderr)
            sys.exit(2)
    else:
        pdb = receptor

    r = sh([DOCK_PY, str(MK_RECEPTOR), "--read_pdb", str(pdb),
            "-o", str(work / "receptor"), "-p", "-a", "--default_altloc", "A"])
    if r.returncode != 0 or not pdbqt.is_file():
        print(f"error: receptor -> PDBQT failed\n{r.stdout[-400:]}{r.stderr[-400:]}",
              file=sys.stderr)
        sys.exit(2)
    return pdbqt


def prepare_ligand(smiles: str, work: Path) -> Path:
    """SMILES -> 3D coords -> PDBQT."""
    pdbqt = work / "ligand.pdbqt"
    r = sh([DOCK_PY, "-c", f"""
from meeko import MoleculePreparation
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles({smiles!r})
if mol is None:
    raise SystemExit('invalid SMILES')
mol = Chem.AddHs(mol)
if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
    raise SystemExit('could not generate 3D conformer')
AllChem.MMFFOptimizeMolecule(mol)

prep = MoleculePreparation()
prep.prepare(mol)
open({str(pdbqt)!r}, 'w').write(prep.write_pdbqt_string())
"""])
    if r.returncode != 0 or not pdbqt.is_file():
        print(f"error: ligand -> PDBQT failed\n{r.stderr[-400:]}", file=sys.stderr)
        sys.exit(2)
    return pdbqt


def auto_box(receptor: Path, work: Path):
    """Center the search box on the first bound ligand found in the structure."""
    r = sh([DOCK_PY, "-c", f"""
import gemmi
st = gemmi.read_structure({str(receptor)!r})
st.setup_entities()
for model in st:
    for chain in model:
        for res in chain:
            if res.het_flag == 'H':
                continue
    break
# Collect HETATM residue centroids
import statistics
xs, ys, zs = [], [], []
for model in st:
    for chain in model:
        for res in chain:
            name = res.name
            if name in ('HOH', 'WAT'):
                continue
            try:
                if gemmi.find_tabulated_residue(name).is_amino_acid():
                    continue
            except Exception:
                pass
            for atom in res:
                xs.append(atom.pos.x); ys.append(atom.pos.y); zs.append(atom.pos.z)
if not xs:
    raise SystemExit('no ligand found')
print(round(statistics.mean(xs), 2), round(statistics.mean(ys), 2),
      round(statistics.mean(zs), 2))
"""])
    if r.returncode != 0:
        return None
    parts = r.stdout.strip().split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def pdbqt_to_pdb(src: Path, dst: Path, model: int = 1,
                 chain_id: str = "L") -> None:
    """Extract one MODEL from a Vina PDBQT and rewrite it as plain PDB.

    Two fixes are applied so the result renders in structure viewers:
      1. PDBQT ATOM records carry two extra trailing columns (partial charge
         and AutoDock atom type) that PDB parsers reject — they are stripped.
      2. Vina writes the ligand with an empty chain ID and residue name UNL,
         which causes viewers to merge it into a neighbouring chain. A real
         chain ID is written instead so the ligand stays a separate entity.
    """
    lines = []
    in_model = False
    seen_any = False
    serial = 0
    for line in src.read_text().splitlines():
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
            serial += 1
            # PDB fixed-column layout: name, resName, chainID, resSeq, coords
            name = line[12:16].strip()
            x, y, z = line[30:38], line[38:46], line[46:54]
            elem = (line[76:78].strip() or name[0])[:2].rjust(2)
            # cols: 1-6 HETATM, 7-11 serial, 13-16 name, 18-20 resName,
            #       22 chainID, 23-26 resSeq, 31-38/39-46/47-54 x/y/z
            lines.append(
                f"HETATM{serial:5d} {name:<4s} {'UNL':>3s} {chain_id}{1:4d}    "
                f"{x}{y}{z}{'  1.00':>6s}{'  0.00':>6s}          "
                f"{elem:>2s}{'L':>2s}"
            )
            seen_any = True
    if not seen_any:
        raise ValueError(f"no ATOM records in MODEL {model}")
    # TER only — no END. This file is meant to be appended to the receptor
    # PDB, and a PDB parser stops at the first END it sees, which would
    # silently drop the ligand from the complex.
    dst.write_text("\n".join(lines) + "\nTER\n")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--receptor", required=True, help="mmCIF or PDB structure")
    ap.add_argument("--ligand", help="ligand SMILES")
    ap.add_argument("--ligand-file", help="ligand file (.sdf/.mol/.pdb)")
    ap.add_argument("--center", nargs=3, type=float,
                    help="box center x y z (Å)")
    ap.add_argument("--size", nargs=3, type=float, default=[22, 22, 22],
                    help="box size x y z (Å), default 22 22 22")
    ap.add_argument("--auto-box", action="store_true",
                    help="center the box on a bound ligand in the receptor")
    ap.add_argument("--num-modes", type=int, default=9)
    ap.add_argument("--exhaustiveness", type=int, default=8)
    ap.add_argument("--energy-range", type=float, default=3)
    ap.add_argument("--out", default="docking_out")
    args = ap.parse_args()

    if not args.ligand and not args.ligand_file:
        ap.error("provide --ligand (SMILES) or --ligand-file")

    receptor = Path(args.receptor)
    if not receptor.is_file():
        print(f"error: receptor not found: {receptor}", file=sys.stderr)
        return 2

    work = Path(args.out)
    work.mkdir(parents=True, exist_ok=True)

    print("== preparing receptor", file=sys.stderr)
    rec_pdbqt = prepare_receptor(receptor, work)

    print("== preparing ligand", file=sys.stderr)
    if args.ligand_file:
        lig_pdbqt = Path(args.ligand_file)
    else:
        lig_pdbqt = prepare_ligand(args.ligand, work)

    center = args.center
    if args.auto_box and not center:
        print("== detecting binding site", file=sys.stderr)
        center = auto_box(receptor, work)
        if center is None:
            print("error: --auto-box found no ligand; pass --center instead",
                  file=sys.stderr)
            return 2
        print(f"   box center: {center}", file=sys.stderr)
    if not center:
        ap.error("provide --center x y z, or --auto-box")

    out_pdbqt = work / "docked.pdbqt"
    cmd = [
        VINA,
        "--receptor", str(rec_pdbqt),
        "--ligand", str(lig_pdbqt),
        "--center_x", str(center[0]),
        "--center_y", str(center[1]),
        "--center_z", str(center[2]),
        "--size_x", str(args.size[0]),
        "--size_y", str(args.size[1]),
        "--size_z", str(args.size[2]),
        "--out", str(out_pdbqt),
        "--num_modes", str(args.num_modes),
        "--exhaustiveness", str(args.exhaustiveness),
        "--energy_range", str(args.energy_range),
    ]

    print("== docking", file=sys.stderr)
    r = sh(cmd)
    log = work / "vina.log"
    log.write_text(r.stdout + r.stderr)

    if r.returncode != 0:
        print(f"error: vina failed\n{(r.stdout + r.stderr)[-500:]}",
              file=sys.stderr)
        return 1

    # Parse the mode table from the log
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
        "center": list(center),
        "size": args.size,
        "num_modes": len(modes),
        "best_affinity": modes[0]["affinity_kcal_mol"] if modes else None,
        "modes": modes,
        "output": str(out_pdbqt),
    }

    # Build a viewable complex: receptor PDB + best ligand pose as PDB.
    # The viewer renders cif/pdb only, so PDBQT is not enough.
    complex_pdb = work / "complex.pdb"
    try:
        pdbqt_to_pdb(out_pdbqt, work / "ligand_pose1.pdb", model=1)
        rec_pdb = work / "receptor.pdb"
        if rec_pdb.is_file():
            # A PDB parser stops at the first END, so the receptor's own
            # END must go and a single one be written after the ligand.
            rec_lines = [l for l in rec_pdb.read_text().splitlines()
                         if l.strip() != "END"]
            lig_lines = (work / "ligand_pose1.pdb").read_text().splitlines()
            complex_pdb.write_text(
                "\n".join(rec_lines + lig_lines) + "\nEND\n")
            summary["complex_pdb"] = str(complex_pdb)
    except Exception as e:
        print(f"warning: could not build complex.pdb ({e})", file=sys.stderr)

    (work / "summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
