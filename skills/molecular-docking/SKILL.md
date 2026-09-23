---
name: molecular-docking
description: '**WORKFLOW SKILL** — Dock a small-molecule ligand into a protein receptor with AutoDock Vina, rigid or with flexible receptor side chains, and report the predicted binding poses and affinities. USE FOR: predicting how a drug-like molecule binds a protein; finding a binding site; scoring binding affinity; comparing poses of several ligands; virtual screening a molecule library against a target; docking when the pocket must rearrange to fit the ligand. DO NOT USE FOR: installing Vina (use vina-deploy); designing a protein to bind a ligand (use ligand-binding-design); generating the ligand itself (use molecule-generation or pocket-molecule-design); publishing structures (use structure-viewer). Requires Vina installed — run vina-deploy first.'
---

# Molecular Docking

Dock a small molecule into a protein with AutoDock Vina and report the scored
poses.

**Prerequisite:** Vina and the Python toolchain installed. If not, follow
`vina-deploy` first.

## When to Use

- "Dock this molecule into that protein"
- "Where does this drug bind?"
- "Score the binding affinity of these ligands"
- "Screen this library against the target"
- "Predict the binding pose"
- "Dock this letting the pocket move" / "flexible docking"

## Confirm before running

Docking is fast (seconds to a minute), so confirm intent:

```
Receptor:     7XWQ.cif (GPCR, chains A-D)
Ligand:       c1cc(ccc1C[C@@H](C#N)c2ccc(cc2Cl)O)O  (I1M)
Box center:   -3.3, -7.6, -39.7   (centroid of the bound ligand)
Box size:     20 x 20 x 20 Å
Poses:        5
Exhaustiveness: 16

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Box center** | Known binding site, or `--auto-box` | required |
| **Box size** | Should cover the site plus ligand reach | 22 Å cube |
| **Poses** | How many binding modes to report | 9 |
| **Exhaustiveness** | Higher = more thorough, slower | 8 |

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Report progress                    (structure-viewer publisher)
2. Run the docking
3. Interpret and report the poses
```

## Step 1 — Report progress

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step docking --index 1 --total 2 \
  --skill molecular-docking --message "正在对接小分子"
```

## Step 2 — Run

```bash
DOCK=/home/asus_gx10/biobench/skills/molecular-docking/scripts/dock.py
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python
```

### With a known binding site

Get the box center from a co-crystallised ligand when one exists — its
centroid is the binding site:

```bash
$PY $DOCK \
  --receptor 7XWQ.cif \
  --ligand "c1cc(ccc1C[C@@H](C#N)c2ccc(cc2Cl)O)O" \
  --center -3.3 -7.6 -39.7 \
  --size 20 20 20 \
  --num-modes 5 \
  --exhaustiveness 16 \
  --out docking_out
```

> **Apo structures need a centre from somewhere else.** 7XWO has no bound
> ligand, so `--auto-box` fails and a guessed centre gives meaningless
> affinities (we saw +6 kcal/mol from a box that missed the pocket). Use a
> holo structure of the same target, a literature site, or a pocket detector.

### Auto-detect the box

If the receptor structure already contains a bound ligand, the box can be
centered on it automatically:

```bash
$PY $DOCK --receptor 7XWO.cif --ligand "CCO" --auto-box --out docking_out
```

> **`--auto-box` needs a ligand in the structure.** An apo structure has only
> amino acids, so auto-detection finds nothing — pass `--center` explicitly.

### Ligand from a file

```bash
$PY $DOCK --receptor r.cif --ligand-file mol.sdf \
  --center 0 0 0 --size 25 25 25 --out docking_out
