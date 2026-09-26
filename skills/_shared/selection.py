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


def resolve(structure: Path, sel: dict):
    """The PDB serials the viewer's selection refers to.

    The viewer records the PDB serial for every atom it selects, so this is
    a lookup. A selection written before serials were recorded falls back to
    the label, "N9 — SAH 2001 · 1", which gives the atom name, residue and
    chain.

    The fields come from the shared parser rather than from slices here. A
    slice that is off by one reads a different residue name and the
    selection resolves to the wrong atoms, with nothing to show it happened.
    """
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
    if len(out) != len(sel["atoms"]):
        print(f"warning: resolved {len(out)} of {len(sel['atoms'])} atoms",
              file=sys.stderr)
    return out


def build_mol(structure: Path):
    """The PDB as an RDKit mol with 3D coordinates and no sanitisation.

    Sanitisation is skipped because a PDB read straight off disk routinely
    has valence errors — a missing hydrogen, an unusual protonation — and
    RDKit refuses the whole molecule over one of them. The edit only needs
    the graph and the coordinates, both of which survive without it.
    """
    mol = Chem.MolFromPDBFile(str(structure), sanitize=False,
                              removeHs=False, proximityBonding=True)
    if mol is None:
        sys.exit(f"RDKit could not read {structure}")
    if not mol.GetConformer().Is3D():
        sys.exit("the structure has no 3D coordinates")
    return mol


def pos(conf, i):
    """Atom `i`'s position as a numpy array."""
    p = conf.GetAtomPosition(i)
    return np.array([p.x, p.y, p.z])
