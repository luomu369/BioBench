---
name: ligand-binding-design
description: '**WORKFLOW SKILL** — Design a protein binder that binds a small-molecule ligand, using BoltzGen''s protein-small_molecule protocol. USE FOR: designing a protein to bind a drug-like molecule; finding a binding pocket for a ligand; generating small-molecule-binding proteins; testing whether a scaffold can accommodate a ligand. DO NOT USE FOR: installing BoltzGen (use boltzgen-deploy); protein-only binders (use binder-design); generating the ligand itself (use molecule-generation); publishing results (use structure-viewer). Requires BoltzGen installed — run boltzgen-deploy first.'
---

# Ligand-Binding Design

Design a protein that binds a small molecule, using BoltzGen's
`protein-small_molecule` protocol.

**Prerequisite:** BoltzGen installed with weights. If not, follow
`boltzgen-deploy` first.

## When to Use

- "Design a protein that binds this molecule"
- "Find a binder for this ligand"
- "Can this scaffold accommodate this drug?"
- "Design a small-molecule-binding protein"

## Confirm before running

**A design run costs 20–30 minutes of GPU time. Never start one without
confirming the parameters with the user first.**

```
Ligand:      <SMILES or CCD code>
Protein:     de novo, 140–180 residues
Designs:     4
Protocol:    protein-small_molecule
Est. time:   ~25 minutes

Proceed?
```

Confirm:

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Ligand** | SMILES string or a PDB CCD code | required |
| **Protein length** | From the request; 140–180 is typical for a binding pocket | 140–180 |
| **Number of designs** | Ask the user | 4 |

If the user gave a count, use it. Otherwise ask.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Write the design spec              (YAML with a ligand entity)
2. Validate the spec                  (boltzgen check)
3. Report progress                    (structure-viewer publisher)
4. Run the design                     (boltzgen run)
5. Publish the top-ranked result      (structure-viewer publisher)
```

## Step 1 — Write the design spec

The ligand is an entity alongside the designed protein.

### From a SMILES string

```yaml
entities:
  - protein:
      id: A
      sequence: 140..180
  - ligand:
      id: B
      smiles: "CCOc1ccc(C[NH+]2CCN(c3ccccc3)CC2)cc1"
```

### From a PDB CCD code

Use this when the ligand is a known chemical component:

```yaml
entities:
  - protein:
      id: A
      sequence: 140..180
  - ligand:
      id: B
      ccd: RPB
```

### Around user-selected residues

If the user selected residues in the viewer and wants the design to bind near
them, read `webapp/static/selection.json` and add a `binding` block to the
protein entity:

```yaml
entities:
  - protein:
      id: A
      sequence: 140..180
      binding:
        - chain:
            id: A
            residue: [45, 46, 49]
  - ligand:
      id: B
      smiles: "CCO"
```

**If `selection.json` is missing or `count` is 0**, the user has not selected
anything — ask them to click residues in the viewer rather than guessing.

## Step 2 — Validate

```bash
HF_HOME=/home/asus_gx10/models/boltzgen-1 HF_HUB_OFFLINE=1 \
  boltzgen check design.yaml
```

This reports unresolved residues, the designed length, and writes a
visualization `.cif`.

> **Note:** unlike protein-only design, this protocol needs `mols.zip` for
> ligand handling. If `boltzgen check` complains about a missing molecule
> dataset, download it:
>
> ```bash
> HF_ENDPOINT=https://hf-mirror.com python -c "
> from huggingface_hub import hf_hub_download
> hf_hub_download('boltzgen/inference-data', 'mols.zip',
>                 local_dir='/home/asus_gx10/models/boltzgen-1',
>                 repo_type='dataset')
> "
> ```

## Step 3 — Report progress

A design run is 20–30 minutes, so declare the whole route before starting it
— the viewer then shows the plan from the first second. The reporting
convention is in `structure-viewer`.

Name the steps for the user, not after BoltzGen's stages. For a standard
six-stage run:

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state queued --skill ligand-binding-design --job lbd1 \
  --plan "生成主链,折叠验证,结构预测,亲和力评估,汇总指标,筛选排序" \
  --message "已规划：4 个结合小分子的蛋白设计，预计 25 分钟"
```

Adjust the route to the run, then report each step as it is reached with the
same `--job` and a `--step` matching what you declared.

### What to report at each step

Write messages a biologist reads — describe the biology, not the algorithm.
Avoid raw terms like "diffusion sampling" or "DataLoader". The pipeline has
six stages, but the wording is yours: "折叠验证" says what is happening,
`design_folding` does not. Split or merge stages when the run warrants it.

## Step 4 — Run

`--num_designs` must match what the user confirmed in Step 0.

```bash
HF_HOME=/home/asus_gx10/models/boltzgen-1 \
boltzgen run design.yaml \
  --output results/ \
  --protocol protein-small_molecule \
  --num_designs 4 \
  --diffusion_batch_size 1 \
  --num_workers 0 \
  --use_kernels false \
  --steps design design_folding folding affinity analysis filtering
```

| Flag | Why |
|------|-----|
| `--protocol protein-small_molecule` | Enables the ligand entity |
| `--num_designs` | **Must equal the confirmed count** |
| `--num_workers 0` | Avoids DataLoader worker deadlock |
| `--steps ...` | Explicit pipeline |

Unlike protein-only design, **do not pass `--skip_inverse_folding`** here —
the ligand path needs the full pipeline.

> **Do not set `HF_HUB_OFFLINE=1` for this protocol.** The ligand path resolves
> `mols.zip` through the Hub cache and raises
> `LocalEntryNotFoundError` when offline mode blocks the lookup. Protein-only
> design is unaffected.

## Step 5 — Publish the result

```bash
RUN=results

BEST=$(ls "$RUN"/intermediate_designs/rank0_*.cif 2>/dev/null | head -1)
[[ -z "$BEST" ]] && BEST="$RUN"/<spec_name>.cif

python3 $PUB \
  --structure "$BEST" \
  --name "$(basename "$BEST" .cif)" \
  --source boltzgen \
  --roles "A=protein,B=ligand" \
  --metrics '{"designs": 4}' \
  --notes "Protein designed to bind the specified ligand" \
  --status --state done --step "筛选并排序设计" --index 5 --total 5 \
  --skill ligand-binding-design --message "Published $(basename "$BEST")"
```

**Check the chain IDs in the generated `.cif` before setting `--roles`** —
BoltzGen does not preserve the IDs from your spec.

## Output layout

```
results/
├── config/                  # Generated per-step YAML
├── intermediate_designs/    # Per-design .cif + .npz
├── steps.yaml
└── <spec_name>.cif          # Final complex structure
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `mols.zip` not found | Ligand handling needs the molecule dataset | Download it (see Step 2) |
| `LocalEntryNotFoundError` for `mols.zip` | `HF_HUB_OFFLINE=1` blocks the cache lookup | Drop that env var for this protocol |
| `mols.zip` is 76 bytes | LFS pointer, not the real file | Re-download with `force_download=True` |
| CUDA OOM | batch too large | Lower `--diffusion_batch_size` |
| Ligand rejected in spec | Invalid SMILES or unknown CCD code | Validate the SMILES with RDKit first |
| No designs pass filtering | Ligand too large or pocket too small | Widen the protein length range |

## References

- [BoltzGen GitHub](https://github.com/HannesStark/boltzgen)
- [BoltzGen weights on HF](https://huggingface.co/boltzgen/boltzgen-1)
