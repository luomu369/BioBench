#!/usr/bin/env bash
# Install everything the molecular-dynamics skills need.
#
# The awkward parts, all of which cost time to discover:
#   * openff-toolkit and friends are yanked on PyPI, so pip skips them
#     unless the version is pinned explicitly.
#   * python-constraint is Python 2 code and will not build; openff only
#     needs it for E/Z stereo, so a shim is installed instead.
#   * am1-bcc charges need AmberTools' sqm, which only conda-forge ships
#     for aarch64.
#
# Idempotent — safe to re-run.
set -e

ENV=/home/asus_gx10/miniconda3/envs/openmm
PY=$ENV/bin/python
SITE=$ENV/lib/python3.12/site-packages

echo "==> conda env"
if [ ! -d "$ENV" ]; then
  conda create -y -n openmm python=3.12
fi

echo "==> activating"
source /home/asus_gx10/miniconda3/etc/profile.d/conda.sh
conda activate openmm

echo "==> openmm, mdtraj, pdbfixer (PyPI mirror, no proxy needed)"
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  openmm mdtraj pdbfixer lxml networkx cachetools

echo "==> openff-toolkit (yanked on PyPI — versions must be pinned)"
# 0.18.0 / 0.3.2 / 0.1.16 are all yanked, but each has a py3-none-any wheel.
pip install "openff-toolkit==0.18.0" \
            "openff-units==0.3.2" \
            "openff-utilities==0.1.16" || {
  echo "    mirror lacked them; retrying against upstream PyPI"
  export https_proxy=http://127.0.0.1:6450 http_proxy=http://127.0.0.1:6450
  pip install "openff-toolkit==0.18.0" \
              "openff-units==0.3.2" \
              "openff-utilities==0.1.16"
}

echo "==> constraint shim (python-constraint is Python 2, unbuildable)"
if [ ! -f "$SITE/constraint.py" ]; then
  cat > "$SITE/constraint.py" <<'SHIM'
"""Minimal stand-in for the `constraint` package.

openff-toolkit imports `constraint.Problem` inside
`_assign_rdmol_bonds_stereo`, which solves a CSP to place E/Z bond
directions. The upstream `python-constraint` package is Python 2 code
(`except OSError, ex:`) and no longer builds on Python 3.12, so the import
fails before the function can check whether it has any work to do.

Molecules with no stereogenic bonds return before touching the solver, so
this shim only has to satisfy the import. `Problem` raises if actually
used, which is the honest outcome — better than a silent wrong answer.
"""


class Problem:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "constraint.Problem is a shim: openff-toolkit needs "
            "python-constraint for E/Z stereo, which is unbuildable on "
            "Python 3.12. This molecule has no stereogenic bonds, so the "
            "solver should not have been reached."
        )
SHIM
  echo "    installed $SITE/constraint.py"
else
  echo "    already present"
fi

echo "==> ambertools (provides sqm for am1-bcc charges; aarch64 only on conda-forge)"
if ! command -v "$ENV/bin/sqm" > /dev/null; then
  export https_proxy=http://127.0.0.1:6450 http_proxy=http://127.0.0.1:6450
  conda install -y -c conda-forge ambertools
else
  echo "    already present"
fi

echo
echo "==> verifying"
"$PY" - <<'CHECK'
from openmm import Platform
import openmm, mdtraj, pdbfixer, lxml, networkx, cachetools
from openff.toolkit.topology import Molecule
import constraint  # the shim
print(f"  openmm            {openmm.__version__}")
print(f"  mdtraj            {mdtraj.__version__}")
print(f"  platforms         {[Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]}")
m = Molecule.from_smiles("CCO")
print(f"  openff Molecule   ok ({m.n_atoms} atoms)")
CHECK

if [ -x "$ENV/bin/sqm" ]; then
  echo "  sqm               $("$ENV/bin/sqm" -h 2>&1 | head -1)"
else
  echo "  sqm               MISSING — am1-bcc will fail"
  exit 1
fi

echo
echo "All set. Run the preflight for a fuller check:"
echo "  python3 /home/asus_gx10/biobench/skills/molecular-dynamics-deploy/scripts/preflight.py"
