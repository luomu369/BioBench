#!/usr/bin/env python3
"""Predict drug-likeness and ADMET-relevant properties from SMILES.

Rule-based, using RDKit's descriptors and structural alerts. No trained
model, so the numbers are physicochemical facts and literature cutoffs
rather than learned predictions — which means they are fast, reproducible,
and honest about what they are.

What it reports:

  Lipinski Ro5    — the original rule of five, plus the Ghose and Veber
                    variants that followed it
  QED             — a single 0-1 drug-likeness score from eight descriptors
  SA score        — synthetic accessibility, 1 (easy) to 10 (hard)
  PAINS          — pan-assay interference compounds: substructures that
                    hit many assays for the wrong reasons
  Brenk           — structural alerts for toxicity and reactivity
  Property ranges — the distributions a medicinal chemist checks first

Usage:
    python admet.py --smiles "CCO"
    python admet.py --sdf molecules.sdf
    python admet.py --smiles "CCO" --json
"""

import argparse
import json
import sys

# Lipinski's rule of five. The cutoffs are multiples of five because that
# is how they were published, not because the chemistry cares.
LIPINSKI = {
    "mw": (500, "molecular weight <= 500"),
    "clogp": (5, "logP <= 5"),
    "hbd": (5, "H-bond donors <= 5"),
    "hba": (10, "H-bond acceptors <= 10"),
}

# Ghose et al. (1999) — a tighter window derived from a different dataset.
GHOSE = {
    "mw": (200, 480),
    "clogp": (-0.4, 5.6),
    "mr": (40, 130),
}

# Veber et al. (2002) — rotatable bonds and polar surface area, which
# predict oral bioavailability better than the size cutoffs do.
VEBER = {
    "rotb": (10, "rotatable bonds <= 10"),
    "tpsa": (140, "TPSA <= 140"),
}


def load_molecules(args):
    """Molecules from --smiles or --sdf, as (name, mol) pairs."""
    from rdkit import Chem

    out = []
    if args.smiles:
        for i, smi in enumerate(args.smiles):
            mol = Chem.MolFromSmiles(smi)
            if mol is None:
                print(f"warning: could not parse SMILES {smi!r}", file=sys.stderr)
                continue
            out.append((f"mol{i+1}", mol))
    if args.sdf:
        supp = Chem.SDMolSupplier(args.sdf, removeHs=False)
        for i, mol in enumerate(supp):
            if mol is None:
                print(f"warning: SDF entry {i+1} did not parse",
                      file=sys.stderr)
                continue
            name = mol.GetProp("_Name") if mol.HasProp("_Name") else f"mol{i+1}"
            out.append((name or f"mol{i+1}", mol))
    return out


def sa_score(mol):
    """Synthetic accessibility, 1 (trivial) to 10 (essentially impossible).

    RDKit ships the Fraser et al. implementation as a contrib module; it is
    not on the default path, so it is added explicitly.
    """
    try:
        from rdkit.Chem import RDConfig
        import os
        import sys
        contrib = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if contrib not in sys.path:
            sys.path.append(contrib)
        import sascorer
        return sascorer.calculateScore(mol)
    except Exception:
        return None


def structural_alerts(mol):
    """PAINS and Brenk matches, as (catalog, description) pairs."""
    from rdkit.Chem import FilterCatalog

    hits = []
    for label, catalog in (("PAINS", "PAINS"), ("Brenk", "BRENK")):
        try:
            params = FilterCatalog.FilterCatalogParams()
            params.AddCatalog(getattr(
                FilterCatalog.FilterCatalogParams.FilterCatalogs, catalog))
            fc = FilterCatalog.FilterCatalog(params)
            for entry in fc.GetMatches(mol):
                hits.append((label, entry.GetDescription().strip()))
        except Exception as e:
            print(f"warning: {label} catalog failed ({e})", file=sys.stderr)
    return hits


