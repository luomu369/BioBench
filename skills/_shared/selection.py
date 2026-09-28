"""Reading the viewer's selection, and the structure it was made on.

Both molecule-editing scripts need the same three things: the selection the
user made, the PDB serials it refers to, and the structure as an RDKit mol.
Each had written its own copy, and the copies had already drifted — one
warned when it resolved fewer atoms than it was given, the other did not, so
the same partial selection was silent in one script and reported in the
other.

The serial is the join between the two numbering schemes. 3Dmol's own index
is not used: it reorders atoms when it builds a model, so its index is not a
position in the file and cannot be walked back to a serial.

Usage:
    from selection import read_selection, resolve, build_mol, pos

    sel, atoms = read_selection()
    serials = resolve(structure, sel)
    mol = build_mol(structure)
"""
import json
import sys
from pathlib import Path

import numpy as np
from rdkit import Chem

from pdb_format import iter_atoms

# Covalent radii (Cordero 2008), for the distance test that replaces
# proximityBonding when bonding is bounded to a few residues.
COVALENT = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05,
    "P": 1.07, "F": 0.57, "Cl": 1.02, "Br": 1.20, "I": 1.39,
    "Se": 1.20, "Si": 1.11, "B": 0.84,
}


def viewer_dir():
    """The viewer's static directory, which holds selection.json."""
    from biobench_paths import viewer_static
    return viewer_static()


def read_selection(what="select"):
    """The viewer's selection, as (full dict, atom list).

    `what` finishes the error message, so each script says what the user
    should have selected rather than a generic instruction.
    """
    p = viewer_dir() / "selection.json"
    if not p.is_file():
        sys.exit("no selection.json — select atoms in the viewer first")
    d = json.loads(p.read_text())
    atoms = d.get("atoms") or []
    if not atoms:
        sys.exit(f"selection has no atoms — switch the viewer to Atom mode "
                 f"and select the {what}")
    return d, atoms


def _cif_atom_rows(text: str):
    """The atom_site rows of a mmCIF in file order, or None if not a CIF.

    Each row is (id, atom name, resn, resi, chain) read from the same fields
    the viewer reads — auth_* where present, label_* otherwise — with the id
    the file itself carries. A TER record is the one row the viewer's parser
    drops, so it is dropped here too; a model change ends the first model,
    which is the only one the viewer builds.
    """
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() != "loop_":
            i += 1
            continue
        j = i + 1
        names = []
        while j < n and lines[j].strip().startswith("_atom_site."):
            # CIF tags are case-insensitive, and a file's writer chooses:
            # Cartn_x from one, cartn_x from another. Fold to lower case so
            # the lookups below are one spelling, not several.
            names.append(lines[j].strip().split(".", 1)[1].strip().lower())
            j += 1
        if not names or "cartn_x" not in names:
            i = max(j, i + 1)
            continue
        idx = {name: k for k, name in enumerate(names)}
        raw = []
        while j < n:
            s = lines[j].strip()
            if (not s or s.startswith("loop_") or s.startswith("_")
                    or s.startswith("#") or s.startswith("data_")):
                break
            toks = s.split()
            if len(toks) != len(names):
                break
            raw.append(toks)
            j += 1
        if not raw:
            return None

        def field(t, *preference):
            for name in preference:
                if name in idx:
                    return t[idx[name]].strip("'\"")
            return ""

        rows = []
        model = field(raw[0], "pdbx_pdb_model_num") or "1"
        for t in raw:
            if field(t, "pdbx_pdb_model_num") not in ("", model):
                break
            if field(t, "group_pdb") == "TER":
                continue
            rows.append((
                int(field(t, "id")),
                field(t, "auth_atom_id", "label_atom_id"),
                field(t, "auth_comp_id", "label_comp_id"),
                field(t, "auth_seq_id", "label_seq_id"),
                field(t, "auth_asym_id", "label_asym_id"),
            ))
        return rows or None
    return None


def resolve(structure: Path, sel: dict):
    """The PDB serials the viewer's selection refers to.

    The viewer records the PDB serial for every atom it selects, so this is
    a lookup. A selection written before serials were recorded falls back to
    the label, "N9 — SAH 2001 · 1", which gives the atom name, residue and
    chain.

    The fields come from the shared parser rather than from slices here. A
    slice that is off by one reads a different residue name and the
    selection resolves to the wrong atoms, with nothing to show it happened.

    **The recorded serial is the PDB serial only when the structure on
    screen was a PDB.** A mmCIF is parsed by 3Dmol's CIF reader, which never
    records a `pdbline` and numbers each atom by its row — the
    `_atom_site.id` the file carries is dropped on the floor. The viewer's
    `pdbSerial()` falls back to that counter, so what arrives here for a CIF
    is the atom's 0-based position in the file, one less than its id.
    Looking it up among the file's ids lands on the atom *before* the one
    that was clicked, and inside one residue — a ligand's six hydrogens, say
    — nothing about the residue or the label gives it away. It happened
    here: an edit asked for the acid's O-H hydrogen and replaced the ring's
    para hydrogen instead, one atom earlier in the same residue.

    So for a CIF the recorded serial is read as a position, and the id of
    the row at that position is returned — the id every other reader of the
    file uses. The row is checked against the residue and chain the viewer
    recorded, and a disagreement is reported rather than resolved quietly.
    """
    text = structure.read_text(errors="replace")
    cif_rows = _cif_atom_rows(text)

    out, used = [], set()
    for a in sel["atoms"]:
        s = a.get("serial")
        hit = None
        if cif_rows is not None:
            if s is not None and 0 <= s < len(cif_rows):
                row = cif_rows[s]
                if row[0] in used:
                    continue
                if _row_is(row, a):
                    hit = row[0]
                else:
                    print(f"warning: serial {s} is the atom at file position "
                          f"{s}, {row[2]}{row[3]}:{row[1]} on chain {row[4]}, "
                          f"but the selection names {a.get('resn')} "
                          f"{a.get('resi')} on chain {a.get('chain')} — "
                          f"falling back to the label", file=sys.stderr)
        else:
            for atom in iter_atoms(text):
                if atom.serial == s and atom.serial not in used:
                    hit = atom.serial
                    break
        if hit is None:
            hit = _resolve_by_label(text, cif_rows, a, used)
        if hit is None:
            continue
        used.add(hit)
        out.append(hit)

    if not out:
        sys.exit("could not resolve the selection against the structure")
    if len(out) != len(sel["atoms"]):
        print(f"warning: resolved {len(out)} of {len(sel['atoms'])} atoms",
              file=sys.stderr)
    return out


