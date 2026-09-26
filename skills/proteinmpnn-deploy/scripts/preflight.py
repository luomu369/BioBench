#!/usr/bin/env python3
"""Preflight check for a ProteinMPNN installation.

Verifies the conda env, torch/CUDA, the proteinmpnn import, pkg_resources,
bundled weights, and biotite. Read-only — installs nothing.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import conda_root
from preflight import check, run_py, summary

CONDA = conda_root()
ENV = CONDA / "envs" / "proteinmpnn"
PY = ENV / "bin" / "python"

EXPECTED_WEIGHTS = {
    "vanilla_model_weights": ["v_48_002", "v_48_010", "v_48_020", "v_48_030"],
    "ca_model_weights": ["v_48_002", "v_48_010", "v_48_020"],
    "soluble_model_weights": ["v_48_010", "v_48_020"],
}


def main():
    results = []

    if not PY.is_file():
        check("conda env 'proteinmpnn'", False, f"{ENV} not found")
        print("\nCreate it: conda create -n proteinmpnn python=3.12 -y")
        return 1
    results.append(check("conda env 'proteinmpnn'", True, str(ENV)))

    r = run_py(PY, "import torch; print(torch.__version__, torch.cuda.is_available())")
    if r.returncode == 0:
        ver, cuda = r.stdout.strip().rsplit(" ", 1)
        results.append(check("torch + CUDA", cuda == "True", f"torch {ver}"))
        if cuda == "True":
            name = run_py(PY, "import torch; print(torch.cuda.get_device_name(0))")
            results.append(check("GPU", True, name.stdout.strip()))
    else:
        results.append(check("torch importable", False, r.stderr.strip()[-120:]))

    r = run_py(PY, "import proteinmpnn; print(proteinmpnn.__file__)")
    if r.returncode == 0:
        results.append(check("proteinmpnn importable", True))
        pkg = Path(r.stdout.strip()).parent
        data = pkg / "data"
        for sub, names in EXPECTED_WEIGHTS.items():
            d = data / sub
            missing = [n for n in names if not (d / f"{n}.pt").is_file()]
            results.append(check(
                f"weights: {sub}",
                not missing,
                "all present" if not missing else f"missing {missing}",
            ))
    else:
        results.append(check("proteinmpnn importable", False,
                             r.stderr.strip()[-120:]))

    r = run_py(PY, "import pkg_resources")
    results.append(check("pkg_resources", r.returncode == 0,
                         "" if r.returncode == 0
                         else "pip install 'setuptools<81'"))

    r = run_py(PY, "import biotite.structure.io")
    results.append(check("biotite (mmCIF→PDB)", r.returncode == 0,
                         "" if r.returncode == 0
                         else "pip install biotite"))

    cli = ENV / "bin" / "protein_mpnn_run"
    results.append(check("CLI present", cli.is_file(), str(cli)))

    return summary(results)


if __name__ == "__main__":
    sys.exit(main())