```

### Batch screening

Run the script once per ligand and compare `best_affinity` across runs.

### Parameters

| Flag | Meaning |
|------|---------|
| `--center x y z` | Box center in Å (receptor coordinates) |
| `--size x y z` | Box dimensions in Å |
| `--auto-box` | Center on a bound ligand found in the receptor |
| `--num-modes` | How many poses to output |
| `--exhaustiveness` | Search effort; 8 is default, 32 for production |
| `--energy-range` | Max affinity gap from the best mode (kcal/mol) |

## Step 3 — Interpret the results

The script prints JSON and writes `summary.json`:

```json
{
  "best_affinity": -9.8,
  "num_modes": 9,
  "modes": [
    {"mode": 1, "affinity_kcal_mol": -9.8, "rmsd_lb": 0.0, "rmsd_ub": 0.0},
    {"mode": 2, "affinity_kcal_mol": -9.1, "rmsd_lb": 1.9, "rmsd_ub": 6.4}
  ]
}
```

| Field | Meaning |
|-------|---------|
| `affinity_kcal_mol` | Predicted binding free energy; **more negative = stronger** |
| `rmsd_lb` / `rmsd_ub` | Deviation from the best pose; low values mean the pose is reproducible |

### Reading the numbers

| Affinity (kcal/mol) | Interpretation |
|---------------------|----------------|
| < -10 | Strong predicted binding |
| -7 to -10 | Moderate |
| -5 to -7 | Weak |
| > -5 | Likely no meaningful binding |

Report in biological terms:

```
Docked the ligand into NK1R (box centered on the orthosteric site):

  Best pose:  -9.8 kcal/mol  (strong)
  9 poses within 3 kcal/mol of the best
  Poses 1-3 agree within 1.9 Å RMSD → the binding mode is well determined

  The ligand's protonated piperazine sits near Asp135 in TM3,
  consistent with the conserved amine-binding pocket of class A GPCRs.
```

Mark the task done:

```bash
python3 $PUB --status --state done --step docking --index 2 --total 2 \
  --skill molecular-docking --message "对接完成，最佳亲和力 -9.8 kcal/mol"
```

## Output files

```
docking_out/
├── receptor.pdbqt     # Prepared receptor
├── ligand.pdbqt       # Prepared ligand
├── docked.pdbqt       # All predicted poses (MODEL records)
├── ligand_pose1.pdb   # Best pose as plain PDB (chain L)
├── complex.pdb        # Receptor + best pose — publish this
├── vina.log           # Full Vina output
└── summary.json       # Parsed modes and affinities
```

`complex.pdb` is the file to hand to the viewer — it is the receptor with the
best pose appended as chain `L`. See `structure-viewer` for publishing.

### Publishing the complex

```bash
$PY $PUB --structure docking_out/complex.pdb \
  --name "I1M_docked_7XWQ" --source vina \
  --roles "A=receptor,L=ligand" \
  --metrics '{"best_affinity_kcal_mol":-8.55,"num_modes":5}' \
  --status --state done --step docking --index 2 --total 2 \
  --skill molecular-docking --message "对接完成"
```

The viewer renders ligand chains as orange ball-and-stick on top of the
protein style, so the pose stays visible under cartoon/surface views.

### Writing `--notes`

The Metrics panel shows raw numbers with no context. `--notes` is the only
place to explain them, so **every publish must spell out what the numbers
mean and what to do next**. Two parts, in this order:

1. **What each metric means** — name it, give the value, say which
   direction is good
2. **A suggestion** — one concrete next step based on the result

```
--notes "亲和力 -8.55 kcal/mol（越负越强，-10 以下为强结合），5 个姿态。
建议：结合较强，可用更高 exhaustiveness 复核，并与共晶配体对比打分。"
```

Match the advice to the number:

| Result | Suggest |
|--------|---------|
| affinity < -10 | Strong; verify with higher exhaustiveness |
| affinity -7 to -10 | Moderate candidate; compare against a known ligand |
| affinity > -5 | Weak; check the box centre, or try flexible docking |
| Flexible beats rigid by > 2 kcal/mol | The pocket rearranges; report both numbers |
| All modes within 1 kcal/mol | Well-determined binding mode |
| Modes spread over > 3 kcal/mol | Ambiguous; raise exhaustiveness |

Never publish a bare number. `{"best_affinity":-8.55}` with no notes
leaves the user guessing whether that is good.

### Flexible-residue docking

The default is a rigid receptor. When the pocket has to rearrange to take
a new ligand, let the side chains move:

```bash
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python

$PY skills/molecular-docking/scripts/dock_flex.py \
  --receptor 7XWQ.cif --ligand "CCO" \
  --center -3.3 -7.6 -39.7 --size 20 20 20 \
  --flex-chain A --flex-cutoff 8.0 \
  --num-modes 5 --exhaustiveness 16 --out flex_out
