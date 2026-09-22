---
name: genmol-deploy
description: '**DEPLOYMENT SKILL** — Install GenMol (NVIDIA discrete-diffusion small-molecule generator) on a CUDA machine and verify the installation. USE FOR: setting up GenMol from scratch; downloading model weights; diagnosing install or import failures; checking GPU readiness; verifying an existing installation. DO NOT USE FOR: generating molecules (use molecule-generation); protein design (use binder-design); publishing results (use structure-viewer). INVOKES: shell tools (conda/pip/git), file system tools, GPU diagnostics. Requires NVIDIA GPU with CUDA 12.x+ and ~3 GB free disk.'
---

# GenMol Deployment

GenMol generates novel small molecules as SAFE strings using masked discrete
diffusion. This skill covers **installation only**.

To actually generate molecules, see the `molecule-generation` skill. To show a
structure in the viewer, see `structure-viewer`.

## When to Use

- User wants to install GenMol on a new machine
- Import or checkpoint-loading errors need diagnosis
- User asks whether GenMol is ready to use

## Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| GPU VRAM | 4 GB | 8 GB+ |
| CUDA | 12.0 | 12.4+ |
| Disk | 3 GB | 5 GB |
| RAM | 8 GB | 16 GB |
| Python | 3.10+ | 3.12 |

GenMol is small (89M parameters, 1.3 GB weights) and runs in seconds.

## Installation

### Step 1: Create the environment

```bash
conda create -n genmol python=3.12 -y
conda activate genmol
```

### Step 2: Install PyTorch with CUDA

Match the CUDA build to your driver. Check with `nvidia-smi`.

```bash
# CUDA 12.4
pip install torch --index-url https://download.pytorch.org/whl/cu124

# CUDA 13.0 (DGX Spark / GB10, driver 580+)
pip install torch --index-url https://download.pytorch.org/whl/cu130
```

### Step 3: Clone and install GenMol

```bash
git clone --depth 1 https://github.com/NVIDIA-BioNeMo/GenMol.git /tmp/genmol
cd /tmp/genmol
pip install -e .
```

### Step 4: Install the remaining dependencies

```bash
pip install safe-mol openbabel-wheel easydict einops jaxtyping \
            pandas omegaconf hydra-core huggingface_hub
```

### Step 5: Fix the `safe` package import

`safe-mol` installs a module named `safe`, and its `__init__.py` pulls in
`transformers` symbols that no longer exist. Comment out its contents and
expose only the converter:

```bash
SP=$CONDA_PREFIX/lib/python3.12/site-packages/safe/__init__.py
cp "$SP" "$SP.orig"
sed -i 's/^/# /' "$SP"
echo 'from .converter import SAFEConverter, decode, encode' >> "$SP"
```

Adjust the `python3.12` path segment to match your Python version.

### Step 6: Verify

```bash
python -c "import genmol; print('genmol OK')"
```

## Weights

Download from Hugging Face (~1.3 GB):

```bash
HF_ENDPOINT=https://hf-mirror.com python -c "
from huggingface_hub import snapshot_download
snapshot_download('nvidia/NV-GenMol-89M-v1',
                  local_dir='/home/asus_gx10/models/genmol/NV-GenMol-89M-v1',
                  max_workers=8)
"
```

| Model | Size | Notes |
|-------|------|-------|
| `nvidia/NV-GenMol-89M-v1` | 1.3 GB | Original release |
| `nvidia/NV-GenMol-89M-v2` | 1.3 GB | Extended SAFE syntax, better de novo |

## Loading the checkpoint

NVIDIA's checkpoint stores omegaconf config objects. PyTorch 2.6+ defaults to
`weights_only=True`, which rejects them, and lightning passes
`weights_only=None` so torch applies that default.

Patch `torch.load` before constructing the `Sampler`:

```python
import torch

_orig_load = torch.load
def _load(*a, **kw):
    if kw.get("weights_only") is None:
        kw["weights_only"] = False
    return _orig_load(*a, **kw)
torch.load = _load

from genmol.sampler import Sampler
sampler = Sampler("/path/to/model.ckpt")
```

The checkpoint is NVIDIA's official release, so full unpickling is safe. The
`molecule-generation` skill's helper script already applies this patch.

## Verifying an existing installation

```bash
python3 /home/asus_gx10/biobench/skills/genmol-deploy/scripts/preflight.py
```

Checks the conda env, torch/CUDA, the `genmol` import, the `safe` converter,
weights, and that a checkpoint actually loads. Exits non-zero on failure.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: safe_mol` | The module is named `safe`, not `safe_mol` | Import `safe.converter`; run the Step 5 fix |
| `ImportError: _CONFIG_FOR_DOC` from transformers | `safe/__init__.py` imports removed symbols | Step 5 fix |
| `UnpicklingError: omegaconf.dictconfig.DictConfig` | torch 2.6+ `weights_only` default | Patch `torch.load` (see above) |
| `ninja: build stopped` during install | `pandas==2.1.0` compiling from source | Relax the pin: `pip install "pandas>=2.2"` |
| `No matching distribution for pytdc` | PyTDC has no wheel and pulls a huge tree | Remove `pytdc` from `pyproject.toml`; only benchmarks need it |
| `torch==2.6.0` unavailable for your Python | No prebuilt wheel for that combo | Relax the pin and use your CUDA-matched torch |

## References

- [GenMol GitHub](https://github.com/NVIDIA-BioNeMo/GenMol)
- [GenMol weights on HF](https://huggingface.co/nvidia/NV-GenMol-89M-v1)
- GenMol: A Drug Discovery Generalist with Discrete Diffusion (ICML 2025)
