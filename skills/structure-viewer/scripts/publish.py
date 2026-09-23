#!/usr/bin/env python3
"""Publish a structure and/or progress to the BioBench Viewer.

Generic — any skill can call this. It knows nothing about BoltzGen, ESMFold,
or any specific model; it only writes the viewer's contract files.

Usage:
  # Report progress
  python3 publish.py --status --state running --step design \
      --index 1 --total 6 --message "Diffusion sampling"

  # Publish a structure (chains and metrics are read from the file)
  python3 publish.py --structure path/to/design.cif --name my_design \
      --source boltzgen --notes "Top-ranked of 4"

  # Both at once
  python3 publish.py --structure d.cif --status --state done \
      --step filtering --index 6 --total 6 --message "Published"

Exit codes: 0 ok, 1 bad input, 2 structure file unreadable.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

VIEWER = Path(os.environ.get(
    "BIOBENCH_VIEWER",
    "/home/asus_gx10/biobench/webapp/static"
))

AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M", "SEC": "U", "PYL": "O", "HYP": "P",
}


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so the viewer never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text)
    os.replace(tmp, path)


def sniff_format(text: str) -> str:
    """Decide PDB vs mmCIF from the file's first meaningful line.

    The viewer's contract file is always named current.cif, so the source
    suffix says nothing about the content — a .pdb input lands there too.
    Trusting the suffix made parse_chains run the mmCIF branch over PDB
    text and return zero chains, which left the viewer with no chain
    information at all.
    """
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("data_", "_atom_site.", "loop_")):
            return "cif"
        if s.startswith(("HEADER", "CRYST1", "ATOM", "HETATM", "MODEL",
                         "REMARK", "SEQRES", "DBREF")):
            return "pdb"
    return "pdb"


def parse_chains(path: Path) -> list[dict]:
    """Extract per-chain residue counts from a PDB or mmCIF file.

    Both amino-acid residues and HETATM ligands are counted. A chain whose
    residues are mostly non-amino-acid gets ``"ligand": true`` so the viewer
    can style it differently.

    The test is "mostly", not "any": mmCIF files routinely put a small
    molecule in the same auth_asym_id as the protein it binds (7xwr has
    its ligand I2B inside chains A and B). Marking such a chain as a
    ligand would restyle the whole protein.
    """
    residues: dict[str, dict[str, str]] = {}
    ligand_counts: dict[str, int] = {}
    text = path.read_text(errors="replace")
    is_cif = sniff_format(text) == "cif"

    def note(chain: str, resi: str, one: str | None, is_ligand: bool) -> None:
        residues.setdefault(chain, {})[resi] = one or "X"
        if is_ligand:
            ligand_counts[chain] = ligand_counts.get(chain, 0) + 1

    if not is_cif:
        for line in text.splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            resname = line[17:20].strip().upper()
            one = AA3TO1.get(resname)
            is_ligand = one is None
            if is_ligand and resname in ("HOH", "WAT", "DOD"):
                continue  # skip water
            note(line[21] or "_", line[22:27].strip(), one, is_ligand)
    else:
        cols: dict[str, int] = {}
        in_loop = False
        for line in text.splitlines():
            if line.startswith("_atom_site."):
                cols[line.strip().split(".", 1)[1]] = len(cols)
                in_loop = True
                continue
            if in_loop and line.startswith("#"):
                break
            if in_loop and line and not line.startswith("_"):
                parts = line.split()
                if len(parts) < len(cols):
                    continue
                resname = parts[cols.get("label_comp_id", 0)].upper()
                one = AA3TO1.get(resname)
                is_ligand = one is None
                if is_ligand and resname in ("HOH", "WAT", "DOD"):
                    continue
                chain = parts[cols.get("auth_asym_id",
                                       cols.get("label_asym_id", 0))]
                resi = parts[cols.get("auth_seq_id",
                                      cols.get("label_seq_id", 0))]
                note(chain, resi, one, is_ligand)

    out = []
    for c, r in residues.items():
        n_lig = ligand_counts.get(c, 0)
        # A chain is a ligand chain only when ligands are the majority of
        # its residues — a protein with a bound small molecule is still a
        # protein chain.
        if n_lig and n_lig * 2 > len(r):
            out.append({"id": c, "residues": len(r), "ligand": True})
        else:
            out.append({"id": c, "residues": len(r)})
    return out


def parse_highlight(spec: str) -> list[dict]:
    """"A:385,A:386,B:12" -> [{"chain": "A", "resi": 385}, ...].

    Residue numbers are ints when they look like one, so the viewer can
    match them against 3Dmol's numeric resi. Anything unparseable is
    dropped rather than failing the publish — a highlight is a courtesy,
    not part of the contract.
    """
    out = []
    for item in (spec or "").split(","):
        item = item.strip()
        if ":" not in item:
            continue
        chain, _, resi = item.partition(":")
        chain, resi = chain.strip(), resi.strip()
        if not chain or not resi:
            continue
        out.append({
            "chain": chain,
            "resi": int(resi) if resi.lstrip("-").isdigit() else resi,
        })
    return out


def publish_structure(args) -> int:
    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    text = src.read_text(errors="replace")
    fmt = sniff_format(text)
    chains = parse_chains(src)
    if not chains:
        print(f"warning: no chains parsed from {src} (detected {fmt}) — "
              "the viewer will show the structure without chain info",
              file=sys.stderr)

    # Optional role assignment: "A=target,B=binder"
    roles = {}
    for pair in (args.roles or "").split(","):
        if "=" in pair:
            cid, role = pair.split("=", 1)
            roles[cid.strip()] = role.strip()
    for ch in chains:
        if ch["id"] in roles:
            ch["role"] = roles[ch["id"]]

    meta = {
        "name": args.name or src.stem,
        "source": args.source or "unknown",
        "format": fmt,
        "chains": chains,
        "metrics": json.loads(args.metrics) if args.metrics else {},
        "notes": args.notes or "",
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    highlight = parse_highlight(args.highlight)
    if highlight:
        meta["highlight"] = highlight

    atomic_write(VIEWER / "current.cif", text)
    atomic_write(VIEWER / "current.json", json.dumps(meta, indent=2))
    print(f"published structure: {meta['name']} "
          f"({len(chains)} chains, {fmt})")
    return 0


def publish_status(args) -> int:
    status = {
        "state": args.state,
        "skill": args.skill or "",
        "step": args.step or "",
        "step_index": args.index or 0,
        "step_total": args.total or 0,
        "message": args.message or "",
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    if args.state == "error" and args.message:
        status["error"] = args.message

    atomic_write(VIEWER / "status.json", json.dumps(status, indent=2))
    print(f"status: {args.state}"
          + (f" {args.index}/{args.total} {args.step}" if args.total else "")
          + (f" — {args.message}" if args.message else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", help="path to a .cif/.pdb to publish")
    ap.add_argument("--name", help="display name (default: filename stem)")
    ap.add_argument("--source", help="producing skill or model")
    ap.add_argument("--notes", help="free-text context")
    ap.add_argument("--roles", help='chain roles, e.g. "A=target,B=binder"')
    ap.add_argument("--metrics", help="JSON object of metrics")
    ap.add_argument("--highlight",
                    help='residues to highlight on arrival, e.g. '
                         '"A:385,A:386,B:12". The viewer selects them as '
                         'if the user had clicked, so what a skill just '
                         'changed is visible without hunting for it.')

    ap.add_argument("--status", action="store_true", help="also write status")
    ap.add_argument("--state", choices=["idle", "running", "done", "error"])
    ap.add_argument("--step", help="current step name")
    ap.add_argument("--index", type=int, help="current step number")
    ap.add_argument("--total", type=int, help="total step count")
    ap.add_argument("--message", help="human-readable progress message")
    ap.add_argument("--skill", help="skill name for attribution")

    args = ap.parse_args()

    if not args.structure and not args.status:
        ap.error("nothing to do: pass --structure and/or --status")

    rc = 0
    if args.structure:
        rc = publish_structure(args) or rc
    if args.status:
        if not args.state:
            ap.error("--status requires --state")
        rc = publish_status(args) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
