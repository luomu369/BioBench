#!/usr/bin/env python3
"""Extract the residues the user selected in the viewer into a structure file.

Foldseek searches whole chains — it has no "only this stretch of residues"
option. To ask "what known protein looks like this bit?", the selected
residues have to be written out as a structure of their own and used as
the query.

Reads webapp/static/selection.json (written by the viewer) and pulls the
matching residues out of the published structure.

Usage:
  python3 extract_selection.py --out fragment.cif
  python3 extract_selection.py --out fragment.cif --context 2

Exit codes: 0 ok, 1 nothing selected, 2 structure unreadable,
3 no residues matched in the structure.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import gemmi

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import viewer_static

SELECTION = Path(os.environ.get(
    "BIOBENCH_SELECTION",
    str(viewer_static() / "selection.json")))
CURRENT = viewer_static() / "current.cif"
STRUCTURE = Path(os.environ.get(
    "BIOBENCH_STRUCTURE",
    str(CURRENT)))


def load_selection():
    if not SELECTION.is_file():
        print("error: selection.json not found — the user has not selected "
              "any residues yet", file=sys.stderr)
        sys.exit(1)
    data = json.loads(SELECTION.read_text())
    if not data.get("count") or not data.get("residues"):
        print("error: selection is empty — ask the user to click residues "
              "in the viewer", file=sys.stderr)
        sys.exit(1)
    return data


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output .cif or .pdb")
    ap.add_argument("--context", type=int, default=0,
                    help="also include N residues either side of each "
                         "selected stretch (default 0)")
    ap.add_argument("--selection", default=str(SELECTION))
    ap.add_argument("--structure", default=str(STRUCTURE))
    args = ap.parse_args()

    sel = load_selection()
    wanted = {}
    for r in sel["residues"]:
        wanted.setdefault(r["chain"], set()).add(int(r["resi"]))

    src = Path(args.structure)
    if not src.is_file():
        print(f"error: structure not found: {src}", file=sys.stderr)
        return 2

    # The viewer's current.cif holds whatever was published, which is often
    # PDB despite the name — read the format from the metadata and let
    # gemmi pick the right parser.
    fmt = None
    meta_path = src.with_name("current.json")
    if meta_path.is_file():
        try:
            fmt = json.loads(meta_path.read_text()).get("format")
        except json.JSONDecodeError:
            pass
    if fmt == "pdb":
        st = gemmi.read_structure(str(src), format=gemmi.CoorFormat.Pdb)
    else:
        st = gemmi.read_structure(str(src))
    st.setup_entities()

    # Build the fragment: one chain per source chain that had a selection,
    # carrying only the wanted residues (plus optional context).
    out = gemmi.Structure()
    out.name = f"{sel.get('structure', 'selection')}_fragment"
    out.spacegroup_hm = "P 1"
    out.cell = st.cell
    out.add_model(gemmi.Model("1"))

    kept_total = 0
    for model in st:
        for ch in model:
            if ch.name not in wanted:
                continue
            want = wanted[ch.name]

            # Residues are ordered in the file; expand each wanted number
            # by --context so a short motif keeps its flanking residues.
            if args.context:
                seq = [r.seqid.num for r in ch]
                expanded = set(want)
                for n in want:
                    for k in range(1, args.context + 1):
                        if n - k in seq:
                            expanded.add(n - k)
                        if n + k in seq:
                            expanded.add(n + k)
                want = expanded

            new_ch = gemmi.Chain(ch.name)
            for res in ch:
                if res.seqid.num in want:
                    new_ch.add_residue(res)
                    kept_total += 1
            if len(new_ch):
                out[0].add_chain(new_ch)

    if kept_total == 0:
        print("error: none of the selected residues were found in "
              f"{src} — the selection may be stale", file=sys.stderr)
        return 3

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix in (".pdb", ".ent"):
        out.write_pdb(str(dst))
    else:
        out.make_mmcif_document().write_file(str(dst))

    chains = ", ".join(f"{c.name}({len(c)})" for c in out[0])
    print(f"wrote {dst} — {kept_total} residues: {chains}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
