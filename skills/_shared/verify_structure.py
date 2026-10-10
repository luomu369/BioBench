"""Verify a published structure before the viewer shows it.

The checks here are the traps in `skills/_shared/silent-failures.md`, made
mechanical. Each one produced output that looked plausible and was wrong:
a protein written as HETATM reached docking with side chains missing; a
CONECT record still naming the carbon a hydrogen was rebuilt away from drew
a 3.4 A bond across a benzene ring; a cropped design context was published
as a receptor; meeko dropped a pocket phenylalanine and the score improved
because of the hole. Nothing errored. The file wrote, the viewer drew it,
the number printed.

This script does not fix anything and does not decide whether a structure
is good enough for its purpose. It reports facts — counts, distances, sets —
so a reviewer (see `.opencode/agent/structure-reviewer.md`) can apply the
judgement that depends on which skill produced the file.

Usage:
    python3 skills/_shared/verify_structure.py --structure FILE [options]

    --source FILE       compare against this structure (the pre-edit input,
                        the pre-dock receptor, the design target)
    --frozen-chains A,B chains that must be atom-for-atom identical to the
                        source: the molecule-editing invariant. An MD output
                        must NOT be checked this way — its protein is
                        supposed to have moved.
    --json              machine-readable report
    --quiet             findings only, no header

Exit code 0 when nothing failed, 1 when any check failed.

PDB and mmCIF are both read. CIF has no CONECT records, so bonds are taken
from geometry there and the CONECT-specific checks are skipped — say so in
the report rather than passing silently.
"""
import difflib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdb_format import Atom, check_serials_unique, iter_atoms  # noqa: E402

# Covalent radii (A), Cordero et al. A bond is assumed when the distance is
# at most BOND_TOL times the sum. 0.31+0.76 = 1.07 for C-H, times 1.25 gives
# 1.34 A — wide enough for a real C-H, narrow enough that a hydrogen sitting
# on the wrong carbon across the ring (3.4 A in the biaryl bug) is not one.
COVALENT = {
    "H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57,
    "SI": 1.11, "P": 1.07, "S": 1.05, "CL": 1.02, "BR": 1.20, "SE": 1.20,
    "I": 1.39,
}
BOND_TOL = 1.25
# Same-element heavy atoms closer than this are a real overlap, not a
# contact. Vina's scoring softens the vdW term and will still call a pose
# with overlap good, so the clash has to be checked directly.
CLASH_CUTOFF = 2.2
# A hydrogen further than this from every heavy atom is not attached to
# anything.
H_MAX = 1.35

STANDARD_AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "MSE", "SEC", "PYL",
}
WATERS = {"HOH", "WAT", "DOD", "H2O"}
# Pseudo-residues: fpocket writes a pocket's apices as one residue named
# STP holding 50-160 unconnected points, and the valence check then reports
# every apex with too few or too many neighbours. They are coordinates to
# draw, not a molecule, so no check that assumes chemistry applies.
PSEUDO_RES = {"STP", "DUM"}
NUCLEIC = {"A", "C", "G", "T", "U", "DA", "DC", "DG", "DT", "DU"}

# Total connections (heavy bonds + hydrogens) a small-molecule atom may
# have. Only non-standard residues are checked: standard residues are the
# force field's business, and altloc B atoms would double-count.
VALENCE_MAX = {"C": 4, "N": 4, "O": 2, "S": 6, "P": 5, "B": 4, "H": 1}
VALENCE_MIN = {"C": 3, "N": 2, "O": 1, "S": 1, "P": 3, "B": 3, "H": 1}
METALS = {"NA", "K", "MG", "CA", "ZN", "FE", "MN", "CU", "CO", "NI", "HG",
          "CD", "CL", "BR"}


@dataclass
class Finding:
    check: str
    severity: str          # FAIL or WARN
    message: str
    details: list          # capped examples


def _elem_of(atom: Atom) -> str:
    e = (atom.elem or "").strip().upper()
    if e:
        return e
    # Element column blank: fall back to the atom name, which is what a
    # reader has to do anyway.
    name = atom.name.strip()
    return name[:2].upper() if name[:2].upper() in COVALENT else name[:1].upper()


def _dist(a: Atom, b: Atom) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def _key(a: Atom):
    return (a.chain, a.resi, a.icode, a.name)


def _reskey(a: Atom):
    return (a.chain, a.resi, a.icode)


# ---------------------------------------------------------------- CIF reader

