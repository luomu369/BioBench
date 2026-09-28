"""Read and write PDB ATOM/HETATM records in one place.

PDB is fixed-column, and every boundary is implicit in an f-string or a
slice. Nothing checks them: a format string with one space too many shifts
the residue name into the atom-name field, a residue number above 9999
overflows its four columns and pushes the chain ID into the residue name, a
serial that restarts at 1 in a file appended to another collides with the
first file's atoms. None of these raise — the file writes and a parser reads
a different molecule from the one that was meant.

Writing goes through `atom_line`, which refuses input that would not fit.
Reading goes through `parse_atom_line`, which returns fields by name so no
caller has to know where they live.

Usage:
    from pdb_format import atom_line, parse_atom_line, next_serial

    line = atom_line(serial=4075, name="C1", resn="UNL", chain="L",
                     resi=1, x=1.0, y=2.0, z=3.0, elem="C")
    atom = parse_atom_line(line)
"""
from dataclasses import dataclass
from typing import Iterator, Optional

# The columns, as (start, end) in 0-based half-open slices. PDB documentation
# numbers columns from 1, so column 13-16 is [12:16] here.
COL = {
    "record": (0, 6),
    "serial": (6, 11),
    "name": (12, 16),
    "altLoc": (16, 17),
    "resn": (17, 20),
    "chain": (21, 22),
    "resi": (22, 26),
    "x": (30, 38),
    "y": (38, 46),
    "z": (46, 54),
    "elem": (76, 78),
}

# Widest value each column can hold. A number that does not fit is the failure
# this module exists to catch: writing it anyway shifts every column after it.
MAX_SERIAL = 99999
MAX_RESI = 9999


class PdbFormatError(ValueError):
    """A field does not fit its column, and writing it would corrupt the line."""


@dataclass
class Atom:
    """One ATOM/HETATM record, by field name rather than by column."""

    serial: int
    name: str
    resn: str
    chain: str
    resi: int
    x: float
    y: float
    z: float
    elem: str
    icode: str = " "
    record: str = "HETATM"

    def line(self) -> str:
        return atom_line(self.serial, self.name, self.resn, self.chain,
                         self.resi, self.x, self.y, self.z, self.elem,
                         record=self.record, icode=self.icode)


def _fit(value: int, width: int, what: str) -> str:
    """Format an integer into exactly `width` columns, or refuse.

    The refusal is the point. `f"{resi:4d}"` on 43002 produces five characters
    where four were expected, and every column after it moves one to the
    right — the chain ID lands in the residue name, the coordinates in the
    chain field, the element in the occupancy. Nothing complains. Raising
    here turns a silent corruption into a message that names the field.
    """
    if value < 0:
        raise PdbFormatError(f"{what} is negative: {value}")
    if value >= 10 ** width:
        raise PdbFormatError(
            f"{what} {value} does not fit in {width} columns "
            f"(max {10 ** width - 1}). Renumber it, or carry it outside the "
            f"PDB — writing it anyway shifts every column after it.")
    return f"{value:{width}d}"


def _fit_icode(icode: str) -> str:
    """One insertion-code column, or a blank.

    An insertion code is what makes ``LYS H  49A`` a different residue from
    ``LYS H  49``. Dropping it merges the two: no atom goes missing, so a
    coordinate comparison sees nothing wrong, while the file now holds one
    residue carrying two atom sets. A two-character code is refused for the
    same reason an over-wide number is — it shifts every column after it.
    """
    icode = icode or " "
    if len(icode) > 1:
        raise PdbFormatError(
            f"insertion code {icode!r} is longer than 1 column")
    return icode


def atom_line(serial: int, name: str, resn: str, chain: str, resi: int,
              x: float, y: float, z: float, elem: str,
              record: str = "HETATM", icode: str = " ") -> str:
    """One ATOM/HETATM line, with every column where PDB puts it.

    `serial` continues across a file. A ligand appended to a receptor must
    start at the receptor's last serial plus one, not at 1 — CONECT records
    name atoms by serial, so a restart makes the ligand's bonds point at the
    receptor's first atoms.
    """
    if record not in ("ATOM", "HETATM"):
        raise PdbFormatError(f"record must be ATOM or HETATM, got {record!r}")
    # The record name occupies columns 1-6, so ATOM is written with two
    # trailing spaces. Left at four characters it shifts every column after
    # it by two.
    record = record.ljust(6)

    # The atom name is right-justified in its four columns for elements with a
    # one-letter symbol, and starts at column 13 for the rest.
    name = name.strip()
    if len(name) > 4:
        raise PdbFormatError(f"atom name {name!r} is longer than 4 columns")
    if len(elem.strip()) == 1 and len(name) < 4:
        name_field = f" {name:<3s}"
    else:
        name_field = f"{name:<4s}"

    resn = resn.strip()
    if len(resn) > 3:
        raise PdbFormatError(f"residue name {resn!r} is longer than 3 columns")

    chain = (chain or " ")[:1]
    elem = elem.strip()[:2].rjust(2)

    return (
        f"{record}"
        f"{_fit(serial, 5, 'serial')}"
        f" "
        f"{name_field}"
        f" "                                  # altLoc
        f"{resn:>3s}"
        f" "
        f"{chain}"
        f"{_fit(resi, 4, 'residue number')}"
        f"{_fit_icode(icode)}"
        f"   "                                # 3 spaces to the coordinates
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"{'  1.00':>6s}{'  0.00':>6s}"
        f"          "
        f"{elem:>2s}"
        f"  "                                  # charge, columns 79-80
    )


