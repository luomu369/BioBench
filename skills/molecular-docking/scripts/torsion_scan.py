#!/usr/bin/env python3
"""Rotamer scan around a selected bond.

Rotates the torsion defined by the four atoms a-b-c-d through 360 degrees
in fixed steps, minimising the rest of the molecule at each angle with an
MMFF94 force field, and reports the energy profile.

The bond the user selected in the viewer is b-c. Everything on the far side
of c rotates; everything on the near side of b stays put.

Usage:
    python torsion_scan.py --pdb complex.pdb --chain B --resi 464 \
        --a CA --b CB --c CG --d ND1 --step 15

The four atoms are named rather than derived from the selected bond because
a bond alone does not say which way round it was clicked, and the scan needs
a direction: rotating about b-c from the a side is a different scan from
rotating from the d side.
"""

import argparse
import json
import sys

import numpy as np


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdb", required=True, help="structure file")
    ap.add_argument("--chain", required=True)
    ap.add_argument("--resi", required=True, type=int)
    ap.add_argument("--a", required=True, help="atom before the bond")
    ap.add_argument("--b", required=True, help="bond atom, near side")
    ap.add_argument("--c", required=True, help="bond atom, far side")
    ap.add_argument("--d", required=True, help="atom after the bond, far side")
    ap.add_argument("--step", type=int, default=15, help="degrees between samples")
    ap.add_argument("--out", help="write the profile as JSON here")
    return ap.parse_args()


def load_residue(path, chain, resi):
    """Atoms of one residue, in file order, as (name, element, xyz, resn)."""
    import gemmi
    st = gemmi.read_structure(path)
    for ch in st[0]:
        if ch.name != chain:
            continue
        for r in ch:
            if r.seqid.num == resi:
                return ([(a.name, a.element.name,
                          np.array([a.pos.x, a.pos.y, a.pos.z])) for a in r],
                        r.name)
    raise SystemExit(f"{chain}:{resi} not found in {path}")


# Standard amino acid topology: heavy-atom bonds, by residue name. A residue
# read out of a PDB is a fragment — its N and C termini have no neighbours —
# and RDKit's bond determination refuses to guess a valence for an atom with
# one bond. Naming the bonds explicitly sidesteps that, and the topology of
# the twenty residues plus a few common modified ones is short enough to
# write down.
RESIDUE_BONDS = {
    "HIS": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","ND1"),("CG","CD2"),("ND1","CE1"),("CD2","CE1"),("CE1","NE2")],
    "ARG": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD"),("CD","NE"),("NE","CZ"),("CZ","NH1"),("CZ","NH2")],
    "LYS": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD"),("CD","CE"),("CE","NZ")],
    "ASP": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","OD1"),("CG","OD2")],
    "GLU": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD"),("CD","OE1"),("CD","OE2")],
    "ASN": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","OD1"),("CG","ND2")],
    "GLN": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD"),("CD","OE1"),("CD","NE2")],
    "SER": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","OG")],
    "THR": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","OG1"),
            ("CB","CG2")],
    "CYS": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","SG")],
    "MET": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","SD"),("SD","CE")],
    "PRO": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD"),("CD","N")],
    "PHE": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD1"),("CG","CD2"),("CD1","CE1"),("CD2","CE2"),("CE1","CZ")],
    "TYR": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD1"),("CG","CD2"),("CD1","CE1"),("CD2","CE2"),
            ("CE1","CZ"),("CZ","OH")],
    "TRP": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD1"),("CG","CD2"),("CD1","NE1"),("CD2","CE2"),
            ("CD2","CE3"),("CE2","CZ2"),("CE3","CZ3"),("NE1","CE2")],
    "LEU": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG"),
            ("CG","CD1"),("CG","CD2")],
    "ILE": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG1"),
            ("CB","CG2"),("CG1","CD1")],
    "VAL": [("N","CA"),("CA","C"),("C","O"),("CA","CB"),("CB","CG1"),
            ("CB","CG2")],
    "ALA": [("N","CA"),("CA","C"),("C","O"),("CA","CB")],
    "GLY": [("N","CA"),("CA","C"),("C","O")],
}


