#!/usr/bin/env python3
"""Preflight check for BoltzGen deployment.

Verifies GPU, CUDA, Python, package, and weight readiness without running
any design workload. Read-only: does not install or modify anything.

Exit codes:
    0  all checks passed
    1  one or more checks failed
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

MIN_VRAM_GB = 24.0
MIN_DISK_GB = 50.0
EXPECTED_CKPTS = {
    "boltzgen1_diverse.ckpt": 1.8,
    "boltzgen1_adherence.ckpt": 1.8,
    "boltzgen1_ifold.ckpt": 1.8,
    "boltz2_conf_final.ckpt": 1.9,
    "boltz2_aff.ckpt": 1.9,
}


def check(label, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def check_gpu():
    if not shutil.which("nvidia-smi"):
        return check("nvidia-smi present", False, "not found in PATH")
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return check("GPU detected", True, out)
    except Exception as e:
        return check("GPU detected", False, str(e))


def check_torch():
    try:
        import torch
    except ImportError:
        return check("torch importable", False, "pip install torch")
    ok = torch.cuda.is_available()
    detail = f"torch {torch.__version__}"
    if ok:
        name = torch.cuda.get_device_name(0)
        free, total = torch.cuda.mem_get_info()
        detail += f", {name}, {free/1024**3:.1f}/{total/1024**3:.1f} GB free"
        ok = free / 1024**3 >= MIN_VRAM_GB
        if not ok:
            detail += f" (below {MIN_VRAM_GB} GB minimum)"
    else:
        detail += ", CUDA unavailable"
    return check("torch + CUDA", ok, detail)


def check_boltzgen():
    try:
        import boltzgen  # noqa: F401
        return check("boltzgen importable", True)
    except ImportError:
        return check("boltzgen importable", False, "pip install boltzgen")


def check_weights(weights_dir):
    d = Path(weights_dir)
    if not d.is_dir():
        return check("weights directory", False, f"{d} not found")

    found = {}
    for ckpt in d.rglob("*.ckpt"):
        found[ckpt.name] = ckpt.stat().st_size / 1024**3

    missing = [n for n in EXPECTED_CKPTS if n not in found]
    if missing:
        return check("weights present", False, f"missing: {', '.join(missing)}")

    # LFS pointers are ~76 bytes; real checkpoints are gigabytes.
    pointers = [n for n, sz in found.items() if sz < 0.01]
    if pointers:
        return check("weights are real files", False,
                     f"LFS pointers only: {', '.join(pointers)}")

    total = sum(found.values())
    return check("weights present", True, f"{len(found)} checkpoints, {total:.1f} GB")


def check_disk(path):
    usage = shutil.disk_usage(path)
    free_gb = usage.free / 1024**3
    return check("disk space", free_gb >= MIN_DISK_GB, f"{free_gb:.0f} GB free")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", default=str(Path.home() / "models" / "boltzgen-1"),
                    help="directory containing BoltzGen checkpoints")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    results = {
        "gpu": check_gpu(),
        "torch": check_torch(),
        "boltzgen": check_boltzgen(),
        "weights": check_weights(args.weights),
        "disk": check_disk(str(Path(args.weights).anchor or "/")),
    }

    if args.json:
        print(json.dumps(results, indent=2))

    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
