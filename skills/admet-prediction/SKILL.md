---
name: admet-prediction
description: '**WORKFLOW SKILL** — Predict drug-likeness and ADMET-relevant properties of small molecules with RDKit: Lipinski rule of five, QED, synthetic accessibility, PAINS and Brenk structural alerts, and the property distributions a medicinal chemist checks first. USE FOR: asking whether a molecule is drug-like; filtering generated molecules before docking; checking a candidate for PAINS or toxicity alerts; comparing a set of molecules on drug-likeness; triaging which generated molecules are worth docking. DO NOT USE FOR: generating molecules (use molecule-generation or pocket-molecule-design); docking or scoring binding (use molecular-docking); designing a protein binder (use binder-design); predicting binding affinity (this is not what ADMET properties do). Requires RDKit — present in the docking and boltzgen conda environments.'
---

# ADMET Prediction

Predict drug-likeness and ADMET-relevant properties from SMILES, using
RDKit's descriptors and structural alert catalogs.

**Prerequisite:** RDKit. It is already in the `docking` and `boltzgen`
conda environments.

## What this is, and is not

**Rule-based, not a trained model.** Everything here is a physicochemical
descriptor compared against a published cutoff, or a substructure matched
against a literature alert list. That means:

- Fast — milliseconds per molecule, no GPU
- Reproducible — the same SMILES always gives the same answer
- Honest — the numbers are facts about the molecule, not predictions about
  a biological system

What it does **not** do: predict solubility, permeability, clearance,
hERG liability, or toxicity from a learned model. Those need trained models
on experimental data, which are not installed. The structural alerts below
are the closest rule-based proxy, and they are a proxy.

## When to Use

- "Is this molecule drug-like?"
- "Which of these generated molecules should I dock?"
- "Does this hit any PAINS filters?"
- "Compare these candidates on drug-likeness"
- "Flag anything with toxicity alerts"

## Confirm before running

Prediction is milliseconds, so confirm intent rather than cost:

```
Molecules:  10 from runs/resgen_redock/out/molecules.sdf
Checks:     Lipinski, QED, SA, PAINS, Brenk
Output:     table, ranked by QED

Proceed?
```

## Workflow

```
1. Get the molecules      (SMILES, or an SDF from a generation run)
2. Run the prediction
3. Rank and report
4. Hand the survivors to molecular-docking
```

## Step 1 — Get the molecules

From SMILES directly:

```bash
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python
SCRIPT=/home/asus_gx10/biobench/skills/admet-prediction/scripts/admet.py

$PY $SCRIPT --smiles "CC(=O)Oc1ccccc1C(=O)O" --smiles "Cn1cnc2c1c(=O)n(C)c(=O)n2C"
```

From an SDF — which is what `molecule-generation` and
`pocket-molecule-design` produce:

```bash
$PY $SCRIPT --sdf runs/resgen_redock/out/molecules.sdf
```

## Step 2 — Read the output

```
mol1  CC(=O)Oc1ccccc1C(=O)O
  MW 180.16  cLogP 1.31  TPSA 63.6  RotB 2  HBD 1  HBA 3
  heavy atoms 13  rings 1 (1 aromatic)  Fsp3 0.11  charge 0
  QED 0.55   SA 1.58
  Lipinski pass (0 violations)   Ghose pass   Veber pass
  ! Brenk: phenol_ester
  → passes Lipinski; 1 alert(s): Brenk
```

### The three rules

| Rule | What it checks | Notes |
|------|----------------|-------|
| **Lipinski Ro5** | MW ≤ 500, cLogP ≤ 5, HBD ≤ 5, HBA ≤ 10 | Fails if **more than one** is broken — that is how it was published |
| **Ghose** | MW 200–480, cLogP −0.4 to 5.6, MR 40–130 | A window, not a ceiling |
| **Veber** | RotB ≤ 10, TPSA ≤ 140 | Predicts oral bioavailability better than size does |

**Ghose's lower bound is reported but not counted as a failure.** A molecule
under the floor is small, which is rarely what a filter is looking for —
aspirin fails Ghose on MW 180 against a 200 floor, and calling that a
failure would be misleading. The JSON output separates `below_range` from
`above_range` for exactly this reason.

### The scores

| Score | Range | Reading |
|-------|-------|---------|
| **QED** | 0–1 | Drug-likeness from eight descriptors. ≥ 0.67 attractive, < 0.34 poor |
| **SA** | 1–10 | Synthetic accessibility. < 3 straightforward, > 6 hard to make |

### The alerts

| Catalog | What it flags |
|---------|---------------|
| **PAINS** | 480 substructures that hit many assays for the wrong reasons — frequent hitters, redox cyclers, aggregators |
| **Brenk** | Reactive, toxic, or metabolically unstable functionality |

An alert is not a rejection. It is a reason to look more carefully: a PAINS
hit means the compound may show activity in a screen without binding the
intended target, which matters far more for a screening library than for a
designed ligand.

## Step 3 — Rank and report

For a set of molecules, rank by QED and report the distribution rather than
listing every one:

```bash
$PY $SCRIPT --sdf molecules.sdf --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
rows = sorted(d, key=lambda a: -a['qed'])
print(f'{len(rows)} molecules')
for a in rows[:10]:
    flags = []
    if not a['lipinski']['passes']: flags.append('Lipinski')
    if a['sa_score'] and a['sa_score'] > 6: flags.append('hard-SA')
    if a['alerts']: flags.append(str(len(a['alerts'])) + ' alerts')
    print(f\"  QED {a['qed']:.2f}  SA {a['sa_score']}  {a['smiles'][:40]:40s}  {', '.join(flags)}\")
"
```

Report in terms of what to do next:

```
10 molecules generated, 7 pass Lipinski.

  Best QED:  0.82 — CCCc1ccc(O)cc1, SA 2.1, no alerts
  Worst:     0.21 — fails Lipinski on MW and cLogP, 3 PAINS alerts

Recommend docking the top 5 by QED. Two carry Brenk phenol_ester alerts —
worth knowing if they show activity, since that functionality hydrolyses.
```

## Step 4 — Hand off to docking

The survivors go to `molecular-docking`. Pass the SMILES or write an SDF;
the docking skill takes either.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `could not parse SMILES` | Invalid or unsupported syntax | Check the string; try a different tautomer |
| SDF entry did not parse | Missing coordinates or bad valences | Re-export the SDF; RDKit logs which entry |
| `SA_Score` import fails | Contrib module not on the path | The script adds it; if it still fails, SA is reported as null |
| Everything fails Ghose | Ghose is a window; small molecules fall below it | Read `below_range` — it is not a failure |
| QED is 0 for a reasonable molecule | QED's eight descriptors include some that can be zero | Check the properties; QED is a weighted product |

## References

- Lipinski et al., *Adv. Drug Deliv. Rev.* 23, 3–25 (1997), doi:10.1016/S0169-409X(96)00423-1
- Bickerton et al., *Nat. Chem.* 4, 90–98 (2012), doi:10.1038/nchem.1243 — QED
- Ertl & Schuffenhauer, *J. Cheminform.* 1, 8 (2009), doi:10.1186/1758-2946-1-8 — SA score
- Baell & Holloway, *J. Med. Chem.* 53, 2719–2740 (2010), doi:10.1021/jm901137j — PAINS