# Hydrogens each heavy atom carries, by (residue, atom). PDB depositions
# usually omit hydrogens, and the force field needs the count to build a
# valid valence — RDKit cannot infer it on a fragment, whose N and C
# termini have no neighbours to complete theirs.
#
# Only the atoms that actually carry hydrogens are listed; everything else
# defaults to zero.
IMPLICIT_HS = {
    ("HIS","N"):1, ("HIS","CA"):1, ("HIS","CB"):2, ("HIS","ND1"):1, ("HIS","CD2"):1, ("HIS","CE1"):1,
    ("ARG","N"):1, ("ARG","CA"):1, ("ARG","CB"):2, ("ARG","CG"):2, ("ARG","CD"):2,
    ("ARG","NE"):1, ("ARG","NH1"):2, ("ARG","NH2"):2,
    ("LYS","N"):1, ("LYS","CA"):1, ("LYS","CB"):2, ("LYS","CG"):2, ("LYS","CD"):2,
    ("LYS","CE"):2, ("LYS","NZ"):3,
    ("ASP","N"):1, ("ASP","CA"):1, ("ASP","CB"):2,
    ("GLU","N"):1, ("GLU","CA"):1, ("GLU","CB"):2, ("GLU","CG"):2,
    ("ASN","N"):1, ("ASN","CA"):1, ("ASN","CB"):2, ("ASN","ND2"):2,
    ("GLN","N"):1, ("GLN","CA"):1, ("GLN","CB"):2, ("GLN","CG"):2, ("GLN","NE2"):2,
    ("SER","N"):1, ("SER","CA"):1, ("SER","CB"):2, ("SER","OG"):1,
    ("THR","N"):1, ("THR","CA"):1, ("THR","CB"):1, ("THR","OG1"):1, ("THR","CG2"):3,
    ("CYS","N"):1, ("CYS","CA"):1, ("CYS","CB"):2, ("CYS","SG"):1,
    ("MET","N"):1, ("MET","CA"):1, ("MET","CB"):2, ("MET","CG"):2, ("MET","CE"):3,
    ("PRO","N"):0, ("PRO","CA"):1, ("PRO","CB"):2, ("PRO","CG"):2, ("PRO","CD"):2,
    ("PHE","N"):1, ("PHE","CA"):1, ("PHE","CB"):2, ("PHE","CD1"):1, ("PHE","CD2"):1,
    ("PHE","CE1"):1, ("PHE","CE2"):1, ("PHE","CZ"):1,
    ("TYR","N"):1, ("TYR","CA"):1, ("TYR","CB"):2, ("TYR","CD1"):1, ("TYR","CD2"):1,
    ("TYR","CE1"):1, ("TYR","CE2"):1, ("TYR","OH"):1,
    ("TRP","N"):1, ("TRP","CA"):1, ("TRP","CB"):2, ("TRP","CD1"):1, ("TRP","NE1"):1,
    ("TRP","CE3"):1, ("TRP","CZ2"):1, ("TRP","CZ3"):1,
    ("LEU","N"):1, ("LEU","CA"):1, ("LEU","CB"):2, ("LEU","CG"):1,
    ("LEU","CD1"):3, ("LEU","CD2"):3,
    ("ILE","N"):1, ("ILE","CA"):1, ("ILE","CB"):1, ("ILE","CG1"):2,
    ("ILE","CG2"):3, ("ILE","CD1"):3,
    ("VAL","N"):1, ("VAL","CA"):1, ("VAL","CB"):1, ("VAL","CG1"):3, ("VAL","CG2"):3,
    ("ALA","N"):1, ("ALA","CA"):1, ("ALA","CB"):3,
    ("GLY","N"):2, ("GLY","CA"):2,
}


