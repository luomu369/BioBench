#!/usr/bin/env python3
"""Preflight check for an AutoDock Vina installation.

Verifies the binary, the Python toolchain (meeko/rdkit/gemmi), and that a
ligand can actually be converted to PDBQT. Read-only.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONDA = Path("/home/asus_gx10/miniconda3")
ENV = CONDA / "envs" / "docking"
PY = ENV / "bin" / "python"

CANDIDATES = [
    Path("/home/asus_gx10/tools/vina/vina"),
    Path.home() / "tools/vina/vina",
]


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def run(code, timeout=300):
    return subprocess.run([str(PY), "-c", code], capture_output=True,
                          text=True, timeout=timeout)


def main():
    results = []

    # ---- binary ----
    binary = shutil.which("vina") or next(
        (str(c) for c in CANDIDATES if c.is_file()), None)
    if binary is None:
        check("vina binary", False, "not on PATH and not in ~/tools/vina")
        print("\nInstall: see skills/vina-deploy/SKILL.md")
        return 1
    results.append(check("vina binary", True, binary))

    r = subprocess.run([binary, "--version"], capture_output=True,
                       text=True, timeout=60)
    version = (r.stdout + r.stderr).strip().splitlines()
    version = version[0].strip() if version else ""
    results.append(check("reports version", bool(version), version))

    # ---- python toolchain ----
    if not PY.is_file():
        check("conda env 'docking'", False, f"{ENV} not found")
        print("\nCreate it: conda create -n docking python=3.12 -y")
        return 1
    results.append(check("conda env 'docking'", True, str(ENV)))

    for mod, label in [("meeko", "meeko (SMILES→PDBQT)"),
                       ("rdkit", "rdkit"),
                       ("gemmi", "gemmi (mmCIF reader)"),
                       ("numpy", "numpy"),
                       ("scipy", "scipy")]:
        r = run(f"import {mod}")
        results.append(check(label, r.returncode == 0,
                             "" if r.returncode == 0
                             else f"pip install {mod}"))

    # ---- ligand conversion smoke test ----
    with tempfile.TemporaryDirectory() as td:
        r = run(
            "from meeko import MoleculePreparation\n"
            "from rdkit import Chem\n"
            "from rdkit.Chem import AllChem\n"
            "m = Chem.MolFromSmiles('CCO')\n"
            "m = Chem.AddHs(m)\n"
            "AllChem.EmbedMolecule(m, randomSeed=42)\n"
            "p = MoleculePreparation()\n"
            "p.prepare(m)\n"
            "s = p.write_pdbqt_string()\n"
            "assert 'ATOM' in s, 'no ATOM records'\n"
            "print(len(s.splitlines()))",
            timeout=300,
        )
        results.append(check("ligand → PDBQT", r.returncode == 0,
                             f"{r.stdout.strip()} lines" if r.returncode == 0
                             else r.stderr.strip()[-160:]))

    print(f"\nsummary: {sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