def _split_cif_row(line: str):
    """Whitespace-split one CIF data row, honouring simple quoting."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c.isspace():
            i += 1
            continue
        if c in "'\"":
            j = line.find(c, i + 1)
            if j < 0:
                out.append(line[i + 1:].strip())
                break
            out.append(line[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and not line[j].isspace():
                j += 1
            out.append(line[i:j])
            i = j
    return out


def load_cif(path: Path):
    """Atoms from an mmCIF `_atom_site` loop.

    auth_asym_id/auth_seq_id are preferred over label_* when present: they
    are what current.json's chain ids and the viewer's selection use, so a
    report keyed on label_asym_id would name chains the user cannot see.
    """
    lines = path.read_text().splitlines()
    atoms, i = [], 0
    while i < len(lines):
        if lines[i].strip() != "loop_":
            i += 1
            continue
        j = i + 1
        tags = []
        while j < len(lines) and lines[j].strip().startswith("_"):
            tags.append(lines[j].strip())
            j += 1
        if not any(t.startswith("_atom_site.") for t in tags):
            i = j
            continue
        col = {t.split(".", 1)[1]: k for k, t in enumerate(tags)}
        while j < len(lines):
            row = _split_cif_row(lines[j])
            if (not row or lines[j].strip() == "loop_"
                    or lines[j].strip().startswith("_")
                    or lines[j].strip().startswith("#")):
                break
            if len(row) != len(tags):
                break
            def get(name, default=""):
                k = col.get(name)
                return row[k] if k is not None else default
            chain = get("auth_asym_id") or get("label_asym_id") or " "
            resi = get("auth_seq_id") or get("label_seq_id") or "1"
            icode = (get("pdbx_PDB_ins_code") or " ").strip() or " "
            try:
                resi_i = int(resi)
            except ValueError:
                resi_i = abs(hash(resi)) % 100000
            try:
                x, y, z = float(get("Cartn_x")), float(get("Cartn_y")), float(get("Cartn_z"))
            except ValueError:
                j += 1
                continue
            atoms.append(Atom(
                serial=len(atoms) + 1,
                name=get("label_atom_id").strip(),
                resn=get("label_comp_id").strip(),
                # Full auth_asym_id, not one character: a PDB chain is a
                # single column but mmCIF's is not, and ResGen's ten designs
                # are chains ra-rj. Truncating to the first letter merged
                # them into one residue with thirty connections each.
                chain=(chain or " ")[:4],
                resi=resi_i,
                x=x, y=y, z=z,
                elem=get("type_symbol").strip().upper(),
                icode=icode,
                record=(get("group_PDB") or "ATOM").strip() or "ATOM",
            ))
            j += 1
        i = j
    if not atoms:
        raise ValueError(f"no _atom_site records found in {path.name}")
    return atoms, "cif"


def load_structure(path: Path):
    text = path.read_text()
    if path.suffix.lower() == ".cif":
        return load_cif(path)
    atoms = list(iter_atoms(text))
    if not atoms:
        raise ValueError(f"no ATOM/HETATM records found in {path.name}")
    conect = []
    for line in text.splitlines():
        if not line.startswith("CONECT"):
            continue
        fields = [line[6:11], line[11:16], line[16:21], line[21:26], line[26:31]]
        serials = [int(f) for f in fields if f.strip()]
        conect.append(serials)
    return atoms, ("pdb", conect)


# ------------------------------------------------------------------ geometry

def bond_graph(atoms):
    """(i, j) index pairs assumed bonded, by distance. Cell-hashed: the
    trimer is 22k atoms and a full n^2 scan is 250M pairs.

    Two passes, because one grid cannot serve every element. A 2 A cell
    with a +/-1 neighbourhood never misses a pair closer than 2 A — which
    covers C, N, O, F and H, whose longest bond (C-C at 1.9 A) fits — but
    S-S is 2.63 A and S-C is 2.26 A, and a disulfide straddling a cell
    edge was silently dropped from the graph: the CONECT check then called
    a real bond stale. The large-radius elements get their own pass over a
    4 A grid, queried from their own atoms so the cost stays proportional
    to how many of them there are, not to the size of the protein.
    """
    SMALL = {"H", "C", "N", "O", "F"}

    def grid(cell):
        g = {}
        for i, a in enumerate(atoms):
            g.setdefault((int(a.x // cell), int(a.y // cell),
                          int(a.z // cell)), []).append(i)
        return g

    def add_pair(i, j, out):
        ra, rb = COVALENT.get(_elem_of(atoms[i])), COVALENT.get(_elem_of(atoms[j]))
        if ra is None or rb is None:
            return
        d = _dist(atoms[i], atoms[j])
        if 0.4 < d <= BOND_TOL * (ra + rb):
            out.add((i, j))

    bonds = set()
    # Pass 1: both atoms small-radius. Complete for this subset — the
    # longest small-small bond is under the 2 A cell size.
    g1 = grid(2.0)
    for i, a in enumerate(atoms):
        if _elem_of(a) not in SMALL:
            continue
        cx, cy, cz = int(a.x // 2), int(a.y // 2), int(a.z // 2)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in g1.get((cx + dx, cy + dy, cz + dz), ()):
                        if j <= i or _elem_of(atoms[j]) not in SMALL:
                            continue
                        add_pair(i, j, bonds)
    # Pass 2: anything involving a large-radius element, queried from that
    # atom over a 4 A grid. The longest bond in the table (I-I, 3.48 A)
    # fits inside a 4 A cell.
    big = [i for i, a in enumerate(atoms) if _elem_of(a) not in SMALL
           and _elem_of(a) in COVALENT]
    if big:
        g2 = grid(4.0)
        for i in big:
            a = atoms[i]
            cx, cy, cz = int(a.x // 4), int(a.y // 4), int(a.z // 4)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for j in g2.get((cx + dx, cy + dy, cz + dz), ()):
                            if j == i:
                                continue
                            add_pair(min(i, j), max(i, j), bonds)
    return bonds


def neighbours(bonds, n):
    adj = {i: set() for i in range(n)}
    for i, j in bonds:
        adj[i].add(j)
        adj[j].add(i)
    return adj


def _is_heavy(a: Atom) -> bool:
    return _elem_of(a) != "H"


def _same_residue(a: Atom, b: Atom) -> bool:
    return _reskey(a) == _reskey(b)


# ------------------------------------------------------------------- checks

def check_records(atoms):
    """Standard amino acids must be ATOM; anything else HETATM.

    meeko's receptor preparer templates ATOM residues as protein and treats
    HETATM ones as ligand. A protein written entirely as HETATM reaches
    docking with whole side chains missing.
    """
    wrong_aa, wrong_other = [], []
    for a in atoms:
        if a.record == "ATOM" and a.resn not in STANDARD_AA and a.resn not in WATERS \
                and a.resn not in NUCLEIC and _elem_of(a) not in METALS:
            wrong_other.append(a)
        elif a.record == "HETATM" and a.resn in STANDARD_AA:
            wrong_aa.append(a)
    findings = []
    if wrong_aa:
        findings.append(Finding(
            "record_type", "FAIL",
            f"{len(wrong_aa)} standard-amino-acid atoms written as HETATM",
            [f"{a.chain} {a.resn} {a.resi} {a.name}" for a in wrong_aa[:10]]))
    if wrong_other:
        findings.append(Finding(
            "record_type", "WARN",
            f"{len(wrong_other)} non-standard atoms written as ATOM",
            [f"{a.chain} {a.resn} {a.resi} {a.name}" for a in wrong_other[:10]]))
    return findings


def check_serials(atoms, conect, fmt):
    if fmt != "pdb":
        return []
    text_ok = True
    try:
        # Reuse the shared column-aware check rather than re-deriving it.
        import pdb_format
        rebuilt = "".join(a.line() + "\n" for a in atoms)
        pdb_format.check_serials_unique(rebuilt)
    except Exception as e:
        text_ok = False
        return [Finding("serials", "FAIL", f"serial/CONECT integrity: {e}", [])]
    return [] if text_ok else []


def check_conect(atoms, conect, bonds, adj):
    """CONECT against geometry, and against where the atoms actually are.

    The biaryl bug was exactly this: hydrogens were rebuilt onto the
    nearest carbons but CONECT still named the original carbons, so the
    viewer drew four 3.4 A bonds straight across the ring. The distance
    test alone is not enough — a long-but-real C-H at 1.5 A (meeko pads
    some of them) is not the same failure as a hydrogen sitting on the
    wrong carbon. So the sharp test is: does CONECT name the heavy atom
    the hydrogen is actually nearest? If it names a different one, the
    bond is stale whatever its length.

    The distance test is distance, not residue: a cross-residue CONECT at
    bonding distance is a peptide bond or a disulfide, both correct
    chemistry. Only a CONECT claiming a bond where no bond can exist is
    wrong, and an H-involving one is a WARN — hydrogen placement is the
    softest part of any prepared structure.
    """
    if not conect:
        return []
    by_serial = {a.serial: i for i, a in enumerate(atoms)}
    stale, missing, misplaced = [], [], []
    declared = set()
    declared_by_atom = {i: set() for i in range(len(atoms))}
    for serials in conect:
        for k, s in enumerate(serials):
            if s not in by_serial:
                continue
            for s2 in serials[k + 1:]:
                if s2 not in by_serial:
                    continue
                i, j = by_serial[s], by_serial[s2]
                pair = (min(i, j), max(i, j))
                declared.add(pair)
                declared_by_atom[i].add(j)
                declared_by_atom[j].add(i)
                a, b = atoms[i], atoms[j]
                if pair not in bonds:
                    same_res = _same_residue(a, b)
                    has_h = _elem_of(a) == "H" or _elem_of(b) == "H"
                    stale.append((a, b, _dist(a, b),
                                  "FAIL" if (same_res and not has_h) else "WARN"))
    # Missed bonds, but only inside residues that carry CONECT records —
    # a protein's CONECT is usually absent entirely and distance is the
    # only source there.
    con_res = {_reskey(atoms[by_serial[s]]) for serials in conect
               for s in serials if s in by_serial}
    # Bucketed by residue. Scanning every later atom for each atom in a
    # CONECT-bearing residue is O(n^2) on a receptor whose whole protein
    # carries CONECT — 22k atoms took 9 seconds that way, and the inner
    # test almost always failed on the first comparison.
    by_res = {}
    for i, a in enumerate(atoms):
        by_res.setdefault(_reskey(a), []).append(i)
    for rk, idxs in by_res.items():
        if rk not in con_res:
            continue
        for x, i in enumerate(idxs):
            if not _is_heavy(atoms[i]):
                continue
            for j in idxs[x + 1:]:
                if not _is_heavy(atoms[j]):
                    continue
                pair = (i, j)
                if pair in bonds and pair not in declared:
                    missing.append((atoms[i], atoms[j],
                                    _dist(atoms[i], atoms[j])))
    # A hydrogen whose CONECT names a heavy atom that is not the one it is
    # sitting on. This is the failure the distance test cannot see when the
    # stale bond is short, and it is the one that drew bonds across a ring.
    for i, a in enumerate(atoms):
        if _elem_of(a) != "H":
            continue
        partners = [j for j in declared_by_atom[i] if _is_heavy(atoms[j])
                    and _same_residue(a, atoms[j])]
        if not partners:
            continue
        nearest = min(partners, key=lambda j: _dist(a, atoms[j]))
        nearest_d = _dist(a, atoms[nearest])
        others = [(_dist(a, atoms[j]), j) for j in adj[i]
                  if _is_heavy(atoms[j]) and _same_residue(a, atoms[j])
                  and j not in partners]
        if others and others[0][0] < nearest_d - 0.3:
            misplaced.append((a, atoms[nearest], nearest_d,
                              atoms[others[0][1]], others[0][0]))
    findings = []
    if misplaced:
        findings.append(Finding(
            "conect_h", "FAIL",
            f"{len(misplaced)} hydrogen(s) sit on a different heavy atom "
            "than their CONECT names — the bond was not rebuilt with the "
            "hydrogen",
            [f"{a.chain} {a.resn} {a.resi} {a.name}: nearest heavy is "
             f"{b2.name} at {d2:.2f} A, CONECT names {b1.name} at {d1:.2f} A"
             for a, b1, d1, b2, d2 in misplaced[:10]]))
    if stale:
        hard = [s for s in stale if s[3] == "FAIL"]
        soft = [s for s in stale if s[3] == "WARN"]
        if hard:
            findings.append(Finding(
                "conect", "FAIL",
                f"{len(hard)} CONECT bond(s) inside a residue claim a bond "
                "at a distance where no bond exists",
                [f"{a.chain} {a.resn} {a.resi} {a.name}-{b.name}: {d:.2f} A"
                 for a, b, d, _ in hard[:10]]))
        if soft:
            findings.append(Finding(
                "conect", "WARN",
                f"{len(soft)} CONECT bond(s) out of bonding range — stale, "
                "an H-involving long bond, or a metal coordination the "
                "radius table does not cover",
                [f"{a.chain} {a.resn} {a.resi} {a.name}-{b.name}: {d:.2f} A"
                 for a, b, d, _ in soft[:10]]))
    if missing:
        findings.append(Finding(
            "conect", "WARN",
            f"{len(missing)} bonded atom pair(s) in CONECT-bearing residues "
            "have no CONECT record",
            [f"{a.chain} {a.resn} {a.resi} {a.name}-{b.name}: {d:.2f} A"
             for a, b, d in missing[:10]]))
    return findings


def check_hydrogens(atoms, adj):
    """Every hydrogen attached to exactly one heavy atom, at bonding range.

    A hydrogen 3.4 A from the carbon CONECT names is the visible half of
    the bug; the invisible half is that the rebuild placed it on the wrong
    carbon and nothing in the file says so.

    "Attached" means the same distance criterion the bond graph uses, which
    is element-aware: an S-H at 1.37 A is a real bond (sum 1.36 x 1.25) and
    a flat 1.35 A cutoff called six cysteine HG atoms floating on the Vina
    complex. Only same-residue neighbours count — a hydrogen 1.1 A from a
    heavy atom in another residue is a superimposed copy or a contact, and
    both belong to the clash check.
    """
    floating, ambiguous, short = [], [], []
    for i, a in enumerate(atoms):
        if _elem_of(a) != "H":
            continue
        heavy = [(j, _dist(a, atoms[j])) for j in adj[i]
                 if _is_heavy(atoms[j]) and _same_residue(a, atoms[j])]
        heavy.sort(key=lambda t: t[1])
        if not heavy:
            near = min(((_dist(a, b), b) for b in atoms if _is_heavy(b)),
                       default=(99.0, None), key=lambda t: t[0])[0]
            floating.append((a, near))
        elif len(heavy) > 1 and heavy[1][1] <= BOND_TOL * (
                COVALENT.get(_elem_of(a), 0.31)
                + COVALENT.get(_elem_of(atoms[heavy[1][0]]), 0.76)):
            ambiguous.append((a, heavy[0][1], heavy[1][1]))
        elif heavy[0][1] < 0.7:
            short.append((a, heavy[0][1]))
    findings = []
    if floating:
        findings.append(Finding(
            "hydrogen", "FAIL",
            f"{len(floating)} hydrogen(s) not attached to any heavy atom "
            f"(nearest is > {H_MAX} A away)",
            [f"{a.chain} {a.resn} {a.resi} {a.name}: nearest heavy {d:.2f} A"
             for a, d in floating[:10]]))
    if ambiguous:
        findings.append(Finding(
            "hydrogen", "WARN",
            f"{len(ambiguous)} hydrogen(s) equidistant between two heavy atoms",
            [f"{a.chain} {a.resn} {a.resi} {a.name}: {d1:.2f}/{d2:.2f} A"
             for a, d1, d2 in ambiguous[:10]]))
    if short:
        findings.append(Finding(
            "hydrogen", "WARN",
            f"{len(short)} hydrogen(s) closer than 0.7 A to a heavy atom",
            [f"{a.chain} {a.resn} {a.resi} {a.name}: {d:.2f} A"
             for a, d in short[:10]]))
    return findings


def check_valence(atoms, adj):
    """Small-molecule atoms with a chemically impossible connection count.

    The trimmed biaryl left C14 with two bonds and C16 with two: a
    three-carbon fragment hanging off C6, valence-incomplete. It drew
    fine and nothing errored.

    Over-full valence is a FAIL — five connections on a carbon is wrong in
    any file. Under-full is a WARN: it is also what a crystallographic
    ligand with unresolved atoms looks like, and only the reviewer knows
    which one this is.
    """
    over, under = [], []
    for i, a in enumerate(atoms):
        if (a.resn in STANDARD_AA or a.resn in WATERS or a.resn in NUCLEIC
                or a.resn in PSEUDO_RES):
            continue
        e = _elem_of(a)
        if e not in VALENCE_MAX or e == "H":
            continue
        n_heavy = sum(1 for j in adj[i]
                      if _is_heavy(atoms[j]) and _same_residue(a, atoms[j]))
        n_h = sum(1 for j in adj[i]
                  if _elem_of(atoms[j]) == "H" and _same_residue(a, atoms[j]))
        total = n_heavy + n_h
        if total > VALENCE_MAX[e]:
            over.append((a, total, VALENCE_MAX[e]))
        elif total < VALENCE_MIN[e]:
            under.append((a, total, VALENCE_MIN[e], VALENCE_MAX[e]))
    findings = []
    if over:
        findings.append(Finding(
            "valence", "FAIL",
            f"{len(over)} small-molecule atom(s) with more connections than "
            "the element allows — unmerged or duplicated edit",
            [f"{a.chain} {a.resn} {a.resi} {a.name} ({_elem_of(a)}): {t} "
             f"connections, max {hi}" for a, t, hi in over[:10]]))
    if under:
        findings.append(Finding(
            "valence", "WARN",
            f"{len(under)} small-molecule atom(s) with an incomplete "
            "valence — dangling fragment, unmerged edit, a crystallographic "
            "ligand with unresolved atoms, or a ligand split across residue "
            "entries",
            [f"{a.chain} {a.resn} {a.resi} {a.name} ({_elem_of(a)}): {t} "
             f"connections, expected {lo}-{hi}"
             for a, t, lo, hi in under[:10]]))
    return findings


def check_clashes(atoms, bonds, adj):
    """Same-element heavy atoms closer than CLASH_CUTOFF, excluding 1-2 and
    1-3 pairs (bonded, or bonded to a common neighbour — that is what a ring
    is).

    One exception to the exclusion: same-element heavy atoms under 1.0 A are
    flagged even when the distance graph calls them bonded. No real bond is
    that short — the shortest heavy-heavy bond is around 1.1 A — so a pair
    at 0.4 A is two copies of a molecule lying on top of each other, which
    is what ResGen designs grown from one pocket seed look like in a file
    that holds all ten.

    Pseudo-residues are skipped: an fpocket alpha-sphere cloud is dozens of
    overlapping spheres by construction, and calling that a clash on every
    pockets file is noise that teaches the reader to ignore the check.
    """
    # Cell size >= the cutoff: with a 2 A cell a pair at 2.1 A whose atoms
    # straddle a cell edge sits two cells apart and is never compared.
    CELL = CLASH_CUTOFF + 0.05
    cells = {}
    for i, a in enumerate(atoms):
        if not _is_heavy(a):
            continue
        cells.setdefault((int(a.x // CELL), int(a.y // CELL),
                          int(a.z // CELL)), []).append(i)
    clashes = []
    for i, a in enumerate(atoms):
        if not _is_heavy(a) or a.resn in PSEUDO_RES:
            continue
        cx, cy, cz = int(a.x // CELL), int(a.y // CELL), int(a.z // CELL)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cells.get((cx + dx, cy + dy, cz + dz), ()):
                        if j <= i:
                            continue
                        b = atoms[j]
                        if _elem_of(a) != _elem_of(b):
                            continue
                        d = _dist(a, b)
                        if d >= CLASH_CUTOFF:
                            continue
                        if d >= 1.0 and (j in adj[i] or (adj[i] & adj[j])):
                            continue
                        clashes.append((a, b, d))
    if not clashes:
        return []
    return [Finding(
        "clash", "WARN",
        f"{len(clashes)} same-element heavy-atom pair(s) closer than "
        f"{CLASH_CUTOFF} A (1-2 and 1-3 excluded; pairs under 1.0 A flagged "
        "even when bonded — superimposed copies)",
        [f"{a.chain} {a.resn} {a.resi} {a.name} .. {b.chain} {b.resn} "
         f"{b.resi} {b.name}: {d:.2f} A" for a, b, d in clashes[:10]])]


def _chain_residues(atoms):
    """Ordered (reskey, resn) per chain, first-appearance order, deduped."""
    out = {}
    for a in atoms:
        out.setdefault(a.chain, {}).setdefault(_reskey(a), a.resn)
    return {ch: [(k, v) for k, v in d.items()] for ch, d in out.items()}


def _pair_chains(src_chains, out_chains):
    """Source chain -> output chain. Same id first, the rest by residue-name
    sequence similarity, one-to-one, best ratio first.

    Sequence-identical copies — the HA trimer's A/C/E — can in principle
    pair rotated; sorted order breaks the tie, which is right for a
    symmetric trimer and merely approximate for a genuinely swapped chain.
    """
    pairs = {ch: ch for ch in src_chains if ch in out_chains}
    left_src = [c for c in src_chains if c not in pairs]
    left_out = [c for c in out_chains if c not in pairs.values()]
    cand = []
    for s in left_src:
        for o in left_out:
            r = difflib.SequenceMatcher(
                a=[x[1] for x in src_chains[s]],
                b=[x[1] for x in out_chains[o]], autojunk=False).ratio()
            if r >= 0.3:
                cand.append((r, s, o))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_s, used_o = set(), set()
    for r, s, o in cand:
        if s in used_s or o in used_o:
            continue
        used_s.add(s)
        used_o.add(o)
        pairs[s] = o
    return pairs


def _residue_map(src_list, out_list):
    """Source index -> output index for one chain pair.

    Direct by (chain, resi, icode) when the numbering agrees. When it does
    not — Vina and BoltzGen both renumber, and a design's chains start at 1
    — align the residue-name sequences and keep whichever covers more.
    Correspondence is by order, never by residue number.
    """
    out_by_key = {k: i for i, (k, _) in enumerate(out_list)}
    direct = {i: out_by_key[k] for i, (k, _) in enumerate(src_list)
              if k in out_by_key}
    sm = difflib.SequenceMatcher(a=[r for _, r in src_list],
                                 b=[r for _, r in out_list], autojunk=False)
    aligned = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            aligned[blk.a + k] = blk.b + k
    if len(aligned) > len(direct):
        return aligned, True
    return direct, False


def check_source(out_atoms, src_atoms, frozen_chains):
    """The published structure against the structure it came from.

    Every source chain must arrive whole, at its residue count, with no
    interior gaps the source did not have. A chain the crop removed
    entirely is still published — from the full target, not from the crop.

    Two severities, because two different failures look alike here:
    residues missing from *inside* a paired chain is the meeko drop — a
    pocket phenylalanine gone, and a ligand that fits the hole scores
    better — and is a FAIL. A whole source chain unpaired is usually a
    step that carried a subset on purpose (the redock took the receptor,
    not the nine other designs sitting in the same file) and is a WARN for
    the reviewer to judge.
    """
    findings = []
    src_chains = _chain_residues(src_atoms)
    out_chains = _chain_residues(out_atoms)
    pairs = _pair_chains(src_chains, out_chains)

    src_res_atoms = {}
    for a in src_atoms:
        src_res_atoms.setdefault((a.chain, _reskey(a)), {})[a.name] = a
    out_res_atoms = {}
    for a in out_atoms:
        out_res_atoms.setdefault((a.chain, _reskey(a)), {})[a.name] = a

    chain_rmsd = {}
    for sch, slist in src_chains.items():
        if sch not in pairs:
            findings.append(Finding(
                "chain_integrity", "WARN",
                f"source chain {sch} ({len(slist)} residues) has no "
                "counterpart in the published structure — correct if this "
                "step carried a subset on purpose, a loss if it was not "
                "meant to", []))
            continue
        och = pairs[sch]
        olist = out_chains[och]
        rmap, renumbered = _residue_map(slist, olist)
        how = " (aligned by sequence: the numbering differs)" if renumbered else ""

        missing = [i for i in range(len(slist)) if i not in rmap]
        if missing:
            findings.append(Finding(
                "residue_drops", "FAIL",
                f"source chain {sch} -> {och}{how}: {len(missing)} of "
                f"{len(slist)} source residues are missing from the "
                "published structure",
                [f"{sch} {slist[i][1]} {slist[i][0][1]}" for i in missing[:15]]
                + ([f"... and {len(missing) - 15} more"]
                   if len(missing) > 15 else [])))
        covered = set(rmap.values())
        extra = [j for j in range(len(olist)) if j not in covered]
        if extra:
            findings.append(Finding(
                "extra_residues", "WARN",
                f"chain {och}{how}: {len(extra)} residue(s) the source "
                "chain does not have",
                [f"{och} {olist[j][1]} {olist[j][0][1]}" for j in extra[:10]]))

        # Interior gaps: numbering holes inside the published range that the
        # source does not have — the signature of a design-time context crop.
        if not renumbered:
            out_nums = sorted({r[0][1] for r in olist})
            src_nums = {r[0][1] for r in slist}
            if out_nums:
                holes = [n for n in range(out_nums[0], out_nums[-1] + 1)
                         if n not in out_nums and n not in src_nums]
                if holes:
                    findings.append(Finding(
                        "interior_gaps", "WARN",
                        f"chain {och}: {len(holes)} numbering gap(s) inside "
                        "the published range that the source does not have",
                        [f"{och}: missing {n}" for n in holes[:10]]))

        # Per-chain RMSD over atoms matched by name inside aligned residues.
        # Computed for every chain, not just frozen ones: an MD output is
        # supposed to have moved, and the reviewer needs the number to say
        # "moved as much as MD moves things" rather than guessing.
        matched, sq = 0, 0.0
        for i, j in rmap.items():
            sres = src_res_atoms.get((sch, slist[i][0]), {})
            ores = out_res_atoms.get((och, olist[j][0]), {})
            for name, sa in sres.items():
                oa = ores.get(name)
                if oa is None:
                    continue
                matched += 1
                sq += _dist(sa, oa) ** 2
        if matched == 0:
            findings.append(Finding(
                "chain_integrity", "WARN",
                f"chain {sch} -> {och}: no atom matched by "
                "chain/resi/name even after sequence alignment", []))
            continue
        rmsd = math.sqrt(sq / matched)
        chain_rmsd[och] = {"rmsd": round(rmsd, 4),
                           "matched_atoms": matched,
                           "source_chain": sch,
                           "renumbered": renumbered}
        if och in (frozen_chains or []) or sch in (frozen_chains or []):
            if rmsd > 1e-3:
                # key= only: a whole chain shifted by the same vector puts
                # every atom at the same deviation, and comparing Atom
                # objects to break the tie raises TypeError.
                worst = sorted(
                    ((_dist(sa, oa), sa, oa)
                     for i, j in rmap.items()
                     for name, sa in src_res_atoms.get(
                         (sch, slist[i][0]), {}).items()
                     for oa in [out_res_atoms.get((och, olist[j][0]), {}).get(name)]
                     if oa is not None),
                    key=lambda t: t[0], reverse=True)[:5]
                findings.append(Finding(
                    "frozen_chain", "FAIL",
                    f"chain {och} was supposed to be untouched but moved: "
                    f"RMSD {rmsd:.4f} A over {matched} atoms",
                    [f"{oa.chain} {oa.resn} {oa.resi} {oa.name}: {d:.3f} A off"
                     for d, sa, oa in worst]))
    return findings, chain_rmsd


# --------------------------------------------------------------------- main

def verify(structure: Path, source: Path = None, frozen_chains=None):
    atoms, (fmt, *rest) = load_structure(structure)
    conect = rest[0] if rest else []
    findings = []

    if fmt == "pdb":
        findings += check_serials(atoms, conect, fmt)
    findings += check_records(atoms)

    bonds = bond_graph(atoms)
    adj = neighbours(bonds, len(atoms))
    findings += check_conect(atoms, conect, bonds, adj)
    findings += check_hydrogens(atoms, adj)
    findings += check_valence(atoms, adj)
    findings += check_clashes(atoms, bonds, adj)

    src_atoms = None
    chain_rmsd = None
    if source is not None:
        src_atoms, _ = load_structure(source)
        # NOT `findings, chain_rmsd = ...` — that rebinds the name and drops
        # every finding the checks above accumulated. It looked fine in
        # review and the trimmed-fragment case went unreported for one run.
        src_findings, chain_rmsd = check_source(atoms, src_atoms, frozen_chains)
        findings += src_findings

    n_prot = sum(1 for a in atoms if a.resn in STANDARD_AA)
    chains = {}
    for a in atoms:
        chains.setdefault(a.chain, set()).add(_reskey(a))
    chain_str = " ".join(f"{ch}({len(r)})" for ch, r in chains.items())

    return {
        "structure": str(structure),
        "format": fmt,
        "atoms": len(atoms),
        "protein_atoms": n_prot,
        "chains": chain_str,
        "conect_records": len(conect) if fmt == "pdb" else None,
        "chain_rmsd_vs_source": chain_rmsd,
        "findings": [f.__dict__ for f in findings],
        "failed": any(f.severity == "FAIL" for f in findings),
    }


def main(argv):
    args = {"structure": None, "source": None, "frozen": None,
            "json": False, "quiet": False}
    it = iter(argv)
    for a in it:
        if a == "--structure":
            args["structure"] = next(it)
        elif a == "--source":
            args["source"] = next(it)
        elif a == "--frozen-chains":
            args["frozen"] = {c.strip() for c in next(it).split(",") if c.strip()}
        elif a == "--json":
            args["json"] = True
        elif a == "--quiet":
            args["quiet"] = True
        else:
            print(__doc__)
            return 2
    if not args["structure"]:
        print(__doc__)
        return 2

    structure = Path(args["structure"])
    source = Path(args["source"]) if args["source"] else None
    try:
        report = verify(structure, source, args["frozen"])
    except (OSError, ValueError) as e:
        print(f"verify_structure: cannot read {structure}: {e}")
        return 2

    if args["json"]:
        print(json.dumps(report, indent=2))
    else:
        if not args["quiet"]:
            print(f"verify_structure: {structure.name}")
            print(f"  format {report['format']}, {report['atoms']} atoms "
                  f"({report['protein_atoms']} standard-AA), "
                  f"chains {report['chains']}")
            if report["conect_records"] is not None:
                print(f"  CONECT records: {report['conect_records']}")
            if source:
                print(f"  source: {source.name}")
            for ch, r in (report.get("chain_rmsd_vs_source") or {}).items():
                print(f"  chain {ch}: RMSD vs source {r['rmsd']} A "
                      f"over {r['matched_atoms']} matched atoms")
            print()
        for f in report["findings"]:
            print(f"{f['severity']:4s}  {f['check']:16s} {f['message']}")
            for d in f["details"]:
                print(f"       {d}")
        if not report["findings"]:
            print("PASS  no findings")
    n_fail = sum(1 for f in report["findings"] if f["severity"] == "FAIL")
    n_warn = sum(1 for f in report["findings"] if f["severity"] == "WARN")
    if not args["json"]:
        print(f"\nRESULT: {'FAIL' if report['failed'] else 'PASS'} "
              f"({n_fail} fail, {n_warn} warn)")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
