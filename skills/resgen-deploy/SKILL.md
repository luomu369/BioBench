---
name: resgen-deploy
description: '**DEPLOYMENT SKILL** — Install ResGen (pocket-aware 3D molecular generation) and its weights, and verify the installation. USE FOR: setting up ResGen from scratch; downloading the val_172 checkpoint; fixing torch_geometric / torch_cluster / pyg-lib import errors; checking that the model loads and can generate; diagnosing slow generation. DO NOT USE FOR: generating molecules (use pocket-molecule-design); docking a known ligand (use molecular-docking); publishing results (use structure-viewer). INVOKES: shell tools (curl/git/pip), file system tools. Needs a CUDA GPU for reasonable speed; CPU works but is very slow.'
---

# ResGen Deployment

ResGen generates 3D molecules conditioned on a protein pocket — given a
receptor structure and a reference ligand position, it proposes new
molecules that fit that pocket. This skill covers **installation only**.

To actually generate molecules, see `pocket-molecule-design`.

## When to Use

- User wants to install ResGen
- `ModuleNotFoundError: torch_scatter` / `torch_cluster` / `pyg-lib`
- `Weights only load failed` when loading the checkpoint
- `knn_graph requires pyg-lib`
- Generation is unexpectedly slow
- User asks whether ResGen is ready

## Requirements

| Resource | Need |
|----------|------|
| GPU | CUDA-capable; ~10 min for 20 molecules on a small pocket |
| Disk | ~1 GB (repo + weights) |
| RAM | 8 GB+ |
| Python | 3.10+ |

## Installation

### 1. Clone the repo

```bash
mkdir -p ~/tools && cd ~/tools
git clone --depth 1 https://github.com/OdinZhang/ResGen.git
cd ResGen
```

### 2. Get the weights

The checkpoint is **not** in the repo — it lives on Google Drive, which
serves a "can't scan this file" interstitial for anything over ~25 MB.
A plain `curl` gets HTML, not the weights.

```bash
# 1. fetch the warning page and pull the uuid out of its form
curl -sL "https://drive.google.com/uc?export=download&id=1RoVnHBLuPGFh2qGlCMoCRKtY-Rribp0j" -o /tmp/gd.html
UUID=$(grep -oE 'name="uuid" value="[^"]+' /tmp/gd.html | cut -d'"' -f4)

# 2. download from drive.usercontent.google.com with confirm=t and that uuid
curl -sL "https://drive.usercontent.google.com/download?id=1RoVnHBLuPGFh2qGlCMoCRKtY-Rribp0j&export=download&confirm=t&uuid=$UUID" \
  -o ckpt/val_172.pt
rm /tmp/gd.html
```

Verify it is a PyTorch checkpoint, not HTML:

```bash
file ckpt/val_172.pt   # → Zip archive data (PyTorch checkpoints are zip)
ls -la ckpt/val_172.pt # → ~45 MB
```

### 3. Install dependencies

ResGen's own `resgen.yml` pins Python 3.8 / PyTorch 1.11 / CUDA 11.3 and
will not build on a modern box. Install into an existing torch environment
instead — reusing its torch avoids a second 2.5 GB download.

```bash
conda activate boltzgen          # or any env with a working torch+CUDA
pip install easydict lmdb torch_geometric
```

`torch_scatter` and `torch_cluster` need compiling and must match the
installed torch. Two things make them fail by default:

- pip's build isolation hides torch, so the build cannot find it
- the default PyPI index may be unreachable behind a proxy

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export TORCH_CMAKE_PATH=$(python -c "import torch,os;print(os.path.dirname(torch.__file__))")
pip install --no-build-isolation torch_scatter torch_cluster
```

### 4. Patch three incompatibilities

The repo targets 2022-era libraries. Three edits are needed.

**a. `torch.load` and `weights_only`** — PyTorch 2.6+ defaults to
`weights_only=True`, and the checkpoint stores its config as an
`EasyDict`, which that mode rejects.

```python
# gen.py
ckpt = torch.load(args.check_point, map_location=args.device,
                   weights_only=False)
```

**b. `subgraph` moved** — `torch_geometric.utils.subgraph` became
`torch_geometric.utils._subgraph` in 2.4+.

```python
# utils/transforms.py
try:
    from torch_geometric.utils.subgraph import subgraph
except ImportError:
    from torch_geometric.utils import subgraph
```

**c. `knn_graph` needs pyg-lib** — `torch_geometric.nn`'s `knn_graph`
delegates to `pyg-lib`, yet another compiled dependency. `torch_cluster`
provides the same functions and is already installed.

```python
# utils/transforms.py
from torch_cluster import knn_graph      # was: from torch_geometric.nn.pool import knn_graph

# models/ResGen.py
from torch_cluster import knn, knn_graph  # was: from torch_geometric.nn import knn, knn_graph
```

### 5. Verify

```bash
python3 /home/asus_gx10/biobench/skills/resgen-deploy/scripts/preflight.py
```

Checks the repo, the checkpoint, every import, and that the model
instantiates. Exits non-zero on failure.

## Tuning generation speed

`configs/sample.yml` ships with `num_samples: 100` and `beam_size: 500`.
That is a large search, and `get_next` processes candidates **one at a
time** — a single-threaded loop over the beam. On a 900-residue receptor
this runs for hours with no output.

| Setting | Shipped | Practical | Effect |
|---------|---------|-----------|--------|
| `num_samples` | 100 | 20 | molecules to produce |
| `beam_size` | 500 | 20 | candidates per step; the dominant cost |
| `max_steps` | 50 | 50 | atoms per molecule |

`beam_size: 20` gives ~11 molecules in about 10 minutes on a DGX Spark.
Raise it only if the pocket is small or quality is poor.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `file ckpt/val_172.pt` says HTML | Got the Drive interstitial, not the file | Use the `drive.usercontent.google.com` URL with `confirm=t` and `uuid` |
| `Weights only load failed ... EasyDict` | PyTorch 2.6+ default | `weights_only=False` |
| `No module named torch_geometric.utils.subgraph` | Module moved in pyg 2.4+ | Try both import paths |
| `knn_graph requires pyg-lib>=0.6.0` | pyg delegates to pyg-lib | Import `knn_graph` from `torch_cluster` |
| `Failed to build torch_scatter ... No module named 'torch'` | pip build isolation | `--no-build-isolation` + `TORCH_CMAKE_PATH` |
| `SSL: UNEXPECTED_EOF` on pip | Proxy blocks the CDN | `unset http_proxy https_proxy`, or use a mirror |
| Generation produces nothing for an hour | `beam_size: 500` | Lower to 20 |
| `pocket residues is None` | Reference ligand not near the protein | Check the SDF and PDB share a coordinate frame |
| `invalid literal for int() with base 10: 'EU'` | Biopython was given mmCIF | Convert to PDB first (gemmi) |

## References

- [ResGen GitHub](https://github.com/OdinZhang/ResGen)
- [Pocket-based 3D molecular generation, *Nature Machine Intelligence* (2024)](https://doi.org/10.1038/s42256-024-00815-9)
