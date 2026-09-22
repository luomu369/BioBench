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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", help="only residues on this chain")
    ap.add_argument("--format",
                    choices=["text", "csv", "boltzgen", "mpnn"],
                    default="text")
    ap.add_argument("--pdb-name", default="backbone.pdb",
                    help="PDB filename for --format mpnn")
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
    else:
        print(f"structure: {data.get('structure')}  "
              f"count: {data.get('count')}  "
              f"updated: {data.get('updated')}")
        for r in residues:
            print(f"  {r['chain']}:{r['resi']} {r['resn']}")


if __name__ == "__main__":
    main()
