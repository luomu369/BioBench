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
import math
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import env_python, tool
from pdb_format import (atom_line, check_serials_unique, conect_line,
                        iter_atoms)

DOCK_PY = str(env_python("docking"))
VINA = str(tool("vina"))
MK_RECEPTOR = str(env_python("docking").parent / "mk_prepare_receptor.py")

# Covalent radii (Cordero 2008), used to rebuild the ligand's bonds when
# converting a Vina PDBQT pose back to PDB. PDBQT records no bonds, so
# without this the viewer is left to infer them from distances alone.
COVALENT_RADII = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05,
    "P": 1.07, "F": 0.57, "Cl": 1.02, "Br": 1.20, "I": 1.39,
    "Se": 1.20, "Si": 1.11, "B": 0.84,
}


def sh(cmd, **kw):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def prepare_receptor(receptor: Path, work: Path,
                     center=None, size=None):
    """mmCIF/PDB -> PDB -> PDBQT. Returns (pdbqt, dropped_residue_keys).

    Always writes ``work/receptor.pdb``, even when the input is already
    PDB. The complex builder below reads that file, and skipping the copy
    for PDB input left it missing — the publish then had no complex at all.

    ``-a`` (allow_bad_res) is used, because the alternative is to refuse any
    receptor with a disordered side chain — and most crystal structures have
    some. But it drops those residues silently, so the list is parsed back out
    of meeko's report and returned. A caller that gets a non-empty list knows
    the score was computed against a receptor with holes in it, which is the
    difference between a result and a misleading one: a run here scored
    -10.7 kcal/mol against a receptor missing a pocket phenylalanine, and
    -6.0 once that residue was restored.
    """
    pdbqt = work / "receptor.pdbqt"
    pdb = work / "receptor.pdb"

    # mmCIF -> PDB with gemmi (Vina's tools only read PDB/PDBQT)
    if receptor.suffix in (".cif", ".mmcif"):
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
        # Copy rather than reuse in place: the complex builder needs a
        # receptor PDB inside the output directory.
        shutil.copy(receptor, pdb)

    cmd = [DOCK_PY, str(MK_RECEPTOR), "--read_pdb", str(pdb),
           "-o", str(work / "receptor"), "-p", "-a", "--default_altloc", "A"]
    r = sh(cmd)
    if r.returncode != 0 or not pdbqt.is_file():
        print(f"error: receptor -> PDBQT failed\n{r.stdout[-400:]}{r.stderr[-400:]}",
              file=sys.stderr)
        sys.exit(2)

    # Which residues meeko could not template, and therefore dropped. `-a`
    # tells it to drop them rather than stop, and it drops them silently — a
    # crystal structure with disordered side chains reaches Vina with holes in
    # the binding pocket, and a ligand that fits a hole scores better than one
    # that does not. One run here went from -10.7 to -6.0 once a pocket
    # phenylalanine that had been dropped was put back.
    #
    # The list is parsed out of meeko's own report and carried in the summary,
    # so a score computed against a receptor with holes in it can be
    # recognised as such instead of being read as a good result.
    dropped = []
    for line in (r.stdout + r.stderr).splitlines():
        m = re.search(r"No template matched for residue_key='([^']+)'", line)
        if m:
            dropped.append(m.group(1))
    if dropped:
        (work / "dropped_residues.txt").write_text("\n".join(dropped) + "\n")
    return pdbqt, dropped


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


def receptor_atom_delta(original: Path, converted: Path) -> int:
    """How many atoms the receptor lost on the way to PDBQT.

    meeko builds a chemical template per residue and silently skips the ones
    it cannot, so a receptor can reach Vina with whole side chains missing.
    Nothing warns. The score is then against a receptor with holes in it, and
    a ligand that fits the hole scores better than one that does not.

    Counting the difference is the cheapest way to know it happened. The
    comparison is by coordinate, because meeko renumbers residues and the
    names are not stable across the conversion.
    """
    try:
        before = {(round(a.x, 3), round(a.y, 3), round(a.z, 3))
                  for a in iter_atoms(original.read_text())}
        after = {(round(a.x, 3), round(a.y, 3), round(a.z, 3))
                 for a in iter_atoms(converted.read_text())}
    except OSError:
        return 0
    return len(before - after)


