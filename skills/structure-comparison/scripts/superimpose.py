#!/usr/bin/env python3
"""Superimpose a hit structure onto a query using a Foldseek alignment.

Foldseek reports the rotation matrix (u) and translation vector (t) that
map a target onto its query. Applying them to the target's atoms puts both
structures in one coordinate system, so they can be published together and
compared visually instead of one at a time.

Usage:
  python3 superimpose.py --query 7XWQ.cif --target hit.cif \
      --out superposed.pdb --query-chain B

  # with the alignment taken from an existing .m8 that carries u,t
  python3 superimpose.py --query q.cif --target t.cif --m8 result.m8 \
      --out superposed.pdb

Exit codes: 0 ok, 1 no alignment found, 2 unreadable input.
"""

import argparse
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import gemmi

FOLDSEEK = Path("/home/asus_gx10/tools/foldseek/bin/foldseek")


def read_structure(path: Path) -> gemmi.Structure:
    """Read PDB or mmCIF, honouring the viewer's format metadata."""
    fmt = None
    meta = path.with_name("current.json")
    if meta.is_file():
        try:
            fmt = __import__("json").loads(meta.read_text()).get("format")
        except Exception:
            pass
    if fmt == "pdb":
        return gemmi.read_structure(str(path), format=gemmi.CoorFormat.Pdb)
    return gemmi.read_structure(str(path))


def chain_matches(name: str, chain: str | None) -> bool:
    """Does a foldseek query name refer to this chain?

    Foldseek names queries like `7XWQ_B` (file stem + chain), so match on
    the trailing chain segment rather than a prefix.
    """
    if not chain:
        return True
    return name == chain or name.endswith("_" + chain) or name.endswith("." + chain)


def parse_ut(u_field: str, t_field: str):
    """Split foldseek's u and t columns into a 9-element rotation + 3-vector.

    They are two separate columns in the .m8, not one comma-joined field.
    """
    u = [float(x) for x in u_field.split(",")]
    t = [float(x) for x in t_field.split(",")]
    if len(u) != 9:
        raise ValueError(f"u: expected 9 values, got {len(u)}")
    if len(t) != 3:
        raise ValueError(f"t: expected 3 values, got {len(t)}")
    return u, t


def find_alignment(query: Path, target: Path, query_chain: str | None,
                   m8: Path | None):
    """Return (u, t, target_chain, pident, alnlen, evalue) for the best hit."""
    if m8 is not None:
        rows = []
        for line in m8.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            if not chain_matches(parts[0], query_chain):
                continue
            rows.append(parts)
        if not rows:
            return None
        # best hit = lowest e-value
        rows.sort(key=lambda p: float(p[4]))
        best = rows[0]
        u, t = parse_ut(best[5], best[6])
        return u, t, best[1], float(best[2]), int(float(best[3])), float(best[4])

    # No .m8 given — run foldseek ourselves, asking for u and t.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "sup.m8"
        tmp = td / "tmp"
        r = subprocess.run(
            [str(FOLDSEEK), "easy-search", str(query), str(target), str(out),
             str(tmp), "--format-output",
             "query,target,pident,alnlen,evalue,u,t"],
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not out.is_file():
            print("error: foldseek search failed\n"
                  f"{(r.stdout + r.stderr)[-400:]}", file=sys.stderr)
            sys.exit(1)
        rows = []
        for line in out.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            if not chain_matches(parts[0], query_chain):
                continue
            rows.append(parts)
        if not rows:
            return None
        rows.sort(key=lambda p: float(p[4]))
        best = rows[0]
        u, t = parse_ut(best[5], best[6])
        return u, t, best[1], float(best[2]), int(float(best[3])), float(best[4])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", required=True, help="reference structure")
    ap.add_argument("--target", required=True, help="structure to move")
    ap.add_argument("--out", required=True, help="output .pdb or .cif")
    ap.add_argument("--query-chain",
                    help="only align on this query chain (e.g. B)")
    ap.add_argument("--m8", help="reuse an existing .m8 with u,t columns")
    args = ap.parse_args()

    q = Path(args.query)
    t = Path(args.target)
    for p in (q, t):
        if not p.is_file():
            print(f"error: not found: {p}", file=sys.stderr)
            return 2

    found = find_alignment(q, t, args.query_chain,
                           Path(args.m8) if args.m8 else None)
    if found is None:
        print("error: no alignment between query and target — nothing to "
              "superimpose", file=sys.stderr)
        return 1
    u, t_vec, target_name, pident, alnlen, evalue = found
    if u is None:
        print("error: the .m8 has no u,t columns — rerun foldseek with "
              "--format-output including u,t", file=sys.stderr)
        return 1

    ref = read_structure(q)
    mov = read_structure(t)

    out = gemmi.Structure()
    out.name = f"{q.stem}_vs_{t.stem}"
    out.spacegroup_hm = "P 1"
    out.cell = ref.cell
    out.add_model(gemmi.Model("1"))

    # Chain IDs carry the origin: the query keeps its own IDs, the target's
    # get an "h" prefix (hit). Without this the two structures are
    # indistinguishable once merged — the viewer shows one flat list of
    # chains and the user cannot tell which came from where.
    for ch in ref[0]:
        out[0].add_chain(ch)

    used = {c.name for c in ref[0]}
    mapping = {}
    for ch in mov[0]:
        new_name = f"h{ch.name}"
        while new_name in used:
            new_name = "h" + new_name
        used.add(new_name)
        mapping[ch.name] = new_name
        new = gemmi.Chain(new_name)
        for res in ch:
            for atom in res:
                x, y, z = atom.pos.x, atom.pos.y, atom.pos.z
                atom.pos = gemmi.Position(
                    u[0]*x + u[1]*y + u[2]*z + t_vec[0],
                    u[3]*x + u[4]*y + u[5]*z + t_vec[1],
                    u[6]*x + u[7]*y + u[8]*z + t_vec[2],
                )
            new.add_residue(res)
        out[0].add_chain(new)

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix in (".pdb", ".ent"):
        out.write_pdb(str(dst))
    else:
        out.make_mmcif_document().write_file(str(dst))

    # CA RMSD over the aligned region, as a sanity check on the fit.
    q_chain = args.query_chain or (ref[0][0].name if len(ref[0]) else None)
    rmsd = None
    if q_chain and q_chain in [c.name for c in ref[0]]:
        t_name = target_name.split("-")[0].split("_")[-1]
        t_chain = None
        for c in mov[0]:
            if c.name == t_name or target_name.startswith(c.name):
                t_chain = c
                break
        if t_chain is not None:
            ca_q = [a.pos for r in ref[0][q_chain] for a in r if a.name == "CA"]
            ca_t = [a.pos for r in t_chain for a in r if a.name == "CA"]
            n = min(len(ca_q), len(ca_t))
            if n:
                ss = sum((ca_q[i].x-ca_t[i].x)**2 + (ca_q[i].y-ca_t[i].y)**2
                         + (ca_q[i].z-ca_t[i].z)**2 for i in range(n))
                rmsd = math.sqrt(ss / n)

    print(f"wrote {dst}")
    print(f"  alignment: {pident:.1f}% id, {alnlen} res, e-value {evalue:.2e}")
    if rmsd is not None:
        print(f"  CA RMSD:   {rmsd:.2f} A")
    print(f"  query  chains (unchanged IDs): {[c.name for c in ref[0]]}")
    print(f"  target chains (prefixed 'h'):  {mapping}")
    print(f"  → in the viewer, plain IDs are {q.stem}, 'h' IDs are {t.stem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
