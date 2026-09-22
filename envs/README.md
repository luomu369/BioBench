# Conda environments for BioBench skills.

Each skill that needs an isolated environment documents its creation here.

## boltzgen

```bash
conda create -n boltzgen python=3.12 -y
conda activate boltzgen

# Match the CUDA build to your driver (check `nvidia-smi`).
# CUDA 12.4:
pip install torch --index-url https://download.pytorch.org/whl/cu124
# CUDA 13.0 (DGX Spark / GB10):
pip install torch --index-url https://download.pytorch.org/whl/cu130

pip install boltzgen
```

Verified on: Python 3.12, torch 2.14.0+cu130, boltzgen 0.3.2, aarch64.

## comfyui

```bash
conda create -n comfyui python=3.13 -y
conda activate comfyui

pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

cd /path/to/ComfyUI
pip install -r requirements.txt
pip install -r manager_requirements.txt

# Optional: SageAttention acceleration (not in requirements.txt)
pip install sageattention
```

Verified on: Python 3.13.15, torch 2.14.0+cu130, ComfyUI 0.37.0, aarch64.