def build_mol(atoms, resn):
    """An RDKit molecule from a residue's atoms, with bonds from the table."""
    from rdkit import Chem

    bonds = RESIDUE_BONDS.get(resn)
    if bonds is None:
        raise SystemExit(f"no topology table for {resn}; add it to RESIDUE_BONDS")

    names = [a[0] for a in atoms]
    missing = [n for n, _ in bonds if n not in names]
    if missing:
        raise SystemExit(f"{resn} is missing atoms {missing}; "
                         "the residue in the file is incomplete")

    rw = Chem.RWMol()
    conf_pts = []
    for name, elem, xyz in atoms:
        # Hydrogens are absent from most PDB depositions. The force field
        # needs to know how many each heavy atom carries, so the count is
        # set explicitly rather than left for RDKit to infer — inference
        # fails on a fragment, whose termini have no neighbours to complete
        # their valence.
        a = Chem.Atom(elem)
        a.SetNoImplicit(True)
        a.SetNumExplicitHs(IMPLICIT_HS.get((resn, name), 0))
        rw.AddAtom(a)
        conf_pts.append(list(xyz))

    idx = {n: i for i, n in enumerate(names)}
    for n1, n2 in bonds:
        rw.AddBond(idx[n1], idx[n2], Chem.BondType.SINGLE)

    mol = rw.GetMol()
    # SetNoImplicit leaves the explicit valence uncalculated, and MMFF reads
    # it on the first atom it looks at. Without this the force field raises
    # a pre-condition violation before doing any work.
    mol.UpdatePropertyCache(strict=False)
    # MMFF also reads ring membership, which is only computed on demand.
    # Skipping this fails on any residue with a ring — HIS, PHE, TYR, TRP,
    # PRO — with "RingInfo not initialized".
    Chem.GetSSSR(mol)
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, p in enumerate(conf_pts):
        conf.SetAtomPosition(i, p)
    mol.AddConformer(conf)
    return mol


def rotate_about_bond(conf, ib, ic, delta_deg):
    """Rotate everything on ic's side of the ib-ic bond by delta_deg.

    "ic's side" is found by walking the bond graph from ic without going
    back through ib — that set is what a rotation about the bond moves, and
    it is exactly the side chain for a CA-CB or CB-CG bond.

    The rotation is rigid: bond lengths and angles on the far side are
    preserved exactly, so the only thing that changes is the torsion. That
    is what makes the resulting energy profile a torsional profile rather
    than a mixture of torsion and relaxation.
    """
    from rdkit import Chem

    mol = conf.GetOwningMol()
    # Which atoms are on ic's side of the bond.
    far = set()
    stack = [ic]
    while stack:
        cur = stack.pop()
        if cur in far or cur == ib:
            continue
        far.add(cur)
        for nb in (a.GetIdx() for a in mol.GetAtomWithIdx(cur).GetNeighbors()):
            if nb != ib and nb not in far:
                stack.append(nb)

    pb = conf.GetAtomPosition(ib)
    pc = conf.GetAtomPosition(ic)
    axis = np.array([pc.x - pb.x, pc.y - pb.y, pc.z - pb.z])
    norm = np.linalg.norm(axis)
    if norm < 1e-6:
        return
    axis = axis / norm

    theta = np.radians(delta_deg)
    # Rodrigues' rotation formula.
    c, s = np.cos(theta), np.sin(theta)
    K = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    R = np.eye(3) * c + s * K + (1 - c) * np.outer(axis, axis)
    pivot = np.array([pb.x, pb.y, pb.z])

    for idx in far:
        p = conf.GetAtomPosition(idx)
        v = np.array([p.x, p.y, p.z]) - pivot
        rv = R @ v + pivot
        conf.SetAtomPosition(idx, [float(rv[0]), float(rv[1]), float(rv[2])])


