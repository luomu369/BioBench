#!/usr/bin/env python3
"""Replace a selected group of atoms with another group, using RDKit.

The user selects atoms in the viewer and asks for a group — a benzene, a
thiazole, a vinyl. The selected atoms are removed from the molecule and the
new group is bonded in their place.

The group is placed by RDKit's own alignment: a reference is built from the
group's own MMFF geometry, expressed in a target frame derived from the bond
the deletion broke, and `GetAlignmentTransform` computes the rigid transform
that puts every heavy atom of the group onto it. `TransformMol` applies it. No
centroid is guessed, no rotation is searched — the transform is solved.

With `--delete` the selected atoms are simply removed, and the atoms they
leave behind are given the hydrogens their valence now lacks. That second
part is not optional: taking three carbons out of a ring turns the two that
remain into a chain, and a chain carbon needs hydrogens the ring carbon did
not. Publishing the removal without them leaves a fragment that draws fine
and reads as a fragment everywhere else.

Usage:
  python3 replace_with_group.py --structure complex.pdb --out edited.pdb \
      --group benzene
  python3 replace_with_group.py --structure complex.pdb --out edited.pdb \
      --delete
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Geometry import Point3D

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from pdb_format import (PdbFormatError, atom_line, check_serials_unique,
                        conect_line, iter_atoms)
from selection import build_mol, pos, read_selection, resolve

# Groups the skill can build, by the name a user would say. Each is the SMILES
# of the unsubstituted group; atom 0 is the substituent position, where the
# bond to the rest of the molecule is made.
#
# Rings and chains both go through the same path. The only thing that differs
# is how many hydrogens the template carries, and that is read off the
# template rather than assumed — a vinyl's terminal carbon needs two, its
# middle carbon one, a benzene's carbons one each.
GROUPS = {
    "benzene": "c1ccccc1",
    "thiazole": "c1nccs1",
    "pyridine": "c1ccncc1",
    "imidazole": "c1c[nH]cn1",
    "furan": "c1ccoc1",
    "thiophene": "c1ccsc1",
    "pyrrole": "c1cc[nH]c1",
    "oxazole": "c1ccoc1",
    "isoxazole": "c1ccoc1",
    "vinyl": "C=C",
    "ethyl": "CC",
    "ethynyl": "C#C",
    "hydroxyl": "O",
    "amino": "N",
    "methyl": "C",
    "nitro": "[N+](=O)[O-]",
    "carboxyl": "C(=O)O",
    "acetyl": "C(=O)C",
    "cyano": "C#N",
    "aldehyde": "C=O",
    "methoxy": "OC",
    "sulfhydryl": "S",
    # Bracketed, because a bare two-letter element in SMILES is read as an
    # aliphatic carbon with that many hydrogens implied — "Cl" parses as a
    # carbon bearing one hydrogen, and AddHs then builds HCl.
    "chloro": "[Cl]",
    "fluoro": "[F]",
    "bromo": "[Br]",
    "iodo": "[I]",
}
# Aromatic C-C, used for the reference bond and the group's own C-H.
BOND = 1.39
CH = 1.09

# The twenty standard amino acids. A residue with one of these names is
# written as ATOM rather than HETATM, because that is the record type every
# downstream tool uses to tell protein from ligand — meeko's receptor
# preparer among them, which templates ATOM residues as protein and silently
# drops the HETATM ones it cannot recognise as a ligand.
STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}

# Covalent radii (Cordero 2008), used to work out how long the bond between the
# attachment and the new group's first atom should be. The template cannot say:
# a vinyl's atom 0 is bonded only to its other carbon, and that length has
# nothing to do with the bond to whatever it is being attached to.
COVALENT = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05,
    "P": 1.07, "F": 0.57, "Cl": 1.02, "Br": 1.20, "I": 1.39,
    "Se": 1.20, "Si": 1.11, "B": 0.84,
}


def to_pdb(structure: Path) -> Path:
    """The structure as a PDB, converting from CIF when that is what it is.

    The serial is carried through unchanged. It is the only join between the
    viewer's selection and the file, and every other writer in this repo
    renumbers as it goes: gemmi's PDB writer assigns its own ordinal and drops
    the serial it was handed, so a CIF's atom 11612 comes back as a different
    atom and the edit lands on whatever now holds that number.

    Writing through `pdb_format.atom_line` is what prevents it. That module
    refuses a field that would not fit its columns instead of shifting the
    ones after it, so a serial that is too wide is an error rather than a
    silent renumber.

    A PDB is returned as it is. The one thing that is checked is that its
    serials are unique, because a file with two atoms sharing a serial makes
    the selection resolve to one of them arbitrarily.
    """
    if structure.suffix.lower() in (".pdb", ".ent"):
        try:
            check_serials_unique(structure.read_text(errors="replace"))
        except PdbFormatError as e:
            sys.exit(f"{structure}: {e}")
        return structure

    try:
        import gemmi
    except ImportError:
        sys.exit("gemmi is required to read a .cif — use the docking env")

    st = gemmi.read_structure(str(structure))
    st.setup_entities()

    # PDB chain IDs are one character, and a CIF from the viewer routinely has
    # more chains than that — a trimer with its ligands is ra..rj plus the
    # protein's own A..F. Truncating to the first character folds them all
    # together: ten ligands become one chain of 205 atoms, the residues merge,
    # and the edit is applied to a molecule that is not the one on screen.
    #
    # So each chain is given a unique single character, and the mapping is
    # written beside the file for whoever needs to read the result back. The
    # first pass keeps the chains that already fit; the rest are assigned from
    # the characters still free, in the order they appear.
    used = set()
    for model in st:
        for ch in model:
            name = (ch.name or "").strip()
            if len(name) == 1:
                used.add(name)
        break
    free = [c for c in "abcdefghijklmnopqrstuvwxyz0123456789"
            if c not in used]
    chain_map = {}
    for model in st:
        for ch in model:
            name = (ch.name or "").strip()
            if name in chain_map:
                continue
            if len(name) == 1:
                chain_map[name] = name
            elif free:
                chain_map[name] = free.pop(0)
            else:
                sys.exit(f"too many chains to write as PDB: {name} has no "
                         f"single-character id left")
        break

    # Every atom first, then the numbering. The serial written is the atom's
    # own — the selection the viewer recorded is resolved against the file as
    # it stands (for a CIF, to the atom's position, whose id is what every
    # other reader reports), and the PDB written here has to mean the same
    # atom by that number. Renumbering from one only coincides with it when
    # the file's ids happen to be 1..N, which these files are and a file from
    # elsewhere need not be. An atom with no serial of its own falls back to
    # a running count, and the duplicate check below catches the collision
    # that a mixed file would make.
    flat = []
    for model in st:
        for ch in model:
            pdb_chain = chain_map[(ch.name or "").strip()]
            for res in ch:
                resn = (res.name or "UNL")[:3]
                record = "ATOM" if resn in STANDARD_AA else "HETATM"
                for a in res:
                    flat.append((a, pdb_chain, resn, record,
                                 res.seqid.num, (res.seqid.icode or " ")[:1]))
        break  # one model; the viewer shows one
    if not flat:
        sys.exit(f"no atoms read from {structure}")

    out, n = [], 0
    for a, pdb_chain, resn, record, resi, icode in flat:
        n += 1
        serial = a.serial if a.serial else n
        out.append(atom_line(
            serial=serial,
            name=a.name,
            resn=resn,
            chain=pdb_chain,
            resi=resi,
            x=a.pos.x, y=a.pos.y, z=a.pos.z,
            elem=a.element.name,
            record=record,
            icode=icode,
        ))

    dest = structure.with_suffix(".edit.pdb")
    dest.write_text("\n".join(out) + "\n")
    # The mapping, for the caller that has to put the result back. Written
    # beside the file rather than printed: it is data, and a line in a log is
    # data the next step has to scrape.
    structure.with_suffix(".edit.chains.json").write_text(
        json.dumps(chain_map, indent=2))
    return dest


def selected_residues(structure: Path, serials) -> set:
    """The (chain, resn, resi) of every residue the selection touches.

    Used to bound proximity bonding: the edit needs the bonds inside the
    residue being edited and nothing else, and bonding a whole complex is
    quadratic in a way that does not finish.
    """
    want = set(serials)
    out = set()
    for atom in iter_atoms(structure.read_text(errors="replace")):
        if atom.serial in want:
            out.add((atom.chain.strip(), atom.resn, atom.resi))
    return out


def complete_hydrogens(edited, sel_res):
    """Give the edited residue the hydrogens the removal took away.

    Removing atoms does not only break the bonds of the atoms they were bonded
    to. Taking three carbons out of a ring turns the two that remain into a
    chain, and a chain carbon needs hydrogens the ring carbon did not: the
    middle one, which lost no bond at all, is short one as well. So the test
    is not "the neighbours of the removed atoms" but "the atoms of the
    affected residue whose valence is incomplete" — which is every atom of it
    with a non-zero implicit hydrogen count after the removal.

    A structure published without those hydrogens is not a molecule. It draws
    fine, and every tool that reads it downstream sees a fragment: a carbon
    with two bonds and no charge is a carbene to RDKit and a hole to a force
    field.

    The counts come from sanitisation, and sanitisation is what makes them
    right: a PDB read with proximityBonding has every bond single, so an
    aromatic carbon with two ring bonds looks like it wants two hydrogens,
    while sanitised it wants none. Without this the hydrogens would land on
    every ring carbon of the ligand.

    Only the selected residue is hydrogenated. Sanitising the whole complex
    and filling every non-zero count would put hydrogens on every unbonded
    protein atom — the rest of the file arrives with no bonds at all, so a
    bare carbon reads as methane.

    Returns (mol, {new atom index: host atom index}).
    """
    try:
        Chem.SanitizeMol(edited)
    except Exception as e:
        print(f"warning: could not sanitise the edited molecule ({e}); "
              f"hydrogen counts fall back to element valences",
              file=sys.stderr)
        edited.UpdatePropertyCache(strict=False)
    conf = edited.GetConformer()

    def xyz(i):
        p = conf.GetAtomPosition(i)
        return np.array([p.x, p.y, p.z])

    rw = Chem.RWMol(edited)
    added = {}
    for a in list(rw.GetAtoms()):
        info = a.GetPDBResidueInfo()
        if info is None:
            continue
        key = (info.GetChainId().strip(), info.GetResidueName(),
               info.GetResidueNumber())
        if key not in sel_res:
            continue
        need = max(0, a.GetNumImplicitHs())
        if need <= 0:
            continue
        h = a.GetIdx()
        p = xyz(h)
        # The bonds the atom still has, as unit vectors away from it.
        dirs = []
        for nb in a.GetNeighbors():
            v = xyz(nb.GetIdx()) - p
            n = float(np.linalg.norm(v))
            if n > 1e-6:
                dirs.append(v / n)
        if dirs:
            away = -np.mean(dirs, axis=0)
            away = away / float(np.linalg.norm(away))
        else:
            away = np.array([1.0, 0.0, 0.0])
        # Where the new hydrogens go: one along the bisector pointing away
        # from the neighbours, two symmetric about it in the plane the
        # neighbours define, more spread on a cone around it. A real C-H is
        # 1.09 A; the angle is the tetrahedral complement, not an optimisation.
        if need == 1 or len(dirs) < 2:
            newdirs = [away]
        elif need == 2:
            nrm = np.cross(dirs[0], dirs[1])
            if float(np.linalg.norm(nrm)) < 1e-3:
                arb = (np.array([1.0, 0.0, 0.0]) if abs(away[0]) < 0.9
                       else np.array([0.0, 1.0, 0.0]))
                nrm = np.cross(away, arb)
            nrm = nrm / float(np.linalg.norm(nrm))
            th = np.radians(55.0)
            newdirs = [np.cos(th) * away + np.sin(th) * nrm,
                       np.cos(th) * away - np.sin(th) * nrm]
        else:
            e1 = (np.array([1.0, 0.0, 0.0]) if abs(away[0]) < 0.9
                  else np.array([0.0, 1.0, 0.0]))
            e1 = e1 - np.dot(e1, away) * away
            e1 = e1 / float(np.linalg.norm(e1))
            e2 = np.cross(away, e1)
            th = np.radians(70.0)
            newdirs = [np.cos(th) * away
                       + np.sin(th) * (np.cos(2 * np.pi * k / need) * e1
                                       + np.sin(2 * np.pi * k / need) * e2)
                       for k in range(need)]
        for k in range(need):
            d = newdirs[k] / float(np.linalg.norm(newdirs[k]))
            idx = rw.AddAtom(Chem.Atom(1))
            q = p + 1.09 * d
            added[idx] = h
            # The position is kept aside: a conformer does not grow with the
            # molecule, so the coordinates are written into a fresh one below.
            rw.GetAtomWithIdx(idx).SetDoubleProp("hx", float(q[0]))
            rw.GetAtomWithIdx(idx).SetDoubleProp("hy", float(q[1]))
            rw.GetAtomWithIdx(idx).SetDoubleProp("hz", float(q[2]))
            rw.AddBond(h, idx, Chem.BondType.SINGLE)
    mol = rw.GetMol()
    if added:
        newconf = Chem.Conformer(mol.GetNumAtoms())
        for i in range(mol.GetNumAtoms()):
            if i in added:
                a = mol.GetAtomWithIdx(i)
                newconf.SetAtomPosition(i, Point3D(
                    a.GetDoubleProp("hx"), a.GetDoubleProp("hy"),
                    a.GetDoubleProp("hz")))
            else:
                newconf.SetAtomPosition(i, conf.GetAtomPosition(i))
        mol.RemoveAllConformers()
        mol.AddConformer(newconf)
    return mol, added


def hydrogen_names(mol, added):
    """Names for the added hydrogens, continuing their residue's numbering.

    The file's atom names have to stay unique within a residue. The added
    hydrogens take the next numbers after the ones already there, so H1..H12
    in a residue gets H13, H14 rather than a second H1.
    """
    per_res = {}
    for a in mol.GetAtoms():
        info = a.GetPDBResidueInfo()
        if info is None or a.GetSymbol() != "H":
            continue
        key = (info.GetChainId().strip(), info.GetResidueName(),
               info.GetResidueNumber())
        per_res.setdefault(key, []).append(info.GetName().strip())
    counters, names = {}, {}
    for idx, host in sorted(added.items()):
        hinfo = mol.GetAtomWithIdx(host).GetPDBResidueInfo()
        key = (hinfo.GetChainId().strip(), hinfo.GetResidueName(),
               hinfo.GetResidueNumber())
        if key not in counters:
            mx = 0
            for n in per_res.get(key, []):
                m = re.search(r"(\d+)$", n)
                if m:
                    mx = max(mx, int(m.group(1)))
            counters[key] = mx
        counters[key] += 1
        names[idx] = f"H{counters[key]}"
    return names


def write_deleted(out_path, mol, added):
    """The complex after a deletion: every atom with its own residue info,
    the added hydrogens named and bonded, CONECT from the bond list."""
    names = hydrogen_names(mol, added)
    conf = mol.GetConformer()
    lines, serial = [], 0
    for a in mol.GetAtoms():
        idx = a.GetIdx()
        info = a.GetPDBResidueInfo()
        p = pos(conf, idx)
        serial += 1
        if info is not None:
            name = info.GetName().strip()
            resn = info.GetResidueName()
            chain = info.GetChainId()
            resi = info.GetResidueNumber()
        else:
            hinfo = mol.GetAtomWithIdx(added[idx]).GetPDBResidueInfo()
            name = names[idx]
            resn = hinfo.GetResidueName()
            chain = hinfo.GetChainId()
            resi = hinfo.GetResidueNumber()
        record = "ATOM" if resn in STANDARD_AA else "HETATM"
        lines.append(atom_line(
            serial=serial, name=name, resn=resn, chain=chain, resi=resi,
            x=p[0], y=p[1], z=p[2], elem=a.GetSymbol(), record=record))
    conect = [conect_line(b.GetBeginAtomIdx() + 1, b.GetEndAtomIdx() + 1)
              for b in mol.GetBonds()]
    text = "\n".join(lines + conect) + "\nEND\n"
    check_serials_unique(text)
    Path(out_path).write_text(text)
    return len(lines), len(conect)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structure", required=True,
                    help="the .cif or .pdb the selection was made on")
    ap.add_argument("--out", required=True, help="output PDB")
    ap.add_argument("--group", default="benzene", choices=sorted(GROUPS),
                    help="which group to build (default benzene)")
    ap.add_argument("--chain", default="L", help="chain id for the new group")
    ap.add_argument("--resi", type=int, default=900,
                    help="residue number for the new group")
    ap.add_argument("--delete", action="store_true",
                    help="remove the selected atoms instead of replacing them "
                         "with a group; the atoms they leave behind are given "
                         "the hydrogens their valence now lacks")
    args = ap.parse_args()

    smiles = GROUPS[args.group]
    structure = Path(args.structure)
    sel, sel_atoms = read_selection()

    # Resolve the selection against the file it was made on, before any
    # conversion. The viewer's serial is the file's own numbering only when
    # that file is a PDB; for a CIF it is the atom's position in the file,
    # and the id at that position is what resolve() returns. Converting
    # first and resolving after would read that position as a serial of the
    # converted file and land one atom off — see selection.resolve.
    serials = resolve(structure, sel)

    # A CIF is converted to PDB here rather than by the caller.
    #
    # The viewer publishes CIF, and the selection resolves to the CIF's own
    # atom numbering. Converting outside this script meant gemmi's PDB writer,
    # which renumbers atoms as it writes and drops the serial it was given —
    # so serial 11612 in the CIF came back as a different atom in the PDB, and
    # the edit was applied to a carbon instead of the hydrogen the user
    # clicked. Writing through pdb_format.atom_line keeps the serial exactly
    # as it was read, because that module refuses to write a line whose
    # columns do not fit rather than shifting them.
    structure = to_pdb(structure)

    # Only the selected residue is bonded. proximityBonding over a whole
    # complex is quadratic and does not finish on one — see build_mol.
    sel_res = selected_residues(structure, serials)
    mol = build_mol(structure, bond_within=sel_res)
    conf = mol.GetConformer()

    # PDB serial -> RDKit atom index. MolFromPDBFile keeps the serial in the
    # atom's PDB info, which is the only reliable join between the two
    # numbering schemes.
    by_serial = {}
    for a in mol.GetAtoms():
        info = a.GetPDBResidueInfo()
        if info is not None:
            by_serial[info.GetSerialNumber()] = a.GetIdx()

    # A selected atom sitting on top of atoms from other residues is a
    # structure where several molecules were written to the same place — a
    # set of designs against one pocket, all of them superimposed. The
    # selection then resolves to an atom whose neighbours belong to ten
    # different molecules, and the group is attached to whichever of them the
    # distance test reaches first. Nothing about the file says so, and the
    # edit looks like it worked.
    #
    # Reported rather than refused: the user may well mean it, and the fix is
    # to publish one design at a time. But it has to be said out loud, because
    # the alternative is a benzene bonded to an oxygen 1.0 A away with no
    # indication that anything was ambiguous.
    for s in serials:
        i = by_serial.get(s)
        if i is None:
            continue
        info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
        if info is None:
            continue
        mine = (info.GetChainId().strip(), info.GetResidueName(),
                info.GetResidueNumber())
        pi = pos(conf, i)
        others = set()
        for a in mol.GetAtoms():
            if a.GetIdx() == i or a.GetSymbol() == "H":
                continue
            ainfo = a.GetPDBResidueInfo()
            if ainfo is None:
                continue
            key = (ainfo.GetChainId().strip(), ainfo.GetResidueName(),
                   ainfo.GetResidueNumber())
            if key == mine:
                continue
            if float(np.linalg.norm(pi - pos(conf, a.GetIdx()))) < 1.6:
                others.add(key)
        if others:
            names = ", ".join(f"{c}:{r}{n}" for c, r, n in sorted(others)[:6])
            print(f"warning: serial {s} is within 1.6 A of atoms from other "
                  f"residues ({names}) — the structure has several "
                  f"molecules in the same place. The group will bond to one of "
                  f"them; publish a single design first if that is not what "
                  f"you want.", file=sys.stderr)

    doomed = [by_serial[s] for s in serials if s in by_serial]
    if len(doomed) != len(serials):
        print(f"warning: {len(serials) - len(doomed)} selected serial(s) not "
              f"in the mol", file=sys.stderr)
    if not doomed:
        sys.exit("none of the selected atoms are in the molecule")
    print(f"selected {len(doomed)} atoms: "
          f"{', '.join(mol.GetAtomWithIdx(i).GetSymbol() for i in doomed)}",
          file=sys.stderr)

    # The bonds the deletion is about to break, and the atoms on the far side.
    # These are what the group reconnects to.
    #
    # Hydrogens are skipped: a PDB read with proximityBonding bonds every
    # hydrogen it finds, so the hydrogens sitting on the selected group's own
    # atoms look like surviving neighbours — and bonding the new group to a
    # hydrogen leaves the real attachment dangling.
    #
    # Atoms from another residue are skipped too, and for the same reason.
    # proximityBonding works on distance alone, so a protein side chain sitting
    # 1.3 A from a ligand's amine reads as a bond to it — and the new group is
    # then bonded to the protein as well as to the ligand, drawing a line
    # between two molecules that was never there.
    sel_res = set()
    for i in doomed:
        info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
        if info is not None:
            sel_res.add((info.GetChainId(), info.GetResidueName(),
                         info.GetResidueNumber()))

    def same_residue(j):
        info = mol.GetAtomWithIdx(j).GetPDBResidueInfo()
        if info is None or not sel_res:
            return True
        return (info.GetChainId(), info.GetResidueName(),
                info.GetResidueNumber()) in sel_res

    broken = []
    for i in doomed:
        for nb in mol.GetAtomWithIdx(i).GetNeighbors():
            j = nb.GetIdx()
            if j not in doomed and nb.GetSymbol() != "H" and same_residue(j):
                broken.append((i, j))
    if not broken:
        sys.exit("the selection is not bonded to the rest of the molecule — "
                 "nothing to attach the group to")
    # One entry per surviving neighbour.
    seen, attachments = set(), []
    for i, j in broken:
        if j not in seen:
            seen.add(j)
            attachments.append(j)
    print(f"attachment points: "
          f"{', '.join(mol.GetAtomWithIdx(j).GetSymbol() for j in attachments)}",
          file=sys.stderr)

    # The bond direction, from the attachment toward the removed group. This is
    # the direction the group has to grow in, and it is captured before the
    # deletion because the conformer is read at its original indices.
    dirs = []
    for i, j in broken:
        d = pos(conf, i) - pos(conf, j)
        n = np.linalg.norm(d)
        if n > 1e-6:
            dirs.append((j, d / n))
    anchor = np.mean([pos(conf, j) for j, _ in dirs], axis=0)
    mean_dir = np.mean([d for _, d in dirs], axis=0)
    nd = np.linalg.norm(mean_dir)
    if nd < 1e-6:
        sys.exit("the broken bonds have no consistent direction")
    mean_dir = mean_dir / nd

    # The chain and residue the ring inherits, from the atoms it replaces.
    # Writing it as its own residue in its own chain makes the viewer treat it
    # as a separate ligand; the replaced group was part of one residue, so the
    # ring is too.
    inherit = None
    for i in doomed:
        info = mol.GetAtomWithIdx(i).GetPDBResidueInfo()
        if info is not None:
            inherit = (info.GetChainId(), info.GetResidueName(),
                       info.GetResidueNumber())
            break
    if inherit is None:
        inherit = (args.chain, "RNG", args.resi)

    # Hydrogens the deletion orphaned. A PDB read with proximityBonding bonds
    # every hydrogen it finds, so the hydrogens that sat on the removed group's
    # atoms survive it — bonded to nothing, floating where the group used to
    # be. Leaving them puts stray atoms exactly where the new group is about
    # to go.
    #
    # The test is against the bonds the file was read with, not against the
    # bonds that survive the deletion. Bonding is bounded to the selected
    # residue — `build_mol(bond_within=...)` — so every hydrogen on every
    # other residue arrives with no bonds at all, and "no heavy-atom
    # neighbour" is true of all of them: sweeping on that test deleted the
    # hydrogens of nine untouched ligands in one run. A hydrogen is an
    # orphan of *this* deletion when it had heavy-atom neighbours before it
    # and every one of them is being removed. A hydrogen with no neighbours
    # at all was never bonded by this read; it is not this deletion's
    # doing, and removing it would take atoms the selection never named.
    doomed_set = set(doomed)
    orphan_h = []
    for a in mol.GetAtoms():
        if a.GetSymbol() != "H" or a.GetIdx() in doomed_set:
            continue
        heavy_nb = [n.GetIdx() for n in a.GetNeighbors()
                    if n.GetSymbol() != "H"]
        if heavy_nb and all(j in doomed_set for j in heavy_nb):
            orphan_h.append(a.GetIdx())

    # Delete the selected atoms, and the hydrogens they stranded. RemoveAtom
    # renumbers everything after each removal, so the surviving indices are
    # remapped through `shift` — over every atom being removed, orphans
    # included, or an attachment sitting above an orphan is remapped to the
    # wrong atom.
    removed = doomed_set | set(orphan_h)
    shift, removed_below = {}, 0
    for idx in range(mol.GetNumAtoms()):
        if idx in removed:
            removed_below += 1
        else:
            shift[idx] = idx - removed_below
    attachments = [shift[j] for j in attachments if j in shift]

    rw = Chem.RWMol(mol)
    for i in sorted(removed, reverse=True):
        rw.RemoveAtom(i)
    edited = rw.GetMol()

    if args.delete:
        # Nothing is placed and nothing is bonded in: the atoms are simply
        # gone. What the removal leaves behind is the valence it broke, and
        # publishing that without repair is how a fragment reaches the viewer
        # — it draws fine and reads as a fragment everywhere else.
        # `sel_res` was computed from the atoms being removed, so it names the
        # residue the edit touched; complete_hydrogens fills every atom of it
        # whose valence is now short, which is not only the neighbours of the
        # removed atoms — taking three carbons out of a ring makes a chain of
        # the two that remain, and the middle one lost no bond at all.
        # It was collected with the chain id as the PDB wrote it; the hydrogen
        # pass matches on the stripped form, so normalise here rather than
        # have a chain named "G " match nothing.
        sel_res = {(c.strip(), r, n) for c, r, n in sel_res}
        edited, added = complete_hydrogens(edited, sel_res)
        n_atoms, n_conect = write_deleted(args.out, edited, added)
        print(f"wrote {args.out}: {n_atoms} atoms, {len(removed)} selected "
              f"atoms removed, {len(added)} hydrogens added to complete the "
              f"valence, {n_conect} bonds", file=sys.stderr)
        return 0

    # The group template, with real geometry from an MMFF optimisation — a
    # thiazole's C-S is 1.72 A and its C-N 1.32 A, which no regular polygon
    # reproduces.
    #
    # The template keeps its hydrogens. They are part of the group's geometry
    # and they are what makes a vinyl a vinyl: its terminal carbon carries two
    # and its middle carbon one, and rebuilding them by hand means knowing
    # that per group. Reading them off the template instead means a new entry
    # in GROUPS is the whole cost of supporting it.
    #
    # A single-atom group is the exception, and it is stripped bare. SMILES
    # implies hydrogens to fill a bare atom's valence, so "O" is water, "N" is
    # ammonia, "C" is methane — and a substituent is none of those.
    template = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if template is None:
        sys.exit(f"could not parse the SMILES for {args.group}")
    if template.GetNumAtoms() == 1:
        template = Chem.MolFromSmiles(smiles)
    if AllChem.EmbedMolecule(template, randomSeed=42) != 0:
        sys.exit(f"could not build a 3D conformer for {args.group}")
    AllChem.MMFFOptimizeMolecule(template, maxIters=500)
    # The heavy atoms, in template order. Atom 0 is the substituent position.
    heavy = [a.GetIdx() for a in template.GetAtoms()
             if a.GetSymbol() != "H"]
    n_heavy = len(heavy)

    # The reference the group is aligned to: the whole group, placed where the
    # removed group was.
    #
    # Every heavy atom is mapped, not just the first two. A two-atom reference
    # fixes the bond's position and direction but leaves the group free to
    # rotate about that bond, and RDKit picks an arbitrary angle — one that can
    # put a far side of the group back on top of the attachment, so the viewer
    # draws a bond that was never asked for.
    #
    # The group's centre goes one bond length plus its own radius beyond the
    # attachment, along the direction the removed group occupied, with atom 0
    # facing back at the attachment. Its plane is the removed group's own
    # plane, so the group lies where the old one lay rather than at an
    # arbitrary tilt.
    conf_t = template.GetConformer()
    T = np.array([pos(conf_t, k) for k in heavy])
    t_centre = T.mean(0)
    # The template's own frame: +x from the centre toward atom 0, +z the
    # group's plane normal. Orthogonalised, because in a five-membered ring the
    # vector to atom 0 and the plane normal are not perpendicular and using
    # them raw shears the group.
    #
    # A single-atom group has no frame of its own — its centre *is* atom 0, so
    # the vector between them is zero and dividing by its length is a NaN that
    # propagates into every coordinate. It needs no frame either: there is one
    # atom, it goes one bond length out along the bond direction, and there is
    # nothing else to orient.
    if n_heavy == 1:
        ex = ey = ez = None
    else:
        ex = T[0] - t_centre
        ex = ex / np.linalg.norm(ex)
        if n_heavy >= 3:
            ez = np.cross(T[1] - t_centre, T[2] - t_centre)
            ne = np.linalg.norm(ez)
            ez = ez / ne if ne > 1e-6 else None
        else:
            # A two-atom group has no plane of its own. Its normal is taken
            # from the removed group's plane below, so there is nothing to
            # compute here.
            ez = None
        if ez is not None:
            ez = ez - np.dot(ez, ex) * ex
            ez = ez / np.linalg.norm(ez)
            ey = np.cross(ez, ex)
        else:
            ey = None

    # The removed group's plane, for the new group to lie in. Its third
    # principal axis is the normal when the group is planar; otherwise the new
    # group's own orientation is kept and only the bond direction is honoured.
    P = np.array([pos(conf, i) for i in doomed])
    nrm = None
    if len(P) >= 3:
        sv = np.linalg.svd(P - P.mean(0), compute_uv=False)
        if sv[2] < 0.3 * sv[0]:
            cand = np.linalg.svd(P - P.mean(0))[2][2]
            cand = cand - np.dot(cand, mean_dir) * mean_dir
            nc = np.linalg.norm(cand)
            if nc > 1e-6:
                nrm = cand / nc
    if nrm is None:
        nrm = ez
    if nrm is None:
        # Neither the removed group nor the new one has a plane to take a
        # normal from — a two-atom group replacing a two-atom group, say. Any
        # axis perpendicular to the bond will do, because the group is a line
        # and rotating it about its own axis changes nothing.
        arb = (np.array([1.0, 0.0, 0.0])
               if abs(mean_dir[0]) < 0.9 else np.array([0.0, 1.0, 0.0]))
        nrm = np.cross(mean_dir, arb)
        nrm = nrm / np.linalg.norm(nrm)

    # The target frame: centre one bond length plus the group's radius out
    # along the bond direction, atom 0 facing back at the attachment, plane
    # normal as computed above.
    #
    # The bond length is the group's own, not a constant. A vinyl's C-S is
    # 1.75 A and a benzene's C-C 1.39 A, and using one number for both puts
    # the group's first atom at the wrong distance — which drags its hydrogens
    # into the attachment. The pair is looked up from covalent radii, which is
    # what the length actually is; a pair the table does not cover falls back
    # to the aromatic C-C the skill was built around.
    radius = np.linalg.norm(T[0] - t_centre)
    a_elem = mol.GetAtomWithIdx(attachments[0]).GetSymbol()
    g_elem = template.GetAtomWithIdx(heavy[0]).GetSymbol()
    bond_len = (COVALENT.get(a_elem, 0.76) + COVALENT.get(g_elem, 0.76))
    target_centre = anchor + (radius + bond_len) * mean_dir
    target_ex = -mean_dir
    target_ez = nrm
    target_ey = np.cross(target_ez, target_ex)

    # The reference carries the group's own topology, so a vinyl's double bond
    # and a benzene's ring are both represented rather than approximated by a
    # ring of single bonds.
    #
    # The conformer is replaced, not added. Copying the template brings its own
    # conformer along, and AddConformer would append a second one — leaving
    # GetConformer to return the untransformed first, which makes the
    # alignment solve against the wrong coordinates and report a perfect RMSD
    # for a transform that was never needed.
    ref = Chem.RWMol(template)
    conf_ref = Chem.Conformer(ref.GetNumAtoms())
    for i, k in enumerate(heavy):
        rel = T[i] - t_centre
        # A single-atom group has no frame of its own, and needs none: its
        # centre is atom 0, so every component is zero and the atom lands on
        # the target centre exactly. A two-atom group has no ey, and needs
        # none either — every atom lies on the ex axis, so its component along
        # ey is zero.
        p = (target_centre
             + (np.dot(rel, ex) * target_ex if ex is not None else 0.0)
             + (np.dot(rel, ey) * target_ey if ey is not None else 0.0)
             + (np.dot(rel, ez) * target_ez if ez is not None else 0.0))
        conf_ref.SetAtomPosition(k, Point3D(*p))
    # The hydrogens ride along with the heavy atom they are bonded to, so they
    # keep the geometry the MMFF optimisation gave them.
    for a in template.GetAtoms():
        if a.GetSymbol() != "H":
            continue
        k = a.GetIdx()
        host = [n.GetIdx() for n in a.GetNeighbors() if n.GetSymbol() != "H"]
        if not host:
            continue
        rel_h = pos(conf_t, k) - pos(conf_t, host[0])
        p = pos(conf_ref, host[0]) + rel_h
        conf_ref.SetAtomPosition(k, Point3D(*p))
    ref.RemoveConformer(0)
    ref.AddConformer(conf_ref)

    # RDKit solves the rigid transform that puts every heavy atom onto the
    # reference, then applies it.
    rmsd, tform = AllChem.GetAlignmentTransform(
        template, ref, atomMap=[(k, k) for k in heavy])
    AllChem.TransformMol(template, tform)
    print(f"group placed (alignment RMSD {rmsd:.3f} Å)", file=sys.stderr)

    # The hydrogens are corrected after the transform, not before it. The
    # alignment maps heavy atoms only, so the transform is solved from them —
    # and it then carries the hydrogens wherever they happened to be in the
    # template. A group with no plane of its own, a vinyl or an ethyl, has its
    # normal chosen arbitrarily, and the hydrogens follow it.
    #
    # Turning the group over about the bond axis does not help: the attachment
    # sits on that axis, so every hydrogen keeps its distance to it. What does
    # help is choosing the angle about the axis. The heavy atoms are on or
    # parallel to it, so rotating about it leaves the bond and the group's own
    # geometry intact while swinging the hydrogens clear — and the best angle
    # is simply the one that puts them furthest from the attachment.
    conf_t = template.GetConformer()
    hyd = [a.GetIdx() for a in template.GetAtoms() if a.GetSymbol() == "H"]
    if hyd:
        axis = mean_dir / np.linalg.norm(mean_dir)
        base = {k: pos(conf_t, k) for k in hyd}
        best, best_score = None, -1.0
        for deg in range(0, 360, 15):
            th = np.radians(deg)
            # Rodrigues rotation of every hydrogen about the axis through the
            # anchor.
            score = 0.0
            for k in hyd:
                v = base[k] - anchor
                rot = (v * np.cos(th)
                       + np.cross(axis, v) * np.sin(th)
                       + axis * np.dot(axis, v) * (1 - np.cos(th)))
                score += np.linalg.norm(rot)
            if score > best_score:
                best_score, best = score, deg
        th = np.radians(best)
        for k in hyd:
            v = base[k] - anchor
            rot = (v * np.cos(th)
                   + np.cross(axis, v) * np.sin(th)
                   + axis * np.dot(axis, v) * (1 - np.cos(th)))
            conf_t.SetAtomPosition(k, Point3D(*(rot + anchor)))

    # Merge and bond the group's atom 0 to each attachment.
    combined = Chem.CombineMols(edited, template)
    rw2 = Chem.RWMol(combined)
    base = edited.GetNumAtoms()

    # The hydrogen on the group's atom 0, if it has one, is removed. The
    # template is the unsubstituted group, so its atom 0 carries whatever
    # hydrogen that position normally has — and that position is exactly where
    # the bond to the attachment now goes. Left in place the hydrogen sits on
    # top of the attachment, closer to it than any bond allows.
    subst = [n.GetIdx() for n in rw2.GetAtomWithIdx(base).GetNeighbors()
             if n.GetSymbol() == "H"]
    for h in sorted(subst, reverse=True):
        rw2.RemoveAtom(h)

    for j in attachments:
        rw2.AddBond(j, base, Chem.BondType.SINGLE)
    out_mol = rw2.GetMol()

    # Write PDB. The ring's atoms are named by element and position so a
    # thiazole reads S1, C2, N3 rather than every atom being called C.
    conf_o = out_mol.GetConformer()
    lines, serial = [], 0
    for a in out_mol.GetAtoms():
        idx = a.GetIdx()
        info = a.GetPDBResidueInfo()
        p = pos(conf_o, idx)
        serial += 1
        if idx >= base:
            elem = a.GetSymbol()
            same = [i for i in range(base, idx)
                    if out_mol.GetAtomWithIdx(i).GetSymbol() == elem]
            name = f"{elem}{len(same) + 1}"
            resn, chain, resi = inherit[1], inherit[0], inherit[2]
        else:
            name = (info.GetName().strip() if info else a.GetSymbol())
            resn = info.GetResidueName() if info else "UNL"
            chain = info.GetChainId() if info else args.chain
            resi = info.GetResidueNumber() if info else 1
        # The line is written by the shared formatter. It owns the column
        # layout, and a residue number above 9999 — which these structures
        # have, the ligands are numbered 43002 and 32002 — is refused rather
        # than written shifted into the chain ID column.
        #
        # The record type follows what the atom is. A standard amino acid is
        # ATOM and everything else is HETATM, which is the distinction every
        # downstream tool reads: meeko's receptor preparer templates the ATOM
        # residues as protein and treats HETATM ones as ligands, so a protein
        # written entirely as HETATM reaches the docking step with whole side
        # chains missing. The new group is HETATM, being part of a ligand.
        if idx >= base:
            record = "HETATM"
        else:
            record = ("ATOM" if resn in STANDARD_AA else "HETATM")
        lines.append(atom_line(
            serial=serial, name=name, resn=resn, chain=chain,
            resi=resi if isinstance(resi, int) else 1,
            x=p[0], y=p[1], z=p[2], elem=a.GetSymbol(), record=record))

    # CONECT from the bond list, so the viewer draws the group and its bond to
    # the rest rather than a cloud of atoms. The serials are the ones the
    # atoms carry in this file, which the formatter assigned.
    conect = [conect_line(b.GetBeginAtomIdx() + 1, b.GetEndAtomIdx() + 1)
              for b in out_mol.GetBonds()]

    n_h = sum(1 for a in out_mol.GetAtoms()
              if a.GetIdx() >= base and a.GetSymbol() == "H")
    text = "\n".join(lines + conect) + "\nEND\n"
    # The file is one molecule written in one pass, so a numbering collision
    # is not expected — but the check is cheap and it is the difference
    # between a structure that renders wrong and an error that names the
    # field.
    check_serials_unique(text)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}: {out_mol.GetNumAtoms()} atoms, "
          f"{len(doomed)} selected atoms replaced by a {args.group} "
          f"({n_heavy} heavy atoms + {n_h} H)", file=sys.stderr)


if __name__ == "__main__":
    main()
