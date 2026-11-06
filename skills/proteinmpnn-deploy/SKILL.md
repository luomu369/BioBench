---
name: proteinmpnn-deploy
description: '**DEPLOYMENT SKILL** — Install ProteinMPNN (inverse folding / sequence design) on a CUDA machine and verify the installation. USE FOR: setting up ProteinMPNN from scratch; checking GPU/CUDA readiness; diagnosing install or import failures; verifying an existing installation. DO NOT USE FOR: designing sequences (use sequence-design); designing binders de novo (use binder-design); publishing results (use structure-viewer). INVOKES: shell tools (conda/pip), file system tools, GPU diagnostics. Requires GPU with CUDA 12.x+ and ~2 GB free disk.'
---

# ProteinMPNN Deployment

ProteinMPNN designs amino acid sequences for a fixed protein backbone. This
skill covers **installation only**.

To actually design sequences, see the `sequence-design` skill. To show a
structure in the viewer, see `structure-viewer`.

## When to Use

- User wants to install ProteinMPNN on a new machine
- Import or runtime errors need diagnosis
- User asks whether ProteinMPNN is ready to use

## Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| GPU VRAM | 4 GB | 8 GB+ |
| CUDA | 12.0 | 12.4+ |
| Disk | 2 GB | 5 GB |
| RAM | 8 GB | 16 GB |
| Python | 3.10+ | 3.12 |

ProteinMPNN is small — it runs comfortably alongside other models, and a
typical design takes seconds rather than minutes.

## Installation

### Step 1: Create the environment

```bash
conda create -n proteinmpnn python=3.12 -y
conda activate proteinmpnn
```

### Step 2: Install PyTorch with CUDA

Match the CUDA build to your driver. Use the platform driver diagnostics, then verify `torch.cuda.is_available()` in the target environment.

Install PyTorch with CUDA — see `_shared/pytorch-cuda.md` for which wheel
matches the driver, and the check that confirms it is using the GPU.

### Step 3: Install ProteinMPNN

```bash
pip install proteinmpnn biotite
```

- `proteinmpnn` — the model and CLI
- `biotite` — **required** for mmCIF → PDB conversion (see below)

### Step 4: Install setuptools

The CLI imports `pkg_resources`, which setuptools 81+ removed:

```bash
pip install "setuptools<81"
```

### Step 5: Verify

```bash
python -c "import proteinmpnn, torch; print(torch.cuda.is_available())"
protein_mpnn_run --help
```

## Weights

**No separate download needed.** The PyPI wheel bundles all checkpoints inside
the package:

```
site-packages/proteinmpnn/data/
├── vanilla_model_weights/   v_48_002, v_48_010, v_48_020, v_48_030
├── ca_model_weights/        v_48_002, v_48_010, v_48_020
└── soluble_model_weights/   v_48_010, v_48_020
```

> **Do not pass `--path-to-model-weights`.** That flag belongs to the upstream
> GitHub repo. The PyPI package ignores it and loads weights from its bundled
> `data/` directory — passing it causes `UnboundLocalError: checkpoint_path`.

## Model selection

| Model | Use |
|-------|-----|
| `v_48_002` | Lowest backbone noise (0.02 Å) |
| `v_48_010` | 0.10 Å noise |
| `v_48_020` | 0.20 Å noise — **default, best general choice** |
| `v_48_030` | 0.30 Å noise — most robust to backbone error |

Add `--use-soluble-model` for soluble-protein-trained weights, or `--ca-only`
to design from a Cα-only backbone (uses `ca_model_weights`).

## Verifying an existing installation

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/proteinmpnn-deploy/scripts/preflight.py
```

Checks the conda env, torch/CUDA, the `proteinmpnn` import, `pkg_resources`,
bundled weights, and `biotite`. Exits non-zero if anything is wrong.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: pkg_resources` | setuptools 81+ removed it | `pip install "setuptools<81"` |
| `UnboundLocalError: checkpoint_path` | Passed `--path-to-model-weights` | Remove that flag |
| `ValueError: could not convert string to float` | Input is mmCIF, not PDB | Convert with biotite first |
| `torch.cuda.is_available()` is False | Driver/toolkit mismatch | Reinstall torch with the matching CUDA build |
| numpy conflict warning | biotite pulled numpy 2.x | Harmless; pin `numpy<2` if it matters |

## References

- [ProteinMPNN GitHub](https://github.com/dauparas/ProteinMPNN)
- [ProteinMPNN on PyPI](https://pypi.org/project/proteinmpnn/)
