#!/usr/bin/env python3
"""Turn fpocket's alpha-sphere vertices into a structure the viewer can draw.

fpocket reports each pocket as a cloud of Voronoi vertices with radii
(`pocketN_vert.pqr`). That cloud *is* the pocket's shape, but it is not a
structure — the viewer has nothing to render. This script writes the
vertices as a residue of dummy atoms in their own chain, so the viewer can
draw them as spheres and the cavity becomes visible.

Usage:
  python3 pocket_cloud.py --pockets pockets_out/pockets.json \
      --receptor 7XWQ.cif --out pockets_vis.pdb --top 3

Exit codes: 0 ok, 1 no pockets, 2 bad input.
"""

import argparse
import json
import sys
from pathlib import Path

import gemmi

# One chain per pocket, named so the viewer's chain list reads in rank
# order. fpocket's own IDs are by size, so the rank is what matters here.
CHAIN_NAMES = "pqrstuvwxyzabcdefghijklmno"


def read_vertices(pqr: Path):
    """(x, y, z, radius) for each alpha sphere in a vert.pqr.

    PQR is PDB's fixed columns with charge and radius appended, but fpocket
    writes them space-separated rather than in fixed columns, so the radius
    is the last whitespace field — not a column slice.
    """
    out = []
    for line in pqr.read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            x, y, z = (float(parts[5]), float(parts[6]), float(parts[7]))
            r = float(parts[9])
        except ValueError:
            continue
        out.append((x, y, z, r))
    return out


def pdb_line(serial: int, chain: str, x: float, y: float, z: float,
             b: float) -> str:
    """One HETATM line, built by column so nothing drifts.

    PDB is fixed-column and a format string that is off by one silently
    puts the chain ID in the residue-number field, which makes the whole
    chain unreadable to parsers.
    """
    cols = [" "] * 80

    def put(s: str, at: int) -> None:
        for k, ch in enumerate(s):
            cols[at + k] = ch

    put("HETATM", 0)
    put(f"{serial:5d}", 6)
    put(" C  ", 12)
    put("STP", 17)
    put(chain, 21)
    put(f"{1:4d}", 22)
    put(f"{x:8.3f}", 30)
    put(f"{y:8.3f}", 38)
    put(f"{z:8.3f}", 46)
    put(f"{1.0:6.2f}", 54)
    put(f"{b:6.2f}", 60)
    put(" C", 76)
    return "".join(cols).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pockets", required=True,
                    help="pockets.json from detect_pockets.py")
    ap.add_argument("--receptor", help="receptor to draw the pockets in")
    ap.add_argument("--out", required=True, help="output PDB path")
    ap.add_argument("--top", type=int, default=3,
                    help="how many ranked pockets to include")
    ap.add_argument("--scale", type=float, default=0.45,
                    help="sphere radius multiplier; the raw alpha-sphere "
                         "radii overlap heavily at 1.0")
    args = ap.parse_args()

    data = json.loads(Path(args.pockets).read_text())
    pockets = data.get("pockets", [])[:args.top]
    if not pockets:
        print("error: no pockets in the input", file=sys.stderr)
        return 1

    if args.receptor:
        st = gemmi.read_structure(args.receptor)
        st.setup_entities()
        st.remove_ligands_and_waters()
        st.remove_alternative_conformations()
    else:
        st = gemmi.Structure()
        st.spacegroup_hm = "P 1"
        st.add_model(gemmi.Model("1"))

    model = st[0]
    pocket_lines = []
    for rank, p in enumerate(pockets):
        pqr = p.get("atoms_pdb")
        vert = Path(str(pqr).replace("_atm.pdb", "_vert.pqr"))
        if not vert.is_file():
            print(f"warning: pocket {p['cav_id']} has no vertex file",
                  file=sys.stderr)
            continue
        verts = read_vertices(vert)
        if not verts:
            continue

        name = CHAIN_NAMES[rank % len(CHAIN_NAMES)]
        for i, (x, y, z, r) in enumerate(verts, 1):
            # B-factor carries the sphere radius: the viewer scales a
            # sphere render by it, and there is nowhere else to put a
            # per-atom number in PDB.
            pocket_lines.append(
                pdb_line(i, name, x, y, z, r * args.scale * 10)
            )

    if not pocket_lines:
        print("error: no vertex data in any pocket", file=sys.stderr)
        return 1

    # Append the pocket atoms to the receptor's PDB text rather than
    # building them as gemmi chains: setup_entities() drops hand-built
    # chains when writing mmCIF, and re-running it after adding them
    # renumbers the receptor's own chains.
    if args.receptor:
        rec = gemmi.read_structure(args.receptor)
        rec.setup_entities()
        rec.remove_ligands_and_waters()
        rec.remove_alternative_conformations()
        rec.write_pdb(args.out)
        # A PDB parser stops at the first END, so the receptor's own must
        # go and a single one be written after the pocket atoms.
        lines = [l for l in Path(args.out).read_text().splitlines()
                 if l.strip() != "END"]
        lines.extend(pocket_lines)
        lines.append("END")
        Path(args.out).write_text("\n".join(lines) + "\n")
    else:
        Path(args.out).write_text("\n".join(pocket_lines) + "\nEND\n")

    print(f"wrote {args.out} with {min(len(pockets), len(CHAIN_NAMES))} "
          f"pocket clouds ({len(pocket_lines)} spheres)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
