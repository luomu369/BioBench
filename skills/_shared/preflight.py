"""The parts every deploy skill's preflight check repeats.

Seven skills each shipped their own `check()` and `run()`, byte-identical
modulo a timeout default. That is the same failure the PDB column layout
had: a copy per caller, and a fix applied to one and missed by the other
six. The output format is also what a reader parses — `[PASS]` / `[FAIL]`
on one line, a summary count at the end — so it belongs in one place.

Usage:
    from preflight import check, run_py, summary

    results = [check("binary present", shutil.which("vina") is not None)]
    r = run_py(PY, "import rdkit; print(rdkit.__version__)")
    results.append(check("rdkit imports", r.returncode == 0))
    sys.exit(summary(results))
"""
import subprocess
import sys
from pathlib import Path


def check(label, ok, detail=""):
    """Print one check and return its result, so callers can collect them."""
    print(f"[{'PASS' if ok else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    return bool(ok)


def run_py(python, code, timeout=300, cwd=None):
    """Run a snippet under one environment's interpreter.

    `python` is a path, not a name — the whole point of resolving it through
    biobench_paths is that the caller knows which environment it means, and
    `python` on PATH is whichever one happens to be active.
    """
    return subprocess.run([str(python), "-c", code], capture_output=True,
                          text=True, timeout=timeout,
                          cwd=str(cwd) if cwd else None)


def summary(results):
    """Print the count and return the exit code a preflight should use."""
    passed = sum(1 for r in results if r)
    print(f"\nsummary: {passed} passed, {len(results) - passed} failed")
    return 0 if passed == len(results) else 1


def shared_dir():
    """This module's directory, for a caller that needs to inject it."""
    return Path(__file__).resolve().parent
