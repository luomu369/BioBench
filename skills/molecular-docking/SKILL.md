---
name: molecular-docking
description: '**WORKFLOW SKILL** — Dock a small-molecule ligand into a protein receptor with AutoDock Vina, rigid or with flexible receptor side chains, and report the predicted binding poses and affinities. USE FOR: predicting how a drug-like molecule binds a protein; finding a binding site; scoring binding affinity; comparing poses of several ligands; virtual screening a molecule library against a target; docking when the pocket must rearrange to fit the ligand. DO NOT USE FOR: installing Vina (use vina-deploy); designing a protein to bind a ligand (use ligand-binding-design); generating the ligand itself (use molecule-generation or pocket-molecule-design); analysing the conformational energy of a bond or side chain (use conformational-analysis); publishing structures (use structure-viewer). Requires Vina installed — run vina-deploy first.'
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
Receptor:       7XWQ.cif (GPCR, chains A-D)
Ligand:         c1cc(ccc1C[C@@H](C#N)c2ccc(cc2Cl)O)O  (I1M)
Box center:     -3.3, -7.6, -39.7   (centroid of the bound ligand)
Box size:       20 x 20 x 20 Å
Poses:          5
Exhaustiveness: 16
Flexibility:    rigid receptor

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Box center** | Known binding site, or `--auto-box` | required |
| **Box size** | Should cover the site plus ligand reach | 22 Å cube |
| **Poses** | How many binding modes to report | 9 |
| **Exhaustiveness** | Higher = more thorough, slower | 8 |
| **Flexibility** | Rigid, or flexible side chains | rigid |
| **Flex residues** | Which side chains may move | 8 Å of the box centre |

**Ask about flexibility explicitly.** It is the one parameter that changes
what the result means, not just how long it takes:

- **Rigid** — the pocket is frozen. Fast, and the score is comparable to
  other rigid runs.
- **Flexible** — chosen side chains rotate during the search. Slower
  (roughly 10×), and the score is *not* comparable to a rigid one, because
  a flexible receptor can always improve its score by moving. Compare
  flexible against flexible.

Ask which the user wants, and say why it matters. If they say "just dock
it", rigid is the safe default — but if the pocket is known to rearrange
for this ligand class, say so and offer flexible.

**Ask about the box when there is no co-crystallised ligand.** An apo
structure gives `--auto-box` nothing to find, and a guessed centre produces
meaningless affinities (a box that missed the pocket scored +6 kcal/mol).
Offer the alternatives: a holo structure of the same target, a literature
site, or a residue selection in the viewer.

If the user already specified any of these ("dock with 20 poses", "let the
pocket move", "use the residues I selected"), use it and do not re-ask.

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

python3 $PUB --status --state running --step "分子对接" --index 1 --total 2 \
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
| `--cpu` | Threads for Vina; 0 = all cores (see below) |

### Vina is CPU-only — pass `--cpu` explicitly

AutoDock Vina has no GPU path. Its search is Monte Carlo plus local
optimisation, where each step depends on the previous one, so it cannot be
batched the way a neural network can; the scoring function is cheap enough
that moving data to a GPU costs more than computing it. `--help` lists only
`--cpu`, with no CUDA or OpenCL option.

Vina's own CPU auto-detection is unreliable — it logged `CPU: 0` and then
ran single-threaded on a 20-core machine, turning a one-minute dock into
fifteen. Both scripts therefore pass `--cpu` explicitly, defaulting to `0`
(all cores). Leave it alone unless you want to reserve cores for something
else.

If GPU docking is genuinely needed, the drop-in replacements are
**AutoDock-GPU** (same Vina scoring, CUDA/OpenCL, typically 10–100×) and
**Uni-Dock** (CUDA, faster still, supports batch and covalent docking).
Both keep Vina-compatible output. Diffusion-based dockers (DiffDock) and
structure-prediction models (Boltz-2, Chai-1) are a different paradigm and
do run on the GPU.

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
python3 $PUB --status --state done --step "分子对接" --index 2 --total 2 \
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
  --metrics '{"best_affinity_kcal_mol": {"label": "结合亲和力", "value": -8.55, "hint": "kcal/mol，越负越强"}, "num_modes": {"label": "姿态数", "value": 5}}' \
  --status --state done --step "分子对接" --index 2 --total 2 \
  --skill molecular-docking --message "对接完成"
```

The viewer renders ligand chains as orange ball-and-stick on top of the
protein style, so the pose stays visible under cartoon/surface views.

### Writing `--notes`

The viewer shows `--notes` verbatim, so write whatever the numbers alone
don't say. No required structure:

```
--notes "亲和力 -8.55 kcal/mol，5 个姿态。建议：与共晶配体对比打分。"
```

Reference material if you want it:

| Result | Usually means |
|--------|---------------|
| affinity < -10 | Strong; verify with higher exhaustiveness |
| affinity -7 to -10 | Moderate candidate; compare against a known ligand |
| affinity > -5 | Weak; check the box centre, or try flexible docking |
| Flexible beats rigid by > 2 kcal/mol | The pocket rearranges; report both numbers |
| All modes within 1 kcal/mol | Well-determined binding mode |
| Modes spread over > 3 kcal/mol | Ambiguous; raise exhaustiveness |

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

`--flex-cutoff` picks every amino-acid residue of `--flex-chain` with any
atom within that distance of the box centre; `--flex-residues 298,302`
overrides it with an explicit list.

> **Nearest atom, not centroid.** A long side chain reaches into the box
> while its centroid stays well outside — MET 479 in 7XWQ has its centroid
> 11.4 Å from the pocket centre but its CB/CG sit right where the ligand
> goes. Judging by centroid leaves such residues rigid, and the ligand then
> clashes with a side chain Vina was never allowed to move.

Flexible docking is slower (roughly 10× rigid) and the scores are not
directly comparable to a rigid run — a flexible receptor can always reach
a better score by moving. Compare flexible against flexible.

> **It does search conformations.** `Exhaustiveness` and a random seed
> appear in the log exactly as in rigid docking; the Monte Carlo search
> covers ligand pose *and* the flexible side-chain torsions. Nothing is
> placed directly.

> **Publish `complex.pdb`, not the receptor.** A flexible run returns the
> ligand *and* the side chains it moved, but the receptor file still holds
> those residues in their original positions. Publishing that receptor next
> to the new ligand shows the ligand clashing with side chains Vina had
> already rotated away — the pose looks impossible even though the docking
> was fine. `dock_flex.py` writes `complex.pdb` with the moved residues
> written back over the receptor's copies; publish that.

> **Highlight the flexible residues.** They are the part of the result a
> rigid run could not have produced, and the viewer draws them with their
> side chains out so the rearrangement is visible:
>
> ```bash
> FLEX=$(python3 -c "
> import json, re
> s = json.load(open('flex_out/summary.json'))
> print(','.join(f\"{s['flex_chain']}:{re.match(r'\d+', r).group()}\"
>                for r in s['flex_residues']))
> ")
> python3 $PUB --structure flex_out/complex.pdb --highlight "$FLEX" ...
> ```
>
> `summary.json` lists them as `298LEU`, `336MET` — take the leading digits
> for the number. A flexible run with 20 residues highlights 20; that is
> the point, but if the list is long consider highlighting only the ones
> that moved most.

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

### Verify the complex before publishing it

A flexible run's output is easy to get subtly wrong, and the viewer will
happily render a physically impossible structure. Three checks, all cheap:

```bash
python3 -c "
import gemmi, math, sys
st = gemmi.read_structure(sys.argv[1]); st.setup_entities()
lig = [a for ch in st[0] if ch.name=='L' for r in ch for a in r]
pro = [a for ch in st[0] if ch.name!='L' for r in ch for a in r]
clash = sum(1 for p in pro for l in lig if math.dist(p.pos, l.pos) < 2.0)
print(f'chains: {[(c.name, len(c)) for c in st[0]]}')
print(f'clashes < 2.0 A: {clash}')
" flex_out/complex.pdb
```

1. **Residue count per chain** — compare against the receptor you fed in.
   A chain that lost residues means the merge dropped them.
2. **Clash count** — receptor-ligand atom pairs under 2.0 Å should be 0.
   Anything above means the pose is not physically realisable.
3. **Residue order** — a chain whose numbering jumps backwards mid-file
   renders as two disconnected pieces in the viewer.

> **Residue numbers repeat across chains.** 7XWQ has a 298 on both A and
> B. Any code that matches "the residues Vina moved" by number alone will
> match both, and since Vina only emitted the flexible chain's version,
> the other chain's copy vanishes — 19 residues silently deleted from
> chain B, leaving it in 14 pieces. Always match on chain *and* number.

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
| Ligand overlaps the receptor in the viewer | Receptor still holds its co-crystallised ligand | `remove_ligands_and_waters()` before combining |
| Ligand clashes with a side chain Vina moved | Published the rigid receptor, not the moved one | Publish `complex.pdb` from `dock_flex.py` |
| Ligand clashes with a residue that was never flexible | Flex residues picked by centroid, missing long side chains | `pick_flex_residues` now uses nearest-atom distance |
| Flexible docking takes 15+ minutes | Vina ran single-threaded despite 20 cores | Pass `--cpu 0`; auto-detection logged `CPU: 0` and used one thread |

## References

- [AutoDock Vina GitHub](https://github.com/ccsb-scripps/AutoDock-Vina)
- [Vina documentation](https://autodock-vina.readthedocs.io/)
- Eberhardt et al., *J. Chem. Inf. Model.* (2021), doi:10.1021/acs.jcim.1c00203
