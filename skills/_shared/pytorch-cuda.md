# PyTorch with CUDA — shared install step

Every CUDA-dependent skill on this machine installs PyTorch the same way, and
the version that works depends on the driver, not on the skill. This file is
the one place that is written down; the deploy skills reference it rather than
each carrying a copy that drifts.

## Pick the wheel by driver, not by habit

Inspect the installed driver version with your platform's driver diagnostics.
The following command checks an existing PyTorch installation and its build
runtime; it does not report the host driver version:

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

| Driver | CUDA | Wheel |
|--------|------|-------|
| 580+ (unified-memory GPU) | 13.0 | `cu130` |
| 550–575 | 12.4 | `cu124` |

The default PyPI wheel is CPU-only. Installing it on a GPU machine gives a
torch that runs but never uses the card, and the failure looks like a model
problem rather than an install problem — training is merely slow, and nothing
reports that CUDA is absent.

```bash
# CUDA 12.4
pip install torch --index-url https://download.pytorch.org/whl/cu124

# CUDA 13.0 (unified-memory GPU, driver 580+)
pip install torch --index-url https://download.pytorch.org/whl/cu130
```

## Verify before going further

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

`torch.cuda.is_available()` must print `True`. If it prints `False`, the wheel
does not match the driver — reinstall with the other index rather than
debugging the model.

## ARM64 caveat

On aarch64 (local GPU workstation), some CUDA wheels have no build. `torch` itself does;
packages that compile against it at install time may not. Where a wheel is
missing, install from source with `--no-build-isolation` so the build sees the
already-installed torch:

```bash
pip install --no-build-isolation <package>
```
