#!/usr/bin/env python3
"""Add a substituent to each selected atom, keeping everything else.

The user selects atoms and asks for a group to be added to them — a chlorine
on each of four ring carbons, say. This is not a replacement: the selected
atoms stay, and the new group is bonded to each of them.

The direction is the one thing that has to be worked out. A ring carbon with
two ring bonds has one free valence, and it points away from the ring along
the bisector of the two bonds reversed — the same place a hydrogen would go.
Taking that direction rather than an arbitrary one puts the substituent where
chemistry puts it, and keeps it clear of the ring's own atoms.

Usage:
  python3 add_substituent.py --structure complex.pdb --group chloro \
      --out runs/edited.pdb
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Geometry import Point3D

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import viewer_static
from pdb_format import (atom_line, check_serials_unique, conect_line,
                        iter_atoms)

VIEWER = viewer_static()

# Groups that can be added as a substituent, by the name a user would say.
# Atom 0 is the atom that bonds to the selected atom.
GROUPS = {
    "chloro": "[Cl]",
    "fluoro": "[F]",
    "bromo": "[Br]",
    "iodo": "[I]",
    "hydroxyl": "[OH]",
    "amino": "[NH2]",
    "methyl": "[CH3]",
    "nitro": "[N+](=O)[O-]",
    "cyano": "[C]#N",
    "carboxyl": "[C](=O)O",
    "methoxy": "[O]C",
    "sulfhydryl": "[SH]",
    "vinyl": "[CH]=[CH2]",
    "ethyl": "[CH2][CH3]",
    "ethynyl": "[C]#[CH]",
}

# Covalent radii (Cordero 2008), for the bond length to the selected atom.
COVALENT = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05,
    "P": 1.07, "F": 0.57, "Cl": 1.02, "Br": 1.20, "I": 1.39,
}

STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}


def read_selection():
    p = VIEWER / "selection.json"
    if not p.is_file():
        sys.exit("no selection.json — select atoms in the viewer first")
    d = json.loads(p.read_text())
    atoms = d.get("atoms") or []
    if not atoms:
        sys.exit("selection has no atoms — switch the viewer to Atom mode "
                 "and select the atoms to add to")
    return d, atoms


def resolve(structure: Path, sel: dict):
    """The PDB serials the viewer's selection refers to."""
    serials, names = [], []
    for atom in iter_atoms(structure.read_text()):
        serials.append(atom.serial)
        names.append((atom.name, atom.resn, atom.chain))

    out, used = [], set()
    for a in sel["atoms"]:
        s = a.get("serial")
        if s is not None and s in serials and s not in used:
            out.append(s)
            used.add(s)
            continue
        label = a.get("label", "")
        try:
            name, rest = label.split("—", 1)
            resn_chain = rest.rsplit("·", 1)
            resn = resn_chain[0].strip().rsplit(" ", 1)[0].strip()
            chain = resn_chain[1].strip()
            name = name.strip()
        except (ValueError, IndexError):
            continue
        for i, (n, r, c) in enumerate(names):
            if serials[i] in used:
                continue
            if n == name and r == resn and c == chain:
                out.append(serials[i])
                used.add(serials[i])
                break

    if not out:
        sys.exit("could not resolve the selection against the structure")
    return out


def build_mol(structure: Path):
    """The PDB as an RDKit mol with 3D coordinates and no sanitisation."""
    mol = Chem.MolFromPDBFile(str(structure), sanitize=False,
                              removeHs=False, proximityBonding=True)
    if mol is None:
        sys.exit(f"RDKit could not read {structure}")
    if not mol.GetConformer().Is3D():
        sys.exit("the structure has no 3D coordinates")
    return mol