def analyse(name, mol):
    """Everything this script knows about one molecule."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, QED

    props = {
        "mw": Descriptors.MolWt(mol),
        "clogp": Crippen.MolLogP(mol),
        "hbd": Descriptors.NumHDonors(mol),
        "hba": Descriptors.NumHAcceptors(mol),
        "rotb": Descriptors.NumRotatableBonds(mol),
        "tpsa": Descriptors.TPSA(mol),
        "mr": Crippen.MolMR(mol),
        "heavy": mol.GetNumHeavyAtoms(),
        "rings": Descriptors.RingCount(mol),
        "arom_rings": Descriptors.NumAromaticRings(mol),
        "fsp3": Descriptors.FractionCSP3(mol),
        "formal_charge": Chem.GetFormalCharge(mol),
    }

    # Rule-of-five violations, counted the way Lipinski did: a compound
    # fails if it breaks more than one.
    violations = []
    for key, (limit, _) in LIPINSKI.items():
        if props[key] > limit:
            violations.append(key)

    # Ghose and Veber are windows rather than ceilings, so they are checked
    # as ranges. Ghose's lower bound is reported separately: a molecule
    # below it is small, which is rarely the problem a filter is looking
    # for — aspirin fails Ghose on MW 180 against a 200 floor, and calling
    # that a failure would be misleading.
    ghose_low, ghose_high = [], []
    for key, (lo, hi) in GHOSE.items():
        if props[key] < lo:
            ghose_low.append(key)
        elif props[key] > hi:
            ghose_high.append(key)
    veber_fail = [k for k, (limit, _) in VEBER.items() if props[k] > limit]

    alerts = structural_alerts(mol)
    sa = sa_score(mol)

    return {
        "name": name,
        "smiles": Chem.MolToSmiles(mol),
        "properties": {k: round(v, 2) for k, v in props.items()},
        "lipinski": {
            "violations": violations,
            "passes": len(violations) <= 1,
        },
        "ghose": {
            "below_range": ghose_low,
            "above_range": ghose_high,
            # Only the upper bound counts as a failure; being under the
            # floor is noted but not held against the molecule.
            "passes": not ghose_high,
        },
        "veber": {"violations": veber_fail, "passes": not veber_fail},
        "qed": round(QED.qed(mol), 3),
        "sa_score": round(sa, 2) if sa is not None else None,
        "alerts": [{"catalog": c, "description": d} for c, d in alerts],
    }


def verdict(a):
    """A one-line read on the molecule, for the report."""
    bits = []
    if a["lipinski"]["passes"]:
        bits.append("passes Lipinski")
    else:
        bits.append(f"fails Lipinski ({', '.join(a['lipinski']['violations'])})")
    if a["qed"] >= 0.67:
        bits.append("attractive QED")
    elif a["qed"] < 0.34:
        bits.append("low QED")
    if a["sa_score"] is not None and a["sa_score"] > 6:
        bits.append("hard to synthesise")
    if a["alerts"]:
        catalogs = sorted({x["catalog"] for x in a["alerts"]})
        bits.append(f"{len(a['alerts'])} alert(s): {', '.join(catalogs)}")
    return "; ".join(bits)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smiles", action="append",
                    help="a SMILES string; repeat for several")
    ap.add_argument("--sdf", help="an SDF file of molecules")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of a table")
    args = ap.parse_args()

    if not args.smiles and not args.sdf:
        ap.error("pass --smiles and/or --sdf")

    mols = load_molecules(args)
    if not mols:
        print("error: no molecules could be read", file=sys.stderr)
        return 2

    results = [analyse(name, mol) for name, mol in mols]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0],
                         indent=2))
        return 0

    for a in results:
        p = a["properties"]
        print(f"\n{a['name']}  {a['smiles']}")
        print(f"  MW {p['mw']}  cLogP {p['clogp']}  TPSA {p['tpsa']}  "
              f"RotB {p['rotb']}  HBD {p['hbd']}  HBA {p['hba']}")
        print(f"  heavy atoms {p['heavy']}  rings {p['rings']} "
              f"({p['arom_rings']} aromatic)  Fsp3 {p['fsp3']}  "
              f"charge {p['formal_charge']}")
        print(f"  QED {a['qed']}   SA {a['sa_score']}")
        print(f"  Lipinski {'pass' if a['lipinski']['passes'] else 'FAIL'} "
              f"({len(a['lipinski']['violations'])} violations)   "
              f"Ghose {'pass' if a['ghose']['passes'] else 'FAIL'}   "
              f"Veber {'pass' if a['veber']['passes'] else 'FAIL'}")
        if a["alerts"]:
            for al in a["alerts"]:
                print(f"  ! {al['catalog']}: {al['description']}")
        print(f"  → {verdict(a)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
