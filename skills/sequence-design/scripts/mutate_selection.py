#!/usr/bin/env python3
"""Mutate the residues the user selected in the viewer.

Reads selection.json, converts PDB numbers to ProteinMPNN indices (via
read_selection.py, which owns that rule), runs one scan per position, and
writes a mutant PDB with each position's best replacement applied.

Usage:
  python3 mutate_selection.py --pdb backbone.pdb --out mutant_dir

Outputs: mutant_dir/mutant.pdb, mutant_dir/mutations.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

READ_SELECTION = Path(__file__).resolve().parent.parent.parent / \
    "structure-viewer" / "scripts" / "read_selection.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import env_python

MPNN_RUN = str(env_python("proteinmpnn").parent / "protein_mpnn_run")

ONE3TO1 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
           "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
           "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
           "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}


def pdb_residues(path):
    """{residue number: three-letter name} from a PDB's fixed columns."""
    out = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            try:
                out.setdefault(int(line[22:27]), line[17:21].strip().upper())
            except ValueError:
                pass
    return out


def scan(pdb, out_dir, omit):
    """One ProteinMPNN run omitting `omit`; returns the FASTA path or None."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jl = out_dir / "omit.jsonl"
    jl.write_text(json.dumps(omit) + "\n")
    with open(out_dir / "run.log", "w") as fh:
        subprocess.run(
            [MPNN_RUN, "--pdb-path", str(pdb), "--out-folder", str(out_dir),
             "--model-name", "v_48_020", "--num-seq-per-target", "4",
             "--sampling-temp", "0.1", "--seed", "42", "--batch-size", "1",
             "--omit-AA-jsonl", str(jl)],
            stdout=fh, stderr=subprocess.STDOUT)
    fas = list((out_dir / "seqs").glob("*.fa"))
    return fas[0] if fas else None


def best_at(fa, idx):
    """Lowest-score amino acid at 0-based `idx` in a FASTA of samples."""
    lines = Path(fa).read_text().splitlines()
    best = None
    for i, line in enumerate(lines):
        if not line.startswith(">") or "sample=" not in line:
            continue
        score = 0.0
        for tok in line.split(","):
            if tok.strip().startswith("score="):
                try:
                    score = float(tok.split("=", 1)[1])
                except ValueError:
                    pass
        aa = lines[i + 1].strip()[idx]
        if best is None or score < best[1]:
            best = (aa, score)
    return best


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdb", required=True, help="backbone PDB")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--chain", help="chain to mutate (default: first)")
    args = ap.parse_args()

    # The selection, converted to ProteinMPNN indices by the shared reader so
    # the numbering rule lives in exactly one place.
    cmd = [sys.executable, str(READ_SELECTION), "--format", "mpnn-omit",
           "--pdb-path", args.pdb]
    if args.chain:
        cmd += ["--chain", args.chain]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr.strip() or "error: could not read the selection",
              file=sys.stderr)
        return 1
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)

    spec = json.loads(r.stdout)
    chain = next(iter(next(iter(spec.values()))))
    entries = next(iter(spec.values()))[chain]

    residues = pdb_residues(args.pdb)
    lo = min(residues)
    stem = Path(args.pdb).stem

    # entries are [[1-based index], [wild type]]; back to PDB numbers.
    pos = []
    for (idx,), (wt,) in entries:
        pos.append((idx + lo - 1, idx - 1, wt))
    if not pos:
        print("error: nothing to mutate", file=sys.stderr)
        return 2

    out = Path(args.out)
    muts = {}
    for pdb_num, idx, wt in pos:
        one = {stem: {chain: [[[idx + 1], [wt]]]}}
        fa = scan(args.pdb, out / f"scan_{pdb_num}", one)
        if fa is None:
            print(f"warning: position {pdb_num} produced no output — see "
                  f"{out}/scan_{pdb_num}/run.log", file=sys.stderr)
            continue
        muts[pdb_num] = best_at(fa, idx) + (wt,)

    if not muts:
        print("error: no mutations produced", file=sys.stderr)
        return 3

    # Apply the replacements with PDBFixer, which rebuilds each mutated
    # residue's atoms rather than just renaming it. Renaming alone leaves the
    # old residue's atom set in place — a VAL turned into TRP keeps VAL's
    # seven atoms, and the force field then rejects it with "has 2 C atoms
    # too many". applyMutations swaps in the correct template and adds the
    # missing heavy atoms.
    from pdbfixer import PDBFixer
    from openmm.app import PDBFile

    fixer = PDBFixer(filename=str(args.pdb))
    fixer.findMissingResidues()
    fixer.findMissingAtoms()
    # applyMutations wants three-letter codes: "VAL-8-TRP".
    fixer.applyMutations(
        [f"{ONE3TO1[wt]}-{pdb_num}-{ONE3TO1[muts[pdb_num][0]]}"
         for pdb_num in sorted(muts)],
        chain)
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()

    mutant = out / "mutant.pdb"
    PDBFile.writeFile(fixer.topology, fixer.positions, open(mutant, "w"))

    print(f"\n{'PDB':>5} {'WT':>3} {'mut':>4} {'score':>7}")
    for n in sorted(muts):
        aa, sc, wt = muts[n]
        flag = "  <- wild type kept, omit did not land" if aa == wt else ""
        print(f"{n:5d} {wt:>3} {aa:>4} {sc:7.3f}{flag}")
    print("\nmutations: " + ", ".join(
        f"{muts[n][2]}{n}{muts[n][0]}" for n in sorted(muts)))
    print(f"wrote {mutant}")

    (out / "mutations.json").write_text(json.dumps(
        [{"pdb": n, "wt": muts[n][2], "mut": muts[n][0], "score": muts[n][1]}
         for n in sorted(muts)], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
