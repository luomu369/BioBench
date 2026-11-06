---
name: molecule-generation
description: '**WORKFLOW SKILL** — Generate small molecules with GenMol (de novo, fragment completion, fragment linking) and report the results. USE FOR: generating novel molecules; growing a molecule from a fragment; linking two fragments into one molecule; exploring chemical space around a scaffold; scoring molecules. DO NOT USE FOR: installing GenMol (use genmol-deploy); protein or binder design (use binder-design); publishing structures (use structure-viewer). Requires GenMol installed — run genmol-deploy first.'
---

# Molecule Generation

Generate small molecules with GenMol and report what came out.

**Prerequisite:** GenMol installed and weights present. If not, follow
`genmol-deploy` first.

## When to Use

- "Generate some molecules"
- "Grow this fragment into a full molecule"
- "Link these two fragments"
- "Explore analogs of this scaffold"

## Confirm before running

Generation is fast (seconds), so confirm intent rather than cost:

```
Mode:        de novo
Count:       20 molecules
Temperature: 0.5
Model:       NV-GenMol-89M-v1

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Count** | Ask if not given | 20 |
| **Mode** | From the request | `denovo` |
| **Temperature** | Higher = more diverse, lower = more conservative | 0.5 |

If the user gave a count, use it. Otherwise ask.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Report progress                    (structure-viewer publisher)
2. Run the generator
3. Report the results
```

## Step 1 — Report progress

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "分子生成" --index 1 --total 2 \
  --skill molecule-generation --message "正在生成小分子"
```

## Step 2 — Run

```bash
CONDA="${CONDA:-$HOME/miniconda3}"
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
GEN=$BB/skills/molecule-generation/scripts/generate.py
PY=$CONDA/envs/genmol/bin/python
```

### De novo generation

```bash
$PY $GEN --mode denovo --num 20 --temp 0.5 --out molecules.txt
```

### Fragment completion

Grow a partial molecule into complete ones:

```bash
$PY $GEN --mode complete --fragment "c1ccccc1" --num 10 --out molecules.txt
```

### Fragment linking

Connect two fragments (dot-separated) into a single molecule:

```bash
$PY $GEN --mode link --fragment "c1ccccc1.CC(=O)O" --num 10 --out molecules.txt
```

> **Known limitation with v1 weights:** `fragment_linking` and
> `fragment_linking_onestep` both return 0 molecules on `NV-GenMol-89M-v1` —
> the substructure filter rejects everything the sampler produces. This is a
> model limitation, not a setup problem. GenMol V2 improves fragment linking;
> if you need it, download `NV-GenMol-89M-v2` and point
> `--checkpoint` at it. De novo and fragment completion work fine on v1.

### Modes

| Mode | Input | Use | v1 status |
|------|-------|-----|-----------|
| `denovo` | none | Novel molecules from scratch | ✅ works |
| `complete` | one fragment SMILES | Grow a scaffold | ✅ works |
| `link` | two dot-separated SMILES | Join fragments | ⚠️ returns 0 on v1 |

### Parameters

| Flag | Meaning |
|------|---------|
| `--num` | How many molecules to generate |
| `--temp` | Softmax temperature; 0.1 conservative, 1.0 diverse |
| `--randomness` | Sampling randomness |
| `--min-len` | Minimum added length for de novo |
| `--checkpoint` | Override the model path |

## Step 3 — Report the results

GenMol outputs SMILES, not 3D structures, so there is nothing to publish to
the viewer. Summarize in text instead:

```
Generated 20 molecules (de novo, T=0.5):

 1. Cc1ccc2c(c1)C[C@H](NC(=O)C[NH+]1CCOCC1)C2
 2. Cc1cccc(C(=O)Nc2ccc(-n3ccnn3)cc2)c1
 3. CCOc1ccc(C[NH+]2CCN(c3ccccc3)CC2)cc1
 ...

Notable: 3 contain a morpholine ring, 2 are chiral, all parse as valid SMILES.
```

Mark the task done:

```bash
python3 $PUB --status --state done --step "分子生成" --index 2 --total 2 \
  --skill molecule-generation --message "已生成 20 个分子"
```

## Output format

One SMILES per line:

```
Cc1ccc2c(c1)C[C@H](NC(=O)C[NH+]1CCOCC1)C2
Cc1cccc(C(=O)Nc2ccc(-n3ccnn3)cc2)c1
```

SAFE strings are decoded to SMILES by the sampler, so the output is directly
usable by RDKit or any downstream tool.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UnpicklingError: DictConfig` | torch 2.6+ `weights_only` default | `generate.py` already patches this; if calling the API directly, apply the patch from `genmol-deploy` |
| `ModuleNotFoundError: genmol` | Env not activated | Use the env's python directly |
| All molecules look similar | `--temp` too low | Raise to 0.8–1.2 |
| Molecules are invalid | `--randomness` too high | Lower it, or raise `--min-len` |

## References

- GenMol: A Drug Discovery Generalist with Discrete Diffusion (ICML 2025)
