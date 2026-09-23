#!/usr/bin/env python3
"""Preflight check for a ResGen installation.

Verifies the repo, the checkpoint, every import ResGen needs, that the
model class instantiates, and reports the sampling config. Read-only.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path("/home/asus_gx10/tools/ResGen")
CKPT = REPO / "ckpt" / "val_172.pt"
CONFIG = REPO / "configs" / "sample.yml"

# Any env with a working torch+CUDA will do; ResGen has no env of its own.
CANDIDATE_PY = [
    Path("/home/asus_gx10/miniconda3/envs/boltzgen/bin/python"),
    Path("/home/asus_gx10/miniconda3/envs/genmol/bin/python"),
    Path("/home/asus_gx10/miniconda3/envs/resgen/bin/python"),
]

IMPORTS = [
    "torch", "torch_geometric", "torch_scatter", "torch_cluster",
    "rdkit", "Bio", "easydict", "lmdb", "yaml", "tqdm", "numpy",
]


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def find_python():
    for p in CANDIDATE_PY:
        if p.is_file():
            return p
    return None


def main():
    results = []

    results.append(check("ResGen repo", REPO.is_dir(), str(REPO)))
    if not REPO.is_dir():
        print("\nClone it: git clone --depth 1 https://github.com/OdinZhang/ResGen.git ~/tools/ResGen")
        return 1

    for f in ("gen.py", "models/ResGen.py", "utils/transforms.py"):
        results.append(check(f"file {f}", (REPO / f).is_file()))

    # The checkpoint is a zip (PyTorch's format); HTML means the Google
    # Drive interstitial was saved instead of the weights.
    if not CKPT.is_file():
        results.append(check("checkpoint val_172.pt", False, "missing"))
    else:
        head = CKPT.read_bytes()[:4]
        is_zip = head[:2] == b"PK"
        size = CKPT.stat().st_size / 1e6
        results.append(check("checkpoint val_172.pt", is_zip,
                             f"{size:.0f} MB" if is_zip
                             else "not a PyTorch checkpoint (HTML?)"))

    py = find_python()
    if py is None:
        results.append(check("python with torch", False,
                             "none of the candidate envs exist"))
        print(f"\nsummary: {sum(results)} passed, {len(results) - sum(results)} failed")
        return 1
    results.append(check("python with torch", True, str(py)))

    # Imports
    missing = []
    for m in IMPORTS:
        r = subprocess.run([str(py), "-c", f"import {m}"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            missing.append(m)
    results.append(check("all imports resolve", not missing,
                          "missing: " + ", ".join(missing) if missing
                          else f"{len(IMPORTS)} modules"))

    # The three patches, checked by behaviour rather than by grepping source
    r = subprocess.run(
        [str(py), "-c",
         "import sys; sys.path.insert(0, '%s');"
         "from utils.transforms import subgraph, knn_graph;"
         "from models.ResGen import ResGen; print('ok')" % REPO],
        capture_output=True, text=True, timeout=600, cwd=str(REPO))
    ok = r.returncode == 0
    detail = "model + transforms import" if ok else (r.stderr or r.stdout).strip()[-200:]
    results.append(check("ResGen modules import", ok, detail))

    # Sampling config — the shipped values are unusably slow
    if CONFIG.is_file():
        text = CONFIG.read_text()
        beam = next((l.split(":")[1].strip() for l in text.splitlines()
                     if l.strip().startswith("beam_size")), "?")
        n = next((l.split(":")[1].strip() for l in text.splitlines()
                  if l.strip().startswith("num_samples")), "?")
        slow = beam not in ("?",) and beam.isdigit() and int(beam) > 100
        results.append(check("sampling config", not slow,
                             f"beam_size={beam}, num_samples={n}"
                             + (" — too slow, lower beam_size" if slow else "")))
    else:
        results.append(check("sampling config", False, "configs/sample.yml missing"))

    print(f"\nsummary: {sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