```

`--flex-cutoff` picks every amino-acid residue of `--flex-chain` whose
centroid is within that distance of the box centre; `--flex-residues
298,302` overrides it with an explicit list.

Flexible docking is slower (roughly 10× rigid) and the scores are not
directly comparable to a rigid run — a flexible receptor can always reach
a better score by moving. Compare flexible against flexible.

> **It does search conformations.** `Exhaustiveness` and a random seed
> appear in the log exactly as in rigid docking; the Monte Carlo search
> covers ligand pose *and* the flexible side-chain torsions. Nothing is
> placed directly.

### Publishing a flexible-docking result

The output PDBQT contains the ligand **and** the moved side chains, so
extracting the ligand needs care:

```bash
python3 -c "
import gemmi

def read_pdbqt_model(path, model=1):
    atoms, in_model = [], False
    for line in open(path):
        if line.startswith('MODEL'):
            in_model = (int(line.split()[1]) == model); continue
        if line.startswith('ENDMDL'):
            if in_model: break
            continue
        if not in_model: continue
        if line.startswith(('ATOM', 'HETATM')):
            raw = line[76:78].strip()
            if raw.upper() == 'A': raw = 'C'   # aromatic carbon
            atoms.append({'name': line[12:16].strip(),
                          'resn': line[17:20].strip(),
                          'x': float(line[30:38]), 'y': float(line[38:46]),
                          'z': float(line[46:54]),
                          'elem': raw or line[12:16].strip()[0]})
    return atoms
"
```

Three things bite here, all of which cost a round trip:

- **meeko names the ligand `UNL`, not `LIG`.** Filtering on `LIG` yields
  zero atoms and a silently empty chain.
- **AutoDock writes `A` for aromatic carbon.** It is not an element
  symbol, so parsers record `X` and 3Dmol falls back to magenta for the
  whole molecule. Map `A` → `C`.
- **Call `setup_entities()` before `make_mmcif_document()`.** Without it
  gemmi drops hand-built chains when writing mmCIF, and the publish
  reports fewer chains than expected.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `PDBQT parsing error: Unknown or inappropriate tag` | Receptor is PDB, not PDBQT | The script converts automatically; if running Vina by hand, use `mk_prepare_receptor.py` |
| `No template matched for residue_key=...` | Non-standard residue in the receptor | The script passes `-a` to drop unmatched residues |
| All affinities near 0 | Box does not overlap the binding site | Re-center the box; verify coordinates against the structure |
| Affinities positive (e.g. +6) | Box is in empty space, not the pocket | Get the centre from a co-crystallised ligand |
| `--auto-box` finds nothing | Apo structure with no bound ligand | Pass `--center` explicitly |
| Affinities look too good | Exhaustiveness too low, or box too large | Raise `--exhaustiveness`, shrink the box |
| Ligand has no 3D conformer | RDKit embedding failed | Check the SMILES; try a different tautomer |
| Ligand invisible in the viewer | PDBQT written with an empty chain ID | `pdbqt_to_pdb()` assigns chain `L`; republish `complex.pdb` |
| Ligand renders magenta | Element column read as `X` | AutoDock's `A` (aromatic C) is not an element; map it to `C` |
| `Unknown or inappropriate tag found in flex residue` | Hand-built flex PDBQT lacks the torsion tree | Let `mk_prepare_receptor.py -f` write it; it emits `BRANCH`/`ENDBRANCH` |
| `option --read_with_prody requires Prody` | `-i` defaults to the ProDy reader | Use `--read_pdb` instead |
| Flexible run finds 0 flexible residues | Box centre is on the wrong chain | Check which chain the reference ligand actually binds |
| Published structure missing the ligand chains | mmCIF written without `setup_entities()` | Call it before `make_mmcif_document()` |

## References

- [AutoDock Vina GitHub](https://github.com/ccsb-scripps/AutoDock-Vina)
- [Vina documentation](https://autodock-vina.readthedocs.io/)
- Eberhardt et al., *J. Chem. Inf. Model.* (2021), doi:10.1021/acs.jcim.1c00203
