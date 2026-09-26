#!/usr/bin/env python3
"""Preflight check for a GenMol installation.

Verifies the conda env, torch/CUDA, the genmol import, the `safe` converter,
weights on disk, and that a checkpoint actually loads. Read-only.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import conda_root, env_python, models_root

CONDA = conda_root()
ENV = CONDA / "envs" / "genmol"
PY = env_python("genmol")
WEIGHTS = models_root() / "genmol"


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def run(code, timeout=300):
    return subprocess.run([str(PY), "-c", code], capture_output=True,
                          text=True, timeout=timeout)


def main():
    results = []

    if not PY.is_file():
        check("conda env 'genmol'", False, f"{ENV} not found")
        print("\nCreate it: conda create -n genmol python=3.12 -y")
        return 1
    results.append(check("conda env 'genmol'", True, str(ENV)))

    r = run("import torch; print(torch.__version__, torch.cuda.is_available())")
    if r.returncode == 0:
        ver, cuda = r.stdout.strip().rsplit(" ", 1)
        results.append(check("torch + CUDA", cuda == "True", f"torch {ver}"))
        if cuda == "True":
            name = run("import torch; print(torch.cuda.get_device_name(0))")
            results.append(check("GPU", True, name.stdout.strip()))
    else:
        results.append(check("torch importable", False, r.stderr.strip()[-120:]))

    r = run("import genmol; print('ok')")
    results.append(check("genmol importable", r.returncode == 0,
                         "" if r.returncode == 0 else r.stderr.strip()[-120:]))

    # The `safe` package must expose the converter (see SKILL.md Step 5)
    r = run("from safe.converter import SAFEConverter; print('ok')")
    results.append(check("safe.converter importable", r.returncode == 0,
                         "" if r.returncode == 0
                         else "run the safe/__init__.py fix in SKILL.md"))

    # Weights
    ckpts = sorted(WEIGHTS.rglob("*.ckpt")) if WEIGHTS.is_dir() else []
    if ckpts:
        total = sum(c.stat().st_size for c in ckpts) / 1024**3
        results.append(check("weights present", True,
                             f"{len(ckpts)} checkpoint(s), {total:.1f} GB"))
    else:
        results.append(check("weights present", False, f"none under {WEIGHTS}"))

    # Actually load the newest checkpoint — catches the weights_only issue
    if ckpts:
        newest = max(ckpts, key=lambda p: p.stat().st_mtime)
        r = run(
            "import torch\n"
            "_o = torch.load\n"
            "torch.load = lambda *a, **k: _o(*a, **{**k, 'weights_only': False})\n"
            "from genmol.sampler import Sampler\n"
            f"Sampler({str(newest)!r})\n"
            "print('ok')",
            timeout=600,
        )
        results.append(check("checkpoint loads", r.returncode == 0,
                             newest.name if r.returncode == 0
                             else r.stderr.strip()[-160:]))

    print(f"\nsummary: {sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
