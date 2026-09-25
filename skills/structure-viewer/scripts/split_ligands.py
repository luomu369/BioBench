#!/usr/bin/env python3
"""Split ligands out of a protein's chains into chains of their own.

An mmCIF or PDB entry often puts a small molecule in the same auth_asym_id
as the protein it binds — 1HNN has SAH and SKF inside chains A and B. The
viewer decides whether a chain is a ligand by asking whether *most* of its
residues are non-amino-acid, so a ligand sharing a chain with 260 residues
is never marked as one: it does not appear in the chain panel, it renders in
the protein's cartoon style instead of its own stick style, and the ligand
style control has nothing to act on.

The atoms were never missing — only the metadata that says "this is a
ligand". Splitting by subchain restores it, because mmCIF already records
each ligand as its own label_asym_id.

Usage:
  python3 split_ligands.py --structure 1HNN.cif --out 1HNN_split.pdb

  # keep waters (dropped by default)
  python3 split_ligands.py --structure 1HNN.cif --out out.pdb --keep-waters

Exit codes: 0 ok, 1 failed, 2 bad input.
"""

import argparse
import sys
from pathlib import Path

AA = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "MSE", "SEC", "PYL", "HYP",
}
WATER = {"HOH", "WAT", "DOD", "H2O"}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", required=True, help=".cif or .pdb input")
    ap.add_argument("--out", required=True, help="output .pdb path")
    ap.add_argument("--keep-waters", action="store_true",
                    help="keep water molecules (dropped by default — they "
                         "dominate the atom count and render as noise)")
    ap.add_argument("--ligand-prefix", default="L",
                    help="chain ID prefix for the split ligands")
    args = ap.parse_args()

    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    try:
        import gemmi
    except ImportError:
        print("error: gemmi is required — it is in the 'docking' conda env",
              file=sys.stderr)
        return 1

    st = gemmi.read_structure(str(src))
    st.setup_entities()

    new = gemmi.Structure()
    new.name = st.name or src.stem
    new.spacegroup_hm = "P 1"
    new.cell = st.cell
    model = gemmi.Model("1")

    # subchain -> new chain ID, so two copies of the same ligand in one
    # entry (1HNN has SAH in both A and B) become separate chains rather
    # than collapsing into one.
    lig_chains = {}
    n_prot = n_lig = n_wat = 0

    for ch in st[0]:
        model.add_chain(gemmi.Chain(ch.name))
        for r in ch:
            if r.name in AA:
                model[ch.name].add_residue(r)
                n_prot += 1
            elif r.name in WATER:
                n_wat += 1
                if args.keep_waters:
                    model[ch.name].add_residue(r)
            else:
                sub = r.subchain or f"{ch.name}_{r.name}"
                if sub not in lig_chains:
                    lig_chains[sub] = f"{args.ligand_prefix}{len(lig_chains) + 1}"
                    model.add_chain(gemmi.Chain(lig_chains[sub]))
                model[lig_chains[sub]].add_residue(r)
                n_lig += 1

    new.add_model(model)
    new.setup_entities()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new.write_pdb(str(out))

    print(f"wrote {out}")
    print(f"  protein residues : {n_prot}")
    print(f"  ligand residues  : {n_lig} in {len(lig_chains)} chains")
    if n_wat:
        print(f"  waters           : {n_wat}"
              f"{' (kept)' if args.keep_waters else ' (dropped)'}")
    if lig_chains:
        print("  ligand chains    : "
              + ", ".join(f"{v} ({k})" for k, v in lig_chains.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