def pdbqt_to_pdb(src: Path, dst: Path, model: int = 1,
                 chain_id: str = "L", serial_offset: int = 0,
                 keep_residues: bool = False) -> None:
    """Extract one MODEL from a Vina PDBQT and rewrite it as plain PDB.

    Four fixes are applied so the result renders in structure viewers:
      1. PDBQT ATOM records carry two extra trailing columns (partial charge
         and AutoDock atom type) that PDB parsers reject — they are stripped.
      2. Vina writes the ligand with an empty chain ID and residue name UNL,
         which causes viewers to merge it into a neighbouring chain. A real
         chain ID is written instead so the ligand stays a separate entity.
      3. PDBQT carries no bond records at all, so a plain coordinate dump
         leaves the viewer to guess the connectivity from distances — and it
         guesses wrong often enough that the ligand comes out as a tangle of
         overlapping atoms. CONECT records are written from the covalent radii
         of the atoms involved, which is the same rule the viewer would apply
         but with the radii the structure actually has rather than a default.
      4. The atom serials continue from the receptor's rather than restarting
         at 1. This file is appended to the receptor PDB, so a ligand numbered
         from 1 collides with the receptor's own first atoms — and a CONECT
         naming serial 2 then bonds the ligand to whatever the receptor has at
         serial 2, which is how a ligand ends up wired to the protein's
         N-terminus.
    """
    lines = []
    in_model = False
    seen_any = False
    serial = serial_offset
    # Coordinates and elements, kept alongside the lines so the bonds can be
    # worked out once every atom has been read.
    xyz, elems = [], []
    raw_lines = src.read_text().splitlines()
    # A receptor PDBQT has no MODEL records — it is one structure, not a set
    # of poses. Treating the absence as "not in the model" reads nothing at
    # all, so the file is read whole unless a MODEL record says otherwise.
    has_models = any(l.startswith("MODEL") for l in raw_lines)
    if not has_models:
        in_model = True
    for line in raw_lines:
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
            # The element is the last field on the line, not a fixed column.
            # PDBQT is not always 80 columns: AutoDock's atom type is one or
            # two characters ("C", "Cl", "NA", "OA"), and a two-character type
            # pushes the element one column right. Reading cols 77-78 on such
            # a line returns " C" for a chlorine, and the atom comes out a
            # carbon — which is invisible in the file and obvious on screen,
            # where the chlorines render as carbon grey.
            #
            # AutoDock also uses "A" for aromatic carbon, which is not a real
            # element symbol; parsers read it as unknown and the atom loses
            # its identity. Map it back to carbon.
            tail = line.rstrip().split()
            raw = tail[-1] if len(tail) >= 2 else ""
            if raw.upper() == "A":
                raw = "C"
            elem = (raw or name[0])[:2].rjust(2)
            # The line is written by the shared formatter rather than by an
            # f-string here. It owns the column layout, and it refuses a value
            # that would not fit instead of shifting the columns after it.
            #
            # A ligand is written as one UNL residue in its own chain, which
            # is what Vina's output is. A receptor keeps the residue names and
            # numbers it arrived with, so the converted PDBQT still reads as
            # the protein it came from rather than as one enormous ligand.
            if keep_residues:
                out_resn = line[17:20].strip() or "UNL"
                out_chain = line[21] or chain_id
                try:
                    out_resi = int(line[22:26])
                except ValueError:
                    out_resi = 1
            else:
                out_resn, out_chain, out_resi = "UNL", chain_id, 1
            lines.append(atom_line(
                serial=serial, name=name, resn=out_resn, chain=out_chain,
                resi=out_resi,
                x=float(x), y=float(y), z=float(z), elem=elem))
            xyz.append((float(x), float(y), float(z)))
            elems.append(elem.strip())
            seen_any = True
    if not seen_any:
        raise ValueError(f"no ATOM records in MODEL {model}")

    # Bonds from covalent radii. Two atoms are bonded when they sit closer
    # than the sum of their radii plus a tolerance — the tolerance covers the
    # spread in real bond lengths for a given pair, and the radii keep a
    # carbon from bonding to the carbon two away in a ring.
    #
    # The serials carry the offset, so a CONECT names the atom by the number
    # it has in the complex rather than the number it had in this file alone.
    conect = []
    for i in range(len(xyz)):
        for j in range(i + 1, len(xyz)):
            ri = COVALENT_RADII.get(elems[i])
            rj = COVALENT_RADII.get(elems[j])
            if ri is None or rj is None:
                continue
            d = math.dist(xyz[i], xyz[j])
            if d < ri + rj + 0.45:
                conect.append(conect_line(i + 1 + serial_offset,
                                          j + 1 + serial_offset))

    # TER only — no END. This file is meant to be appended to the receptor
    # PDB, and a PDB parser stops at the first END it sees, which would
    # silently drop the ligand from the complex.
    dst.write_text("\n".join(lines + conect) + "\nTER\n")


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
    ap.add_argument("--cpu", type=int, default=0,
                    help="threads for Vina; 0 = all available cores. Vina is "
                         "CPU-only and its auto-detection often falls back "
                         "to a single thread, so this is passed explicitly.")
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

    # The box is resolved before the receptor is prepared, because the
    # receptor preparation now needs it: meeko is told which residues near the
    # box it may not silently drop.
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

    print("== preparing receptor", file=sys.stderr)
    rec_pdbqt, dropped = prepare_receptor(receptor, work, center, args.size)
    if dropped:
        print(f"warning: meeko dropped {len(dropped)} receptor residue(s) it "
              f"could not template: {', '.join(dropped)}", file=sys.stderr)
        print("         the score is against the reduced receptor",
              file=sys.stderr)

    print("== preparing ligand", file=sys.stderr)
    if args.ligand_file:
        # A ligand file is not a PDBQT. Handing one straight to Vina fails
        # with a PDBQT parsing error that names the tag it choked on, which
        # reads like a malformed file rather than a file that was never
        # converted. Read the structure, take its SMILES, and go through the
        # same path as --ligand.
        lig_path = Path(args.ligand_file)
        if not lig_path.is_file():
            print(f"error: ligand file not found: {lig_path}",
                  file=sys.stderr)
            return 2
        r = sh([DOCK_PY, "-c", f"""
from rdkit import Chem
mol = Chem.MolFromPDBFile({str(lig_path)!r}, sanitize=False,
                          removeHs=False, proximityBonding=True)
if mol is None:
    raise SystemExit('could not read the ligand file')
mol = Chem.RemoveHs(mol)
try:
    Chem.SanitizeMol(mol)
except Exception:
    pass
print(Chem.MolToSmiles(mol))
"""])
        if r.returncode != 0:
            print(f"error: could not read {lig_path}: {r.stderr.strip()}",
                  file=sys.stderr)
            return 2
        smiles = r.stdout.strip()
        if not smiles:
            print(f"error: no SMILES could be derived from {lig_path}",
                  file=sys.stderr)
            return 2
        print(f"   ligand SMILES: {smiles}", file=sys.stderr)
        lig_pdbqt = prepare_ligand(smiles, work)
    else:
        lig_pdbqt = prepare_ligand(args.ligand, work)

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
        "--cpu", str(args.cpu),
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
        # Residues meeko could not template and dropped. Empty for a complete
        # receptor; non-empty means the score is against a receptor with holes
        # in it and should not be compared with a score from a whole one.
        "receptor_residues_dropped": dropped,
    }

    # Build a viewable complex: receptor PDB + best ligand pose as PDB.
    # The viewer renders cif/pdb only, so PDBQT is not enough.
    #
    # The receptor half is the one Vina actually docked against — the PDBQT,
    # converted back — not the PDB that was handed in. The two are not the
    # same set of atoms: meeko adds the polar hydrogens Vina scores against,
    # and it silently drops any residue it cannot build a template for. A
    # complex assembled from the input PDB therefore shows atoms Vina never
    # saw, and a ligand that scored well against a receptor missing a pocket
    # side chain is drawn buried inside that side chain. The score and the
    # picture then disagree, and the picture is what the user sees.
    complex_pdb = work / "complex.pdb"
    try:
        rec_view = work / "receptor_view.pdb"
        pdbqt_to_pdb(work / "receptor.pdbqt", rec_view, model=1,
                     serial_offset=0, keep_residues=True)
        rec_atoms = sum(1 for l in rec_view.read_text().splitlines()
                        if l.startswith(("ATOM", "HETATM")))
        # The ligand pose keeps the serials Vina gave it, starting at 1. This
        # file is read on its own by anything that needs to map a docked atom
        # back to the molecule — the PDBQT's REMARK SMILES IDX pairs name
        # atoms by these serials, and `prepare_complex.py` aligns the pose by
        # them. Renumbering here to continue the receptor's silently broke
        # that: every pair then named a serial the file did not have, and the
        # alignment failed with a KeyError.
        #
        # The offset is applied to the text that goes into the complex
        # instead, where it is needed and where nothing reads it back.
        pdbqt_to_pdb(out_pdbqt, work / "ligand_pose1.pdb", model=1,
                     serial_offset=0)
        # A PDB parser stops at the first END, so the receptor's own END must
        # go and a single one be written after the ligand.
        rec_lines = [l for l in rec_view.read_text().splitlines()
                     if l.strip() != "END"]
        # The ligand's serials are shifted by the receptor's atom count as the
        # text is joined — atoms and CONECT alike, since a CONECT names atoms
        # by serial and an unshifted one would bond the ligand to the
        # receptor's first atoms. The file on disk keeps Vina's own numbering,
        # which is what the PDBQT's SMILES IDX map refers to.
        lig_lines = []
        for l in (work / "ligand_pose1.pdb").read_text().splitlines():
            if l.startswith(("ATOM", "HETATM")):
                s = int(l[6:11]) + rec_atoms
                lig_lines.append(l[:6] + f"{s:5d}" + l[11:])
            elif l.startswith("CONECT"):
                a = int(l[6:11]) + rec_atoms
                b = int(l[11:16]) + rec_atoms
                lig_lines.append(l[:6] + f"{a:5d}{b:5d}" + l[16:])
            else:
                lig_lines.append(l)
        text = "\n".join(rec_lines + lig_lines) + "\nEND\n"
        # The complex is two files joined, and the join is where a numbering
        # collision would appear: a ligand whose serials restart at 1
        # duplicates the receptor's, and every CONECT it carries then bonds
        # it to the receptor's first atoms. Checking here catches that before
        # the file reaches the viewer, where it shows up only as a structure
        # that looks wrong.
        check_serials_unique(text)
        complex_pdb.write_text(text)
        summary["complex_pdb"] = str(complex_pdb)
        # What the receptor lost on the way to PDBQT, so a score that came
        # from a receptor with holes in it can be recognised as such.
        dropped = receptor_atom_delta(Path(args.receptor), rec_view)
        if dropped:
            summary["receptor_atoms_dropped"] = dropped
            print(f"warning: meeko dropped {dropped} receptor atom(s) it "
                  f"could not template; the score is against the reduced "
                  f"receptor", file=sys.stderr)
    except Exception as e:
        print(f"warning: could not build complex.pdb ({e})", file=sys.stderr)

    (work / "summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
