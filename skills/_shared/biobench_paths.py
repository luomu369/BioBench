"""Where BioBench keeps things — resolved once, never written as a literal.

Every path in this project used to be spelled out in full wherever it was
needed: a full path to a skill's script in a SKILL.md command, a full path to
a conda interpreter in a script. That works on the machine it was written on
and nowhere else — a clone under a different home directory, or a different
user, leaves every one of those strings pointing at a path that does not
exist, and the failure looks like a missing dependency rather than a wrong
path.

This module resolves each location from the environment, with a default
derived from the repository's own position rather than from a hardcoded home.
Nothing here knows what the machine is called.

Resolution order, first match wins:

  BIOBENCH      the repository root
  CONDA_ROOT    the conda installation
  MODELS_ROOT   downloaded model weights
  TOOLS_ROOT    third-party binaries built outside conda
  FOLDSEEK_DB   the foldseek search database

Environments, weights and tools live outside the repository — one copy per
machine, shared by every clone. There is no fallback inside the tree.

Usage:
    from biobench_paths import env_python, tool, skill_dir

    env_python("docking")          # .../envs/docking/bin/python
    tool("vina")                   # .../tools/vina/vina
    skill_dir("molecular-docking") # .../skills/molecular-docking
"""
import os
import shutil
from pathlib import Path

# The repository root: this file is at <root>/skills/_shared/biobench_paths.py,
# so three directories up. Derived rather than configured, because a module
# that has to be told where it lives cannot be the thing that tells other
# modules where they live.
_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: Path) -> Path:
    """An environment override, or the default when it is unset or empty."""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def _first_existing(*candidates: Path) -> Path:
    """The first candidate that exists, or the last one if none do.

    Returning the last rather than raising keeps the caller's error message
    about the thing it was actually looking for, instead of a failure here
    that says only "nothing was found".
    """
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def biobench_root() -> Path:
    """The repository root."""
    return _env("BIOBENCH", _ROOT)


def conda_root() -> Path:
    """The conda installation that holds the environments.

    Probed in the places conda is normally installed, because a machine that
    already has conda should not have to be told where it is.

    There is deliberately no fallback inside the repository. Environments live
    in conda and nowhere else: a `envs/` directory in the tree would be a
    second, quieter answer to "where is the docking env", and the two would
    drift — a skill fixed against the conda one while a stale copy sat in the
    repo, or the reverse. When conda is not found the last candidate is
    returned and the caller's error names the path it actually wanted, which
    is a clearer failure than silently using something else.
    """
    override = os.environ.get("CONDA_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    home = Path.home()
    return _first_existing(
        home / "miniconda3",
        home / "miniforge3",
        home / "anaconda3",
        Path("/opt/conda"),
        home / "miniconda3",
    )


def models_root() -> Path:
    """Downloaded model weights, shared across skills.

    Outside the repository, like conda. Weights are large and shared between
    skills, so they live in one place per machine rather than being copied
    into a clone — and a second copy inside the tree would be a quieter
    answer to "where are the weights" that could go stale.
    """
    override = os.environ.get("MODELS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "models"


def tools_root() -> Path:
    """Third-party binaries built outside conda (vina, foldseek, ...).

    Outside the repository, for the same reason as the weights: one copy per
    machine, shared by every clone.
    """
    override = os.environ.get("TOOLS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "tools"


def foldseek_db() -> Path:
    """The foldseek search database."""
    return _env("FOLDSEEK_DB", models_root() / "foldseek-db")


def skills_root() -> Path:
    """The directory holding the per-skill folders."""
    return biobench_root() / "skills"


def skill_dir(skill: str) -> Path:
    """One skill's directory, by its name as it appears in SKILL.md."""
    return skills_root() / skill


def shared_dir() -> Path:
    """The directory holding modules shared between skills."""
    return skills_root() / "_shared"


def env_dir(env: str) -> Path:
    """One conda environment's directory."""
    return conda_root() / "envs" / env


def env_python(env: str) -> Path:
    """The interpreter of one conda environment.

    Raises when the environment does not exist. Falling back to the current
    interpreter would let a script run against the wrong packages and report
    a confusing failure later — or worse, succeed with subtly different
    behaviour. A missing environment is a setup problem and should say so.
    """
    candidate = env_dir(env) / "bin" / "python"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"conda environment {env!r} not found at {env_dir(env)}. "
        f"Create it with: conda create -n {env} python=3.11"
    )


def tool(name: str) -> Path:
    """A third-party binary, by the name a skill would call it.

    Looks under TOOLS_ROOT first, then on PATH — a tool installed by a
    package manager is as good as one built locally.
    """
    candidate = tools_root() / name / name
    if candidate.is_file():
        return candidate
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    return candidate


def runs_dir() -> Path:
    """Where a run's intermediate files go."""
    return biobench_root() / "runs"


def viewer_static() -> Path:
    """The viewer's static directory, which holds the contract files."""
    return biobench_root() / "webapp" / "static"


def as_str(p: Path) -> str:
    """A path as a string, for building a command line."""
    return str(p)


if __name__ == "__main__":
    # Print every resolved location, so a new machine can be checked in one
    # command before anything tries to use them.
    print(f"BIOBENCH   {biobench_root()}")
    print(f"CONDA_ROOT {conda_root()}")
    print(f"MODELS     {models_root()}")
    print(f"TOOLS      {tools_root()}")
    print(f"FOLDSEEK_DB{foldseek_db()}")
    print()
    for env in ("docking", "openmm", "boltzgen", "proteinmpnn", "genmol",
                "resgen", "webapp"):
        try:
            py = env_python(env)
        except FileNotFoundError as exc:
            print(f"env {env:<12} MISSING — {exc}")
            continue
        print(f"env {env:<12} {py}")
    print()
    for name in ("vina", "foldseek", "fpocket", "mmseqs"):
        print(f"tool {name:<11} {tool(name)}")
