#!/usr/bin/env python3
"""Find binding pockets in a protein structure with fpocket.

fpocket scores cavities by how drug-like they are, which makes it the
natural first step when the user has no ligand in hand — it answers
"where could something bind" rather than "how does this molecule bind".

Usage:
  python3 detect_pockets.py --structure receptor.pdb --out pockets_out
  python3 detect_pockets.py --structure r.pdb --top 5 --min-volume 200

Exit codes: 0 ok, 1 fpocket failed, 2 bad input, 3 no pockets found.
"""

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The shared PDB column layout, so a pocket's atoms are read with the same
# field boundaries every other skill writes with. The binary's location comes
# from the shared path module rather than a literal.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import tool
from pdb_format import iter_atoms

FPOCKET = Path(
    __import__("os").environ.get("FPOCKET_BIN",
                                 str(tool("fpocket") / "bin" / "fpocket"))
)

# fpocket's descriptor table, in column order. Only the columns a user
# acts on are surfaced; the rest stay in the JSON for anyone who wants them.
DESCRIPTORS = [
    "cav_id", "drug_score", "volume", "nb_asph", "inter_chain",
    "apol_asph_proportion", "mean_asph_radius", "as_density",
    "mean_asph_solv_acc", "mean_loc_hyd_dens", "flex",
    "hydrophobicity_score", "volume_score", "charge_score",
    "polarity_score", "a0_apol", "a0_pol", "af_apol", "af_pol",
    "n_abpa",
]


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def to_pdb(src: Path, dst: Path) -> None:
    """Write a structure as PDB, dropping waters, altlocs and ligands.

    fpocket reads PDB natively; mmCIF only through a molfile plugin that
    is not built on aarch64. Converting here keeps the caller's input
    format free.
    """
    import gemmi
    st = gemmi.read_structure(str(src))
    st.setup_entities()
    st.remove_ligands_and_waters()
    st.remove_alternative_conformations()
    st.write_pdb(str(dst))


def parse_descriptors(text: str) -> list[dict]:
    """Parse fpocket's whitespace-aligned descriptor table.

    The table is space-separated with a header row, but the amino-acid
    composition columns are unnamed and shift the tail, so split on
    whitespace and take the leading named columns by position.
    """
    lines = [l for l in text.splitlines() if l.strip()]
    # find the header line
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("cav_id")), None)
    if start is None:
        return []
    rows = []
    for line in lines[start + 1:]:
        parts = line.split()
        if len(parts) < len(DESCRIPTORS):
            continue
        if not parts[0].isdigit():
            continue
        row = {}
        for name, raw in zip(DESCRIPTORS, parts):
            if name == "cav_id":
                row[name] = int(raw)
            elif raw in ("NULL", "-"):
                row[name] = None
            else:
                try:
                    row[name] = float(raw)
                except ValueError:
                    row[name] = raw
        rows.append(row)
    return rows


def pocket_centroid(atm_pdb: Path):
    """Centroid of a pocket's atoms, or None if the file is missing."""
    if not atm_pdb.is_file():
        return None
    xs, ys, zs = [], [], []
    for atom in iter_atoms(atm_pdb.read_text(errors="replace")):
        xs.append(atom.x)
        ys.append(atom.y)
        zs.append(atom.z)
    if not xs:
        return None
    n = len(xs)
    return [round(sum(xs) / n, 2), round(sum(ys) / n, 2), round(sum(zs) / n, 2)]


def pocket_lining(env_pdb: Path) -> list[dict]:
    """Residues lining a pocket, from its environment file.

    fpocket writes the pocket's alpha spheres plus the protein atoms around
    them into `pocketN_env_atm.pdb`. Those protein residues are what a
    highlight or a design run wants, so collect them as chain/resi/resn.
    """
    if not env_pdb.is_file():
        return []
    seen = {}
    for atom in iter_atoms(env_pdb.read_text(errors="replace")):
        key = (atom.chain, atom.resi)
        if key not in seen:
            seen[key] = {"chain": atom.chain, "resi": atom.resi,
                         "resn": atom.resn}
    return [seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1]))]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", required=True, help="PDB or mmCIF receptor")
    ap.add_argument("--out", default="pockets_out", help="output directory")
    ap.add_argument("--top", type=int, default=0,
                    help="keep only the N best-scoring pockets (0 = all)")
    ap.add_argument("--min-volume", type=float, default=0.0,
                    help="drop pockets smaller than this many A^3")
    args = ap.parse_args()

    if not FPOCKET.is_file():
        print(f"error: fpocket not found at {FPOCKET}\n"
              "set FPOCKET_BIN or install it — see the pocket-detection skill",
              file=sys.stderr)
        return 2

    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    work = Path(args.out)
    work.mkdir(parents=True, exist_ok=True)

    # fpocket writes <stem>_out/ next to its input, so run it on a copy
    # inside the output directory to keep the result self-contained.
    local = work / (src.stem + ".pdb")
    if src.suffix.lower() in (".cif", ".mmcif"):
        to_pdb(src, local)
    else:
        shutil.copy(src, local)

    r = sh([str(FPOCKET), "-f", str(local), "-d"])
    (work / "fpocket.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print(f"error: fpocket failed\n{(r.stdout + r.stderr)[-500:]}",
              file=sys.stderr)
        return 1

    pockets_dir = work / (local.stem + "_out")
    if not pockets_dir.is_dir():
        print("error: fpocket produced no output directory", file=sys.stderr)
        return 1

    pockets = parse_descriptors(r.stdout)
    if not pockets:
        print("error: no pockets in fpocket output", file=sys.stderr)
        return 3

    for p in pockets:
        cid = p["cav_id"]
        atm = pockets_dir / f"pocket{cid}_atm.pdb"
        env = pockets_dir / f"pocket{cid}_env_atm.pdb"
        p["atoms_pdb"] = str(atm) if atm.is_file() else None
        p["environment_pdb"] = str(env) if env.is_file() else None
        p["centroid"] = pocket_centroid(atm)
        p["lining"] = pocket_lining(env)

    # fpocket numbers pockets by size, not by score, so sort here.
    pockets.sort(key=lambda p: -(p.get("drug_score") or 0))

    if args.min_volume:
        pockets = [p for p in pockets if (p.get("volume") or 0) >= args.min_volume]
    if args.top:
        pockets = pockets[:args.top]

    if not pockets:
        print("error: no pockets left after filtering", file=sys.stderr)
        return 3

    summary = {
        "structure": str(src),
        "pocket_count": len(pockets),
        "pockets": pockets,
    }
    (work / "pockets.json").write_text(json.dumps(summary, indent=2))

    best = pockets[0]
    print(f"found {len(pockets)} pockets; best drug_score "
          f"{best['drug_score']:.4f} (volume {best['volume']:.0f} A^3, "
          f"centroid {best['centroid']})")
    for p in pockets[:10]:
        print(f"  pocket {p['cav_id']:>3}  score {p['drug_score']:.4f}  "
              f"vol {p['volume']:8.1f}  centroid {p['centroid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
