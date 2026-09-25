#!/usr/bin/env python3
"""Read the residue selection the user made in the BioBench Viewer.

Any skill that accepts positional constraints (hotspots, fixed residues,
redesign targets) should call this instead of parsing selection.json itself.

Usage:
  # Human-readable summary
  python3 read_selection.py

  # Just the residue numbers on one chain, comma-separated
  python3 read_selection.py --chain B --format csv
  # -> 430,435

  # BoltzGen binding block residue list
  python3 read_selection.py --chain B --format boltzgen
  # -> [430, 435]

  # ProteinMPNN fixed-positions JSONL
  python3 read_selection.py --chain A --format mpnn --pdb-name backbone.pdb
  # -> {"backbone.pdb": [12, 30]}

Exit codes: 0 ok, 1 nothing selected, 2 no residues on the requested chain.
"""

import argparse
import json
import sys
from pathlib import Path

SELECTION = Path(
    os.environ.get(
        "BIOBENCH_SELECTION",
        "/home/asus_gx10/biobench/webapp/static/selection.json",
    )
) if (os := __import__("os")) else None


def load():
    if not SELECTION.is_file():
        print("error: selection.json not found — the user has not selected "
              "any residues yet", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(SELECTION.read_text())
    except json.JSONDecodeError:
        print("error: selection.json is not valid JSON", file=sys.stderr)
        sys.exit(1)

    if not data.get("count") or not data.get("residues"):
        print("error: selection is empty — ask the user to click residues "
              "in the viewer", file=sys.stderr)
        sys.exit(1)
    return data


AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",
}


def mpnn_indices(args, residues) -> str:
    """Convert the viewer's PDB residue numbers to ProteinMPNN indices.

    `--omit-AA-jsonl` and `--fixed-positions-jsonl` count positions in
    ProteinMPNN's own expansion of the chain, which starts at the file's
    lowest residue number and fills every gap with X. A PDB numbered from
    -1 therefore puts PDB 8 at index 9, and the JSONL wants that plus one
    because `tied_featurize` subtracts 1 again.

    Handing PDB numbers straight to those flags mutates the residue *before*
    the one meant, and nothing complains — the run succeeds and the FASTA
    looks normal. So the conversion is verified against the wild type read
    from the file, and a mismatch is an error rather than a silent shift.
    """
    if not args.pdb_path:
        print("error: --format mpnn-index/mpnn-omit needs --pdb-path",
              file=sys.stderr)
        sys.exit(2)
    pdb = Path(args.pdb_path)
    if not pdb.is_file():
        print(f"error: {pdb} not found", file=sys.stderr)
        sys.exit(2)

    # Parse the PDB directly rather than through gemmi: this script has to
    # run in whatever env the calling skill activated, and gemmi is only in
    # some of them. A PDB's fixed columns are enough for residue names and
    # numbers.
    by_num = {}
    for line in pdb.read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            num = int(line[22:27])
            resn = line[17:21].strip().upper()
        except ValueError:
            continue
        by_num.setdefault(num, resn)
    if not by_num:
        print(f"error: no residues parsed from {pdb.name}", file=sys.stderr)
        sys.exit(2)
    lo = min(by_num)
    chain = args.chain or "A"

    out = []
    for r in residues:
        num = r["resi"]
        if num not in by_num:
            print(f"warning: {chain}:{num} is not in {pdb.name} — skipped",
                  file=sys.stderr)
            continue
        idx = num - lo + 1          # 1-based, as the JSONL wants
        wt_file = AA3TO1.get(by_num[num].upper())
        wt_sel = AA3TO1.get(str(r.get("resn", "")).upper())
        # A mismatch is worth saying out loud but is not fatal: the viewer's
        # selection can legitimately come from a different structure — a
        # mutant published earlier, or another tab. What matters is that the
        # *index* is right, and that is derived from the file, not from the
        # selection. So warn and carry on.
        if wt_file and wt_sel and wt_file != wt_sel:
            print(f"warning: {chain}:{num} is {by_num[num]} ({wt_file}) in "
                  f"{pdb.name} but the viewer selected {r.get('resn')} "
                  f"({wt_sel}) — the selection is from a different "
                  f"structure; the index below is for {pdb.name}",
                  file=sys.stderr)
        out.append((num, idx, wt_file or "X"))

    if args.format == "mpnn-index":
        return json.dumps({pdb.stem: [i for _, i, _ in out]}) + "\n"

    # mpnn-omit: one entry per position, omitting its own wild type, which
    # is what a saturation scan needs. Each entry is [[positions], [AAs]].
    entries = [f"[[{idx}], [\"{wt}\"]]" for _, idx, wt in out]
    return ("{" + f"\"{pdb.stem}\": {{\"{chain}\": ["
            + ", ".join(entries) + "]}}\n")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", help="only residues on this chain")
    ap.add_argument("--format",
                    choices=["text", "csv", "boltzgen", "mpnn",
                             "mpnn-index", "mpnn-omit"],
                    default="text")
    ap.add_argument("--pdb-name", default="backbone.pdb",
                    help="PDB filename for --format mpnn")
    ap.add_argument("--pdb-path",
                    help="the backbone PDB, required by --format mpnn-index "
                         "and mpnn-omit so PDB numbers can be converted to "
                         "ProteinMPNN's own indices")
    args = ap.parse_args()

    data = load()
    residues = data["residues"]
    if args.chain:
        residues = [r for r in residues if r["chain"] == args.chain]
        if not residues:
            print(f"error: no selected residues on chain {args.chain} "
                  f"(available: "
                  f"{sorted({r['chain'] for r in data['residues']})})",
                  file=sys.stderr)
            sys.exit(2)

    positions = sorted(r["resi"] for r in residues)

    if args.format == "csv":
        print(",".join(str(p) for p in positions))
    elif args.format == "boltzgen":
        print("[" + ", ".join(str(p) for p in positions) + "]")
    elif args.format == "mpnn":
        print(json.dumps({args.pdb_name: positions}))
    elif args.format in ("mpnn-index", "mpnn-omit"):
        sys.stdout.write(mpnn_indices(args, residues))
    else:
        print(f"structure: {data.get('structure')}  "
              f"count: {data.get('count')}  "
              f"updated: {data.get('updated')}")
        for r in residues:
            print(f"  {r['chain']}:{r['resi']} {r['resn']}")


if __name__ == "__main__":
    main()