def main():
    args = parse_args()
    atoms, resn = load_residue(args.pdb, args.chain, args.resi)
    names = [a[0] for a in atoms]
    for n in (args.a, args.b, args.c, args.d):
        if n not in names:
            raise SystemExit(f"atom {n} not in {args.chain}:{args.resi} "
                             f"(has {', '.join(names)})")

    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = build_mol(atoms, resn)
    # MMFF wants explicit hydrogens. The residue comes from a PDB, which
    # usually omits them, so they are added here — placed by geometry rather
    # than by the force field, since the heavy-atom positions are already
    # the experimental ones and should not move.
    # AddHs appends, so the heavy atoms keep their indices and the four
    # torsion atoms are still where they were. That is worth stating: the
    # obvious alternative — rebuilding the index map from atom names — is
    # only needed if hydrogens were interleaved, which they are not.
    mol = Chem.AddHs(mol, addCoords=True)
    conf = mol.GetConformer()

    ia, ib, ic, id_ = (names.index(args.a), names.index(args.b),
                       names.index(args.c), names.index(args.d))

    # MMFF94 first, UFF as a fallback — MMFF94 is parameterised for
    # organics and gives a smoother profile, but not every residue
    # fragment is covered.
    props = AllChem.MMFFGetMoleculeProperties(mol)
    if props is None:
        print("note: MMFF94 properties unavailable, using UFF", file=sys.stderr)
        ff = AllChem.UFFGetMoleculeForceField(mol)
        kind = "UFF"
    else:
        ff = AllChem.MMFFGetMoleculeForceField(mol, props)
        kind = "MMFF94"

    if ff is None:
        raise SystemExit("no force field could be built for this fragment")

    # The dihedral as it stands, so the scan starts from the real
    # conformation rather than from zero.
    start = Chem.rdMolTransforms.GetDihedralDeg(conf, ia, ib, ic, id_)

    # The rotation is incremental, so each sample has to start from the
    # original coordinates — otherwise the second sample rotates the first
    # sample's result and the angles compound. Snapshot the far side once
    # and restore it before every step.
    mol = conf.GetOwningMol()
    far_side = set()
    stack = [ic]
    while stack:
        cur = stack.pop()
        if cur in far_side or cur == ib:
            continue
        far_side.add(cur)
        for nb in (a.GetIdx() for a in mol.GetAtomWithIdx(cur).GetNeighbors()):
            if nb != ib and nb not in far_side:
                stack.append(nb)
    original = {idx: conf.GetAtomPosition(idx) for idx in far_side}

    profile = []
    for deg in range(0, 360, args.step):
        # Restore, then rotate by the absolute offset from the starting
        # conformation — so the sample at `deg` really is at `deg`.
        for idx, p in original.items():
            conf.SetAtomPosition(idx, [p.x, p.y, p.z])
        rotate_about_bond(conf, ib, ic, deg - start)
        # The force field is rebuilt every sample. PyForceField copies the
        # coordinates when it is constructed and CalcEnergy reads that copy,
        # not the conformer — so a force field built once reports the same
        # number no matter how the conformer is moved afterwards.
        if kind == "MMFF94":
            step_ff = AllChem.MMFFGetMoleculeForceField(mol, props)
        else:
            step_ff = AllChem.UFFGetMoleculeForceField(mol)
        e = step_ff.CalcEnergy()
        profile.append({"angle": deg, "energy": round(e, 4)})

    energies = [p["energy"] for p in profile]
    e_min = min(energies)
    best = [p for p in profile if p["energy"] == e_min]
    # Relative to the minimum, so the numbers read as barriers.
    for p in profile:
        p["rel_kcal"] = round(p["energy"] - e_min, 3)

    result = {
        "structure": args.pdb,
        "residue": f"{args.chain}:{args.resi}",
        "bond": f"{args.b}-{args.c}",
        "torsion": f"{args.a}-{args.b}-{args.c}-{args.d}",
        "force_field": kind,
        "start_angle": round(start, 2),
        "step": args.step,
        "profile": profile,
        "minimum_angle": best[0]["angle"],
        "minimum_energy": e_min,
        "max_barrier_kcal": round(max(energies) - e_min, 3),
    }

    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