def parse_atom_line(line: str) -> Optional[Atom]:
    """One ATOM/HETATM line as an `Atom`, or None if it is not one.

    Fields are returned by name. A caller that wants the residue name writes
    `atom.resn`; it never writes `line[17:20]`, so it cannot be off by one.
    """
    if not line.startswith(("ATOM", "HETATM")):
        return None
    try:
        return Atom(
            record=line[0:6].strip(),
            serial=int(line[6:11]),
            name=line[12:16].strip(),
            resn=line[17:20].strip(),
            chain=line[21],
            resi=int(line[22:26]),
            x=float(line[30:38]),
            y=float(line[38:46]),
            z=float(line[46:54]),
            elem=line[76:78].strip(),
            icode=line[26] if len(line) > 26 else " ",
        )
    except (ValueError, IndexError):
        # A truncated or malformed line. Returning None lets a caller skip it
        # rather than abort a whole file over one bad record.
        return None


def iter_atoms(text: str) -> Iterator[Atom]:
    """Every ATOM/HETATM record in a PDB file, in file order."""
    for line in text.splitlines():
        atom = parse_atom_line(line)
        if atom is not None:
            yield atom


def next_serial(text: str) -> int:
    """The serial a new atom should take to continue this file's numbering.

    This is what keeps a ligand appended to a receptor from colliding with it.
    Pass the receptor's text, get back the serial after its last atom.
    """
    last = 0
    for atom in iter_atoms(text):
        if atom.serial > last:
            last = atom.serial
    return last + 1


def conect_line(a: int, b: int) -> str:
    """One CONECT record naming two atoms by serial.

    Both serials must be the ones the atoms carry in the file this record goes
    into. A CONECT written with a ligand's local numbering, in a file where
    that ligand starts at 4075, bonds the ligand to whatever sits at serial 1.
    """
    return f"CONECT{_fit(a, 5, 'CONECT serial')}{_fit(b, 5, 'CONECT serial')}"


def check_serials_unique(text: str) -> None:
    """Raise if two atoms share a serial, or a CONECT names one that is absent.

    Worth calling on a file that was assembled from parts. A duplicate serial
    is invisible until a viewer resolves a bond to the wrong atom, and a
    CONECT pointing at nothing is a bond that silently does not exist.
    """
    serials = {}
    for atom in iter_atoms(text):
        serials.setdefault(atom.serial, []).append(atom)
    dupes = {s: a for s, a in serials.items() if len(a) > 1}
    if dupes:
        detail = ", ".join(
            f"{s} ({a[0].resn} {a[0].resi} {a[0].name})" for s, a in dupes.items())
        raise PdbFormatError(f"duplicate atom serials: {detail}")

    known = set(serials)
    dangling = []
    for line in text.splitlines():
        if not line.startswith("CONECT"):
            continue
        for field in (line[6:11], line[11:16]):
            if not field.strip():
                continue
            s = int(field)
            if s not in known:
                dangling.append(s)
    if dangling:
        raise PdbFormatError(
            f"CONECT records name {len(dangling)} serial(s) with no atom: "
            f"{sorted(set(dangling))[:10]}")


if __name__ == "__main__":
    # Self-check: the column layout this module claims, against a line it
    # wrote itself. Run it after any edit here.
    line = atom_line(4075, "C1", "UNL", "L", 1, 1.5, -2.25, 3.0, "C")
    assert len(line) == 80, f"line is {len(line)} columns, expected 80"
    a = parse_atom_line(line)
    assert a is not None
    assert (a.serial, a.name, a.resn, a.chain, a.resi) == (4075, "C1", "UNL", "L", 1)
    assert (a.x, a.y, a.z) == (1.5, -2.25, 3.0)
    assert a.elem == "C"

    # A residue number that does not fit must be refused, not written shifted.
    try:
        atom_line(1, "C", "UNL", "L", 43002, 0, 0, 0, "C")
    except PdbFormatError as e:
        assert "does not fit" in str(e)
    else:
        raise AssertionError("a 5-digit residue number was accepted")
    print("pdb_format: ok")

    # A serial that restarts must be caught by the uniqueness check.
    good = atom_line(1, "C", "UNL", "L", 1, 0, 0, 0, "C")
    try:
        check_serials_unique(good + "\n" + good)
    except PdbFormatError as e:
        assert "duplicate" in str(e)
    else:
        raise AssertionError("duplicate serials were accepted")

    print("pdb_format: ok")
