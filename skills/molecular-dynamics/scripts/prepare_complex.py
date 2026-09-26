#!/usr/bin/env python3
"""Prepare a protein-ligand complex for molecular dynamics.

Takes a receptor structure and a docked ligand, and produces a single PDB
ready for simulate_complex.py: renumbered protein with hydrogens and a
C-terminal OXT, plus the ligand placed at its docked pose with bonds.

The three things this fixes, all of which silently break a simulation:

  * PDB numbering — archives often start below 1 or have gaps, and OpenMM's
    template matching walks the chain expecting contiguous residues.
  * Missing OXT — amber14's C-terminal template requires it; without it
    createSystem refuses the last residue.
  * Ligand bonds — a PDB carries no CONECT for a non-standard residue, so
    OpenMM sees isolated atoms and reports "no bonds between its atoms".

Usage:
  python3 prepare_complex.py --receptor 6HDX.cif --chain A \
      --ligand-pdbqt docking/mol_14/ligand.pdbqt \
      --ligand-pose docking/mol_14/ligand_pose1.pdb \
      --ligand-smiles "Cc1ccc(O)cc1" --out complex.pdb

Exit codes: 0 ok, 1 preparation failed, 2 bad input.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import env_python

# gemmi and rdkit live in the docking env; this wrapper stays wherever the
# calling skill is and shells out for the structure work.
DOCKING_PY = str(env_python("docking"))
OPENMM_PY = str(env_python("openmm"))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--receptor", required=True,
                    help="receptor structure (.cif or .pdb)")
    ap.add_argument("--chain", required=True,
                    help="receptor chain(s) to keep; comma-separated for a "
                         "multimer, e.g. A,B")
    ap.add_argument("--ligand-pdbqt", required=True,
                    help="the ligand's PDBQT — carries the SMILES IDX map")
    ap.add_argument("--ligand-pose", required=True,
                    help="the docked pose as PDB (Vina's output)")
    ap.add_argument("--ligand-smiles", required=True,
                    help="the ligand's SMILES, for topology and hydrogens")
    ap.add_argument("--ligand-resname", default="UNL",
                    help="residue name to give the ligand")
    ap.add_argument("--out", required=True, help="output PDB path")
    args = ap.parse_args()

    for p in (args.receptor, args.ligand_pdbqt, args.ligand_pose):
        if not Path(p).is_file():
            print(f"error: {p} not found", file=sys.stderr)
            return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # The steps below run under `python -c`, where __file__ does not exist and
    # a path cannot be derived from it. The shared directory is handed over in
    # the environment instead, so no step carries a literal path and a clone
    # anywhere still finds the modules.
    child_env = dict(os.environ)
    child_env["BIOBENCH_SHARED"] = str(Path(__file__).resolve().parents[2]
                                       / "_shared")

    # Step 1a: renumber the receptor with gemmi (docking env).
    raw = out.with_suffix(".raw.pdb")
    r = subprocess.run([DOCKING_PY, "-c", RENUMBER_STEP,
                        "--receptor", args.receptor,
                        "--chain", args.chain,
                        "--out", str(raw)], env=child_env)
    if r.returncode != 0 or not raw.is_file():
        print("error: receptor extraction failed", file=sys.stderr)
        return 1

    # Step 1b: add hydrogens and the terminal OXT with PDBFixer (openmm env).
    protein_h = out.with_suffix(".protein_h.pdb")
    r = subprocess.run([OPENMM_PY, "-c", HYDROGEN_STEP,
                        "--pdb", str(raw),
                        "--out", str(protein_h)], env=child_env)
    if r.returncode != 0 or not protein_h.is_file():
        print("error: hydrogen/OXT preparation failed", file=sys.stderr)
        return 1
    print(f"protein prepared: {protein_h}")

    # Step 2: place the ligand at its docked pose, on the SMILES topology.
    ligand_sdf = out.with_suffix(".ligand.sdf")
    r = subprocess.run([DOCKING_PY, "-c", LIGAND_STEP,
                        "--pdbqt", args.ligand_pdbqt,
                        "--pose", args.ligand_pose,
                        "--smiles", args.ligand_smiles,
                        "--out", str(ligand_sdf)], env=child_env)
    if r.returncode != 0 or not ligand_sdf.is_file():
        print("error: ligand alignment failed", file=sys.stderr)
        return 1
    print(f"ligand aligned: {ligand_sdf}")

    # Step 3: merge into one PDB, with CONECT records for the ligand.
    r = subprocess.run([DOCKING_PY, "-c", MERGE_STEP,
                        "--protein", str(protein_h),
                        "--ligand", str(ligand_sdf),
                        "--resname", args.ligand_resname,
                        "--out", str(out)], env=child_env)
    if r.returncode != 0:
        print("error: merge failed", file=sys.stderr)
        return 1

    n_atoms = sum(1 for l in out.read_text().splitlines()
                  if l.startswith(("ATOM", "HETATM")))
    n_conect = sum(1 for l in out.read_text().splitlines()
                   if l.startswith("CONECT"))
    print(f"wrote {out}: {n_atoms} atoms, {n_conect} ligand bonds")
    return 0


# ---- step 1a: renumber (gemmi, docking env) -----------------------------
RENUMBER_STEP = r'''
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--receptor"); ap.add_argument("--chain"); ap.add_argument("--out")
a = ap.parse_args()

import gemmi

AA = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR "
         "TRP TYR VAL MSE".split())
st = gemmi.read_structure(a.receptor)
st.setup_entities()

# One chain or several, comma-separated. A homodimer's active site sits at the
# interface of both chains, so simulating one of them puts the ligand in a
# pocket that no longer exists — HIV-1 protease is the case that forced this.
chains = [c.strip() for c in a.chain.split(",") if c.strip()]
if not chains:
    raise SystemExit("--chain names at least one chain")

# Renumber 1..N per chain. Archives start below 1 or skip numbers, and OpenMM
# walks the chain expecting contiguity.
new = gemmi.Structure(); new.spacegroup_hm = "P 1"; new.cell = st.cell
m = gemmi.Model("1")
total = 0
for ch in chains:
    res = [r for r in st[0][ch] if r.name in AA]
    if not res:
        raise SystemExit(f"no amino acids on chain {ch}")
    m.add_chain(gemmi.Chain(ch))
    for i, r in enumerate(res, 1):
        r2 = gemmi.Residue(); r2.name = r.name
        r2.seqid = gemmi.SeqId(i, " "); r2.het_flag = "A"
        for at in r: r2.add_atom(at)
        m[ch].add_residue(r2)
    total += len(res)
    print(f"  chain {ch}: {len(res)} residues renumbered 1..{len(res)}")
new.add_model(m); new.setup_entities()
new.write_pdb(a.out)
print(f"  {total} residues total across {len(chains)} chain(s)")
'''

# ---- step 1b: hydrogens + OXT (openmm env) ------------------------------
HYDROGEN_STEP = r'''
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--pdb"); ap.add_argument("--out")
a = ap.parse_args()

from pdbfixer import PDBFixer
from openmm.app import PDBFile, Modeller, ForceField

# PDBFixer adds the C-terminal OXT and any other missing heavy atoms —
# amber14's C-terminal template requires the OXT and refuses the last
# residue without it. Modeller.addHydrogens adds the rest.
fixer = PDBFixer(filename=a.pdb)
fixer.findMissingResidues(); fixer.findMissingAtoms(); fixer.addMissingAtoms()
ff = ForceField("amber14/protein.ff14SB.xml", "amber14/tip3pfb.xml")
mod = Modeller(fixer.topology, fixer.positions)
mod.addHydrogens(ff)
PDBFile.writeFile(mod.topology, mod.positions, open(a.out, "w"))
print(f"  {mod.topology.getNumAtoms()} atoms with hydrogens")
'''

# ---- step 2: ligand -----------------------------------------------------
LIGAND_STEP = r'''
import argparse
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--pdbqt"); ap.add_argument("--pose")
ap.add_argument("--smiles"); ap.add_argument("--out")
a = ap.parse_args()

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

# The shared PDB column layout, so the docked pose's serials and coordinates
# are read with the same field boundaries every other skill writes with.
# The directory arrives in BIOBENCH_SHARED because this block runs under
# `python -c`, where __file__ does not exist and the path cannot be derived
# from it.
sys.path.insert(0, __import__("os").environ["BIOBENCH_SHARED"])
from pdb_format import iter_atoms

# The PDBQT's REMARK SMILES IDX lines hold (smiles_idx, pdbqt_idx) pairs —
# the only record of which docked atom is which atom of the molecule. Read
# as a flat list they give a wrong mapping and a 6 A misalignment.
nums = []
for line in open(a.pdbqt):
    if line.startswith("REMARK SMILES IDX"):
        nums += [int(x) for x in line.split()[3:]]
pairs = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
if not pairs:
    raise SystemExit("no SMILES IDX map in the PDBQT")

# Docked coordinates, keyed by the PDBQT serial. The serial and coordinate
# columns are in the same places as in a PDB, so the shared parser reads them;
# the trailing charge and AutoDock type columns it ignores.
coords = {}
for atom in iter_atoms(Path(a.pose).read_text()):
    coords[atom.serial] = [atom.x, atom.y, atom.z]

# Build the molecule from SMILES so the topology and hydrogens are right —
# the Vina PDB is only partly hydrogenated and its atom names are not
# unique, so neither can be taken from it.
m = Chem.AddHs(Chem.MolFromSmiles(a.smiles))
AllChem.EmbedMolecule(m, randomSeed=42)
AllChem.MMFFOptimizeMolecule(m)
conf = m.GetConformer()

P = np.array([list(conf.GetAtomPosition(s - 1)) for s, _ in pairs])
Q = np.array([coords[p] for _, p in pairs])

def kabsch(A, B):
    ac, bc = A.mean(0), B.mean(0)
    H = (A - ac).T @ (B - bc)
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    return Vt.T @ np.diag([1, 1, d]) @ U.T, ac, bc

R, ac, bc = kabsch(P, Q)
allpos = np.array([list(conf.GetAtomPosition(i))
                   for i in range(m.GetNumAtoms())])
newpos = (allpos - ac) @ R.T + bc
for i in range(m.GetNumAtoms()):
    conf.SetAtomPosition(i, newpos[i].tolist())

rmsd = float(np.sqrt(((newpos[[s - 1 for s, _ in pairs]] - Q) ** 2).sum(1).mean()))
print(f"  aligned {len(pairs)} heavy atoms, RMSD {rmsd:.2f} A")
if rmsd > 3.0:
    print("  warning: RMSD above 3 A — the mapping may be wrong",
          file=sys.stderr)

w = Chem.SDWriter(a.out)
w.write(m)
'''

# ---- step 3: merge -----------------------------------------------------
MERGE_STEP = r'''
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--protein"); ap.add_argument("--ligand")
ap.add_argument("--resname"); ap.add_argument("--out")
a = ap.parse_args()

import gemmi
from rdkit import Chem

prot = gemmi.read_structure(a.protein)
prot.setup_entities()
supp = Chem.SDMolSupplier(a.ligand, removeHs=False)
lig = supp[0]
if lig is None:
    raise SystemExit("could not read the ligand SDF")
conf = lig.GetConformer()

new = gemmi.Structure(); new.spacegroup_hm = "P 1"
new.cell = gemmi.UnitCell(90, 90, 90, 90, 90, 90)
m = gemmi.Model("1")
m.add_chain(gemmi.Chain("A"))
for r in prot[0]["A"]:
    m["A"].add_residue(r)

m.add_chain(gemmi.Chain("L"))
r2 = gemmi.Residue(); r2.name = a.resname
r2.seqid = gemmi.SeqId(1, " "); r2.het_flag = "H"
counts = {}
for at in lig.GetAtoms():
    el = at.GetSymbol()
    counts[el] = counts.get(el, 0) + 1
    p = conf.GetAtomPosition(at.GetIdx())
    g = gemmi.Atom()
    g.name = f"{el}{counts[el]}"          # unique names; Vina's are all "C"
    g.element = gemmi.Element(el)
    g.pos = gemmi.Position(p.x, p.y, p.z)
    g.occ = 1.0; g.b_iso = 0.0
    r2.add_atom(g)
m["L"].add_residue(r2)
new.add_model(m); new.setup_entities()
new.write_pdb(a.out)

# gemmi writes no CONECT, so OpenMM sees the ligand as isolated atoms.
# Add them from the RDKit bond list.
lines = open(a.out).read().splitlines()
lig_start = None
for l in lines:
    if l.startswith("HETATM") and a.resname in l:
        lig_start = int(l[7:11]); break
conect = [f"CONECT{lig_start + b.GetBeginAtomIdx():5d}"
          f"{lig_start + b.GetEndAtomIdx():5d}"
          for b in lig.GetBonds()]
out = []
for l in lines:
    if l.startswith("END"):
        out += conect
    out.append(l)
open(a.out, "w").write("\n".join(out) + "\n")
print(f"  merged: {len(m['A'])} residues + ligand, {len(conect)} bonds")
'''


if __name__ == "__main__":
    sys.exit(main())
