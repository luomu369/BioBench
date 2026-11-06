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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import env_python
from preflight import check, run_py

# The interpreter the checks run under. Importing torch in *this* process
# asks the wrong question: the script is invoked with whatever python3 is on
# PATH, which on a machine with conda is the system interpreter and has no
# torch — so a fully installed environment reports "pip install torch" and
# the reader concludes the install is broken when only the probe was.
# Every other deploy preflight resolves its env's interpreter the same way.
PY = env_python("boltzgen")

MIN_VRAM_GB = 24.0
MIN_DISK_GB = 50.0
EXPECTED_CKPTS = {
    "boltzgen1_diverse.ckpt": 1.8,
    "boltzgen1_adherence.ckpt": 1.8,
    "boltzgen1_ifold.ckpt": 1.8,
    "boltz2_conf_final.ckpt": 1.9,
    "boltz2_aff.ckpt": 1.9,
}


# Modified 2026-11-06: query the configured compute environment directly.
def check_gpu():
    if not PY.is_file():
        return check("GPU detected", False, "compute environment is missing")
    try:
        result = run_py(PY, "import torch; print(torch.cuda.is_available())", timeout=15)
        ready = result.returncode == 0 and result.stdout.strip() == "True"
        return check("GPU detected", ready,
                     "CUDA available" if ready else "CUDA unavailable in compute environment")
    except Exception as exc:
        return check("GPU detected", False, str(exc))


def check_torch():
    if not PY.is_file():
        return check("conda env 'boltzgen'", False,
                     f"{PY} not found — see SKILL.md Step 1")
    r = run_py(PY, "import torch; print(torch.__version__, "
                   "torch.cuda.is_available())")
    if r.returncode != 0:
        return check("torch importable", False,
                     (r.stderr or r.stdout).strip()[-160:])
    ver, cuda = r.stdout.strip().rsplit(" ", 1)
    ok = cuda == "True"
    detail = f"torch {ver}"
    if ok:
        name = run_py(PY, "import torch; print(torch.cuda.get_device_name(0))")
        mem = run_py(PY, "import torch; f, t = torch.cuda.mem_get_info(); "
                         "print(f, t)")
        try:
            free, total = (int(x) for x in mem.stdout.split())
        except ValueError:
            free, total = 0, 0
        detail += f", {name.stdout.strip()}, {free/1024**3:.1f}/{total/1024**3:.1f} GB free"
        ok = free / 1024**3 >= MIN_VRAM_GB
        if not ok:
            detail += f" (below {MIN_VRAM_GB} GB minimum)"
    else:
        detail += ", CUDA unavailable"
    return check("torch + CUDA", ok, detail)


def check_boltzgen():
    if not PY.is_file():
        return check("boltzgen importable", False,
                     f"{PY} not found — see SKILL.md Step 1")
    r = run_py(PY, "import boltzgen")
    return check("boltzgen importable", r.returncode == 0,
                 "" if r.returncode == 0
                 else (r.stderr or r.stdout).strip()[-160:])


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
