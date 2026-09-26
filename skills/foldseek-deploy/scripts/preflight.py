#!/usr/bin/env python3
"""Preflight check for a Foldseek installation.

Verifies the binary exists, is executable, reports a version, and can run a
trivial search. Read-only.

Exit codes: 0 all checks passed, 1 one or more failed.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import biobench_root, foldseek_db, tools_root
from preflight import check, summary

FOLDSEEK = tools_root() / "foldseek" / "bin" / "foldseek"
DB_DIR = foldseek_db()
STRUCTURES = biobench_root() / "structures"

CANDIDATES = [
    FOLDSEEK,
    Path.home() / "tools/foldseek/bin/foldseek",
]

# The database is a flat set of files sharing a `pdb` prefix — `pdb` itself
# is the main data file, not a directory. Point searches at this directory.

# `foldseek databases` has been observed to unpack incompletely, silently
# dropping the two largest data files and the *.lookup symlinks for the
# sub-databases. Without them a search fails partway through, so check
# explicitly.
DB_REQUIRED = [
    "pdb.lookup",
    "pdb",
    "pdb_ca",
    "pdb_seq_ca.1",
    "pdb_h",
    "pdb_ss",
    "pdb_clu",
    "pdb_ca.lookup",
    "pdb_h.lookup",
    "pdb_ss.lookup",
    "pdb_clu.lookup",
]


def find_binary():
    on_path = shutil.which("foldseek")
    if on_path:
        return Path(on_path)
    for c in CANDIDATES:
        if c.is_file():
            return c
    return None


def main():
    results = []

    binary = find_binary()
    if binary is None:
        check("foldseek binary", False,
              "not on PATH and not in ~/tools/foldseek/bin")
        print("\nInstall: see skills/foldseek-deploy/SKILL.md")
        return 1
    results.append(check("foldseek binary", True, str(binary)))

    results.append(check("executable", binary.stat().st_mode & 0o111 != 0))

    r = subprocess.run([str(binary), "version"], capture_output=True,
                       text=True, timeout=60)
    # `foldseek version` prints just the git commit hash
    version = (r.stdout + r.stderr).strip().splitlines()
    version = version[0].strip() if version else ""
    results.append(check("reports version", bool(version),
                         version[:16] if version else "no output"))

    # Smoke test: search a real structure against itself.
    # A 3-residue stub is filtered out by foldseek's minimum-length cutoff,
    # so use a cached PDB entry when one exists, else skip with a warning.
    smoke = next(iter(sorted(STRUCTURES.glob("*.cif"))), None)
    if smoke is None:
        results.append(check("trivial search runs", True,
                             "skipped — no local structure to test with"))
    else:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out.m8"
            tmp = td / "tmp"
            r = subprocess.run(
                [str(binary), "easy-search", str(smoke), str(smoke), str(out),
                 str(tmp), "--format-output", "query,target,pident,bits"],
                capture_output=True, text=True, timeout=600,
            )
            ok = r.returncode == 0 and out.is_file() and out.stat().st_size > 0
            results.append(check("trivial search runs", ok,
                                 f"{smoke.name} vs itself"
                                 if ok else r.stderr.strip()[-160:]))

    # Optional: the PDB database. Not a hard requirement — local-file
    # comparisons work without it — but report its state so a partial
    # download is caught before a search fails.
    if not DB_DIR.is_dir():
        results.append(check("PDB database", True,
                             "not downloaded (local-file search only)"))
    else:
        missing = [f for f in DB_REQUIRED if not (DB_DIR / f).exists()]
        if missing:
            results.append(check("PDB database", False,
                                 f"incomplete, missing: {', '.join(missing)}"))
        else:
            size = sum(f.stat().st_size for f in DB_DIR.iterdir()
                       if f.is_file()) / 1e9
            results.append(check("PDB database", True,
                                 f"{DB_DIR} ({size:.1f} GB)"))

    return summary(results)


if __name__ == "__main__":
    sys.exit(main())
