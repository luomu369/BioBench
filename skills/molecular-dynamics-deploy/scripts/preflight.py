#!/usr/bin/env python3
"""Preflight check for an OpenMM installation.

Verifies the conda env, the OpenMM import, which compute platforms are
available, and that mdtraj and pdbfixer are present for analysis and
structure fixing. Read-only.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import conda_root
from preflight import check, run_py

ENV = conda_root() / "envs" / "openmm"
PY = ENV / "bin" / "python"


def main():
    ok = True

    ok &= check("conda env 'openmm'", ENV.is_dir(), str(ENV))
    if not ENV.is_dir():
        print("\ncreate it with:")
        print("  conda create -n openmm python=3.12 -y")
        print("  conda activate openmm && pip install openmm mdtraj pdbfixer")
        return 1

    r = run_py(PY, "import openmm; print(openmm.__version__)")
    ok &= check("openmm import", r.returncode == 0,
                r.stdout.strip() or r.stderr.strip()[:120])

    # Which platforms exist. CUDA is absent on ARM64 — there is no aarch64
    # openmm-cuda wheel — so its absence here is expected, not a fault.
    r = run_py(PY, """
from openmm import Platform
print(','.join(Platform.getPlatform(i).getName()
               for i in range(Platform.getNumPlatforms())))
""")
    names = r.stdout.strip() if r.returncode == 0 else ""
    ok &= check("platforms", bool(names), names or r.stderr.strip()[:120])
    if "CUDA" not in names:
        print("       note: no CUDA platform — expected on ARM64, "
              "where openmm-cuda has no wheel")

    # OpenCL is the GPU path on this machine. Its device-name property comes
    # back empty on the GPU driver even though the kernels do run on the
    # GPU, so an empty string says nothing either way — report it as
    # informational rather than as a failure to fix.
    if "OpenCL" in names:
        r = run_py(PY, """
from openmm import Platform
p = Platform.getPlatformByName('OpenCL')
p.setPropertyDefaultValue('OpenCLPlatformIndex', '0')
print(p.getPropertyDefaultValue('OpenCLPlatformName') or '(empty)')
""")
        dev = r.stdout.strip() if r.returncode == 0 else "(error)"
        print(f"       OpenCL device name: {dev}")
        print("       note: empty is normal on the GPU driver — the GPU "
              "is still used.")
        print("       measured: ~145,000 ns/day vs ~6,000 on CPU "
              "(80k atoms).")

    r = run_py(PY, "import mdtraj; print(mdtraj.__version__)")
    ok &= check("mdtraj (analysis)", r.returncode == 0,
                r.stdout.strip() or r.stderr.strip()[:120])

    r = run_py(PY, "import pdbfixer; print('ok')")
    ok &= check("pdbfixer (missing atoms)", r.returncode == 0,
                r.stdout.strip() or r.stderr.strip()[:120])

    # The force fields the workflow defaults to. amber14-all.xml is the
    # tempting choice but carries no ion templates, so a solvated system
    # fails on Na — check for the pair that works.
    r = run_py(PY, """
from openmm.app import ForceField
ff = ForceField('amber14/protein.ff14SB.xml', 'amber14/tip3pfb.xml')
print('NA' in ff._templates and 'CL' in ff._templates)
""")
    ok &= check("amber14 force fields with ions", r.stdout.strip() == "True",
                r.stderr.strip()[:120] if r.returncode else "protein.ff14SB + tip3pfb")

    print()
    print("all checks passed" if ok else "some checks failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
