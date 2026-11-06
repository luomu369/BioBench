---
name: boltzgen-deploy
description: '**DEPLOYMENT SKILL** — Install BoltzGen and its model weights on a CUDA machine, and verify the installation is healthy. USE FOR: setting up BoltzGen from scratch; downloading or repairing model weights; checking GPU/CUDA readiness; diagnosing install failures; verifying an existing installation. DO NOT USE FOR: running a binder design (use binder-design); publishing results (use structure-viewer); sequence design on a fixed backbone (use proteinmpnn-deploy). INVOKES: shell tools (conda/pip), file system tools, GPU diagnostics. Requires GPU with CUDA 12.x+ and ~20 GB free disk.'
---

# BoltzGen Deployment

BoltzGen is a universal binder design model. This skill covers **installation
only** — creating the environment, fetching weights, and verifying the result.

To actually run a design, see the `binder-design` skill. To show a structure in
the viewer, see `structure-viewer`.

## When to Use

- User wants to install BoltzGen on a new machine
- User needs to download or verify model weights
- Install or import errors need diagnosis
- User asks whether BoltzGen is ready to use

## Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| GPU VRAM | 24 GB | 40–80 GB (A100/H100/L40S) |
| CUDA | 12.0 | 12.4+ |
| Disk | 50 GB | 150 GB |
| RAM | 16 GB | 64 GB+ |
| Python | 3.11+ | 3.12 |

**Unified-memory machines** (e.g. unified-memory GPU): VRAM is shared with system
RAM. Check with `free -g` before running large designs.

## Installation

### Step 1: Create the environment

```bash
conda create -n boltzgen python=3.12 -y
conda activate boltzgen
```

### Step 2: Install PyTorch with CUDA

Match the CUDA build to your driver. Use the platform driver diagnostics, then verify `torch.cuda.is_available()` in the target environment.

Install PyTorch with CUDA — see `_shared/pytorch-cuda.md` for which wheel
matches the driver, and the check that confirms it is using the GPU.

### Step 3: Install BoltzGen

```bash
pip install boltzgen
```

This pulls in `cuequivariance`, `biotite`, `rdkit`, `pytorch-lightning`, and
other dependencies. On aarch64, `cuequivariance_ops_cu12` ships native wheels.

### Step 4: Verify

```bash
python -c "import boltzgen, torch; print(torch.cuda.is_available())"
boltzgen --help
```

## Weight Download

Weights total ~9.5 GB and come from Hugging Face.

### Option A: Official endpoint

```bash
boltzgen download all --cache /path/to/models/boltzgen
```

### Option B: Mirror endpoint (China / restricted networks)

```bash
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download \
  boltzgen/boltzgen-1 --local-dir /path/to/models/boltzgen-1
```

> **Note:** `boltzgen download` may leave LFS pointer files (76 bytes) instead
> of real weights. If `.ckpt` files are suspiciously small, use Option B with
> `snapshot_download`, which resolves LFS blobs correctly.

### Expected artifacts

| File | Size | Purpose |
|------|------|---------|
| `boltzgen1_diverse.ckpt` | ~1.8 GB | Diverse binder generation |
| `boltzgen1_adherence.ckpt` | ~1.8 GB | Spec-adherent generation |
| `boltzgen1_structuretrained_small.ckpt` | ~2.1 GB | Structure-trained variant |
| `boltzgen1_ifold.ckpt` | ~1.8 GB | Inverse folding |
| `boltz2_conf_final.ckpt` | ~1.9 GB | Structure/confidence |
| `boltz2_aff.ckpt` | ~1.9 GB | Binding affinity |
| `mols.zip` | ~1.7 GB | Reference molecule data |

### Minimal set for protein-only design

If you only need protein binder generation (no inverse folding, no small
molecules), skip two artifacts entirely:

| Skip | Size saved | Why |
|------|-----------|-----|
| `boltzgen1_ifold.ckpt` | 1.8 GB | Only used by the `inverse_folding` step |
| `mols.zip` | 1.7 GB | Only used for small-molecule specs and spec validation |

The remaining five checkpoints (~9.7 GB) are sufficient. See `binder-design`
for the flags that skip those steps.

> **`mols.zip` lives in a separate dataset repo** (`boltzgen/inference-data`),
> not in `boltzgen/boltzgen-1`. This is why `boltzgen download all` can appear
> to stall — it fetches from two repos with no visible progress.

## Verifying an existing installation

```bash
MODELS="${MODELS:-$HOME/models}"
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/boltzgen-deploy/scripts/preflight.py \
  --weights $MODELS/boltzgen-1
```

Checks GPU, CUDA, torch, the `boltzgen` import, checkpoint sizes, and disk
space. Exits non-zero if anything is wrong. Add `--json` for machine-readable
output.

To re-download missing or truncated checkpoints:

```bash
MODELS="${MODELS:-$HOME/models}"
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
bash $BB/skills/boltzgen-deploy/scripts/fetch_weights.sh \
  --dest $MODELS/boltzgen-1
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `.ckpt` files are 76 bytes | LFS pointers not resolved | Re-download with `snapshot_download` |
| `cuequivariance` import error | Wrong CUDA wheel | Match torch CUDA to driver |
| `boltzgen: command not found` | Env not activated | `conda activate boltzgen` |
| `torch.cuda.is_available()` is False | Driver/toolkit mismatch | Reinstall torch with the matching CUDA build |
| Import fails on aarch64 | Old package version | `pip install -U boltzgen` |

## References

- [BoltzGen GitHub](https://github.com/HannesStark/boltzgen)
- [BoltzGen weights on HF](https://huggingface.co/boltzgen/boltzgen-1)