def _row_is(row, a) -> bool:
    """Whether a CIF row is the residue and chain the viewer recorded."""
    resn, resi, chain = a.get("resn"), a.get("resi"), a.get("chain")
    if resn is not None and str(resn).strip() != row[2]:
        return False
    if chain is not None and str(chain).strip() != row[4]:
        return False
    if resi is not None:
        try:
            if int(resi) != int(row[3]):
                return False
        except (TypeError, ValueError):
            if str(resi).strip() != str(row[3]).strip():
                return False
    return True


def _resolve_by_label(text, cif_rows, a, used):
    """The atom the selection's label names, for a selection with no serial.

    The label is "NAME — RESN RESI · CHAIN". It is the only join a viewer
    older than the serial field leaves, and it is weaker than a serial: a
    residue with several atoms of one name — a ligand's hydrogens — matches
    the first of them.
    """
    label = a.get("label", "")
    try:
        name, rest = label.split("—", 1)
        resn_chain = rest.rsplit("·", 1)
        resn = resn_chain[0].strip().rsplit(" ", 1)[0].strip()
        chain = resn_chain[1].strip()
        name = name.strip()
    except (ValueError, IndexError):
        return None
    if cif_rows is not None:
        for serial, n, r, resi, c in cif_rows:
            if serial in used:
                continue
            if n == name and r == resn and c == chain:
                return serial
        return None
    for atom in iter_atoms(text):
        if atom.serial in used:
            continue
        if atom.name == name and atom.resn == resn and atom.chain == chain:
            return atom.serial
    return None


def build_mol(structure: Path, bond_within=None):
    """The PDB as an RDKit mol with 3D coordinates and no sanitisation.

    Sanitisation is skipped because a PDB read straight off disk routinely
    has valence errors — a missing hydrogen, an unusual protonation —
    and RDKit refuses the whole molecule over one of them. The edit only needs
    the graph and the coordinates, both of which survive without it.

    `bond_within` limits bonding to the residues named in it, as
    (chain, resn, resi) tuples. Everything else is read with no bonds at all.

    This is not an optimisation, it is the difference between finishing and
    not. proximityBonding builds a neighbour grid over every atom in the file
    and tests each pair, which is quadratic: on a 12,000-atom complex it ran
    for minutes and reached 21 GB of RSS before it was killed, on a machine
    with 121 GB. The edit only ever needs the bonds inside the residue being
    edited — the group is attached to an atom of that residue, and the atoms
    of every other residue are there to be counted and written back, not to be
    bonded to.

    A caller that passes nothing gets the whole file bonded, which is what a
    small structure wants and what a large one cannot survive.
    """
    mol = Chem.MolFromPDBFile(str(structure), sanitize=False,
                              removeHs=False, proximityBonding=False)
    if mol is None:
        sys.exit(f"RDKit could not read {structure}")
    if not mol.GetConformer().Is3D():
        sys.exit("the structure has no 3D coordinates")

    if bond_within:
        # Bond the named residues by distance, using covalent radii.
        #
        # RDKit's own DetermineBonds was tried first and does not work here: it
        # needs a mol it can sanitise, and this one is deliberately
        # unsanitised, so it either raises or bonds nothing. A distance test
        # against the same radii is what proximityBonding itself does, run
        # over the few hundred atoms of one residue instead of the whole file.
        conf = mol.GetConformer()
        idx = []
        for a in mol.GetAtoms():
            info = a.GetPDBResidueInfo()
            if info is None:
                continue
            key = (info.GetChainId().strip(), info.GetResidueName(),
                   info.GetResidueNumber())
            if key in bond_within:
                idx.append(a.GetIdx())
        P = np.array([pos(conf, i) for i in idx])
        rw = Chem.RWMol(mol)
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                d = float(np.linalg.norm(P[a] - P[b]))
                ea = mol.GetAtomWithIdx(idx[a]).GetSymbol()
                eb = mol.GetAtomWithIdx(idx[b]).GetSymbol()
                # 1.2x the sum of covalent radii, the same tolerance
                # proximityBonding uses. A hydrogen's radius is small enough
                # that 1.2x still excludes a 1.5 A contact to a heavy atom.
                limit = 1.2 * (COVALENT.get(ea, 0.8) + COVALENT.get(eb, 0.8))
                if d < limit and mol.GetBondBetweenAtoms(idx[a], idx[b]) is None:
                    rw.AddBond(idx[a], idx[b], Chem.BondType.SINGLE)
        mol = rw.GetMol()
    else:
        mol = Chem.MolFromPDBFile(str(structure), sanitize=False,
                                  removeHs=False, proximityBonding=True)
        if mol is None:
            sys.exit(f"RDKit could not read {structure}")

    return mol


def pos(conf, i):
    """Atom `i`'s position as a numpy array."""
    p = conf.GetAtomPosition(i)
    return np.array([p.x, p.y, p.z])