def pos(conf, i):
    p = conf.GetAtomPosition(i)
    return np.array([p.x, p.y, p.z])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structure", required=True,
                    help="the PDB the selection was made on")
    ap.add_argument("--out", required=True, help="output PDB")
    ap.add_argument("--group", default="chloro", choices=sorted(GROUPS),
                    help="which group to add (default chloro)")
    args = ap.parse_args()

    smiles = GROUPS[args.group]
    structure = Path(args.structure)
    sel, _ = read_selection()
    serials = resolve(structure, sel)
    mol = build_mol(structure)
    conf = mol.GetConformer()

    by_serial = {}
    for a in mol.GetAtoms():
        info = a.GetPDBResidueInfo()
        if info is not None:
            by_serial[info.GetSerialNumber()] = a.GetIdx()

    targets = [by_serial[s] for s in serials if s in by_serial]
    if not targets:
        sys.exit("none of the selected atoms are in the molecule")
    print(f"adding {args.group} to {len(targets)} atom(s): "
          f"{', '.join(mol.GetAtomWithIdx(i).GetSymbol() for i in targets)}",
          file=sys.stderr)

    # The substituent's own geometry, from an MMFF optimisation of its SMILES.
    # A nitro is planar and a methyl is tetrahedral, and neither is a regular
    # anything — the template carries the real shape.
    template = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if template is None:
        sys.exit(f"could not parse the SMILES for {args.group}")
    if AllChem.EmbedMolecule(template, randomSeed=42) != 0:
        sys.exit(f"could not build a 3D conformer for {args.group}")
    AllChem.MMFFOptimizeMolecule(template, maxIters=500)
    heavy = [a.GetIdx() for a in template.GetAtoms()
             if a.GetSymbol() != "H"]
    conf_t = template.GetConformer()
    T = np.array([pos(conf_t, k) for k in heavy])
    t_centre = T.mean(0)
    # The template's frame: +x from the centre toward atom 0. A single-atom
    # group has no frame — its centre is atom 0 — and needs none.
    if len(heavy) > 1:
        ex = T[0] - t_centre
        ex = ex / np.linalg.norm(ex)
    else:
        ex = None

    # Where each substituent goes, and how it is oriented.
    #
    # The direction is the free valence of the selected atom: the bisector of
    # its bonds to everything else, reversed. For a ring carbon with two ring
    # bonds that is exactly where a hydrogen would sit, which is where a
    # substituent belongs. An arbitrary direction would put some of them
    # inside the ring.
    new_atoms = []   # (element, position) in the order they are written
    for i in targets:
        pi = pos(conf, i)
        nbrs = [pos(conf, n.GetIdx())
                for n in mol.GetAtomWithIdx(i).GetNeighbors()
                if n.GetSymbol() != "H"]
        if nbrs:
            away = np.mean([pi - n for n in nbrs], axis=0)
            nd = np.linalg.norm(away)
            direction = away / nd if nd > 1e-6 else None
        else:
            direction = None
        if direction is None:
            # No heavy neighbours to bisect — fall back to any axis.
            direction = np.array([0.0, 0.0, 1.0])

        # The bond length from the two covalent radii, so a C-Cl is 1.78 A
        # and a C-F 1.33 A rather than one number for every pair.
        a_elem = mol.GetAtomWithIdx(i).GetSymbol()
        g_elem = template.GetAtomWithIdx(heavy[0]).GetSymbol()
        bond_len = COVALENT.get(a_elem, 0.76) + COVALENT.get(g_elem, 0.76)

        # The group's atom 0 sits one bond length out along the free valence.
        origin = pi + bond_len * direction
        # The rest of the group follows, expressed in the target frame: its
        # own +x points back at the selected atom, and its plane is left as
        # the template had it. A single-atom group has nothing more to place.
        for j, k in enumerate(heavy):
            if j == 0:
                new_atoms.append((template.GetAtomWithIdx(k).GetSymbol(),
                                  origin))
                continue
            if ex is None:
                new_atoms.append((template.GetAtomWithIdx(k).GetSymbol(),
                                  origin))
                continue
            rel = T[j] - t_centre
            # Component along the bond direction, plus the perpendicular part
            # rotated into a frame whose +x is back toward the selected atom.
            along = np.dot(rel, ex) * (-direction)
            perp = rel - np.dot(rel, ex) * ex
            new_atoms.append((template.GetAtomWithIdx(k).GetSymbol(),
                              origin + along + perp))

    # Write the PDB: every original atom, then the new ones, with CONECT for
    # the new bonds and the group's own internal bonds.
    lines = []
    serial = 0
    orig_serial_of = {}
    for a in mol.GetAtoms():
        idx = a.GetIdx()
        info = a.GetPDBResidueInfo()
        p = pos(conf, idx)
        serial += 1
        orig_serial_of[idx] = serial
        name = (info.GetName().strip() if info else a.GetSymbol())
        resn = info.GetResidueName() if info else "UNL"
        chain = info.GetChainId() if info else "L"
        resi = info.GetResidueNumber() if info else 1
        record = "ATOM" if resn in STANDARD_AA else "HETATM"
        lines.append(atom_line(serial=serial, name=name, resn=resn,
                               chain=chain, resi=resi, x=p[0], y=p[1], z=p[2],
                               elem=a.GetSymbol(), record=record))

    # The new atoms continue the numbering, and inherit the residue of the
    # atom they are attached to — a substituent on a ligand is part of that
    # ligand, not a separate entity the viewer would float on its own.
    inherit = None
    for i in targets:
        info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
        if info is not None:
            inherit = (info.GetChainId(), info.GetResidueName(),
                       info.GetResidueNumber())
            break
    if inherit is None:
        inherit = ("L", "UNL", 1)

    new_serial = {}
    per_target = len(heavy)
    for n, (elem, p) in enumerate(new_atoms):
        serial += 1
        new_serial[n] = serial
        same = [m for m in range(n)
                if new_atoms[m][0] == elem]
        name = f"{elem}{len(same) + 1}"
        lines.append(atom_line(serial=serial, name=name, resn=inherit[1],
                               chain=inherit[0], resi=inherit[2],
                               x=p[0], y=p[1], z=p[2], elem=elem,
                               record="HETATM"))

    # CONECT: the original molecule's bonds, plus each new group's bond to its
    # target and its own internal bonds.
    conect = [conect_line(orig_serial_of[b.GetBeginAtomIdx()],
                          orig_serial_of[b.GetEndAtomIdx()])
              for b in mol.GetBonds()]
    for t, i in enumerate(targets):
        base = t * per_target
        # target -> group atom 0
        conect.append(conect_line(orig_serial_of[i], new_serial[base]))
        # the group's own bonds, from the template
        for b in template.GetBonds():
            bi, bj = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
            if bi in heavy and bj in heavy:
                conect.append(conect_line(new_serial[base + heavy.index(bi)],
                                          new_serial[base + heavy.index(bj)]))

    text = "\n".join(lines + conect) + "\nEND\n"
    check_serials_unique(text)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}: {mol.GetNumAtoms() + len(new_atoms)} atoms, "
          f"{args.group} added to {len(targets)} atom(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
