# BioBench

Run biomolecular AI models on local NVIDIA hardware through Agent Skills,
and see the results in a web viewer.

An agent (Claude Code, GitHub Copilot, DeepSeek Harness, or any harness that
can read `SKILL.md` and run shell commands) reads the skills, executes the
models on the local GPU, and publishes structures to the viewer.

## Layout

```
biobench/
├── SKILL.md                  Entry point: bootstrap, route, orchestrate
├── skills/
│   ├── viewer-launch/        Start the viewer; bootstrap the environment
│   │   └── scripts/
│   │       └── bootstrap.sh  Environment check (--fix to repair)
│   ├── structure-viewer/     Publish structures and progress
│   │   └── scripts/
│   │       └── publish.py    Atomic publisher (structure + status)
│   ├── boltzgen-deploy/      Install BoltzGen and its weights
│   │   └── scripts/
│   │       ├── preflight.py  GPU/CUDA/weights readiness
│   │       └── fetch_weights.sh
│   ├── binder-design/        Run a BoltzGen binder design
│   ├── proteinmpnn-deploy/   Install ProteinMPNN
│   │   └── scripts/
│   │       └── preflight.py  Env/import/weights readiness
│   ├── sequence-design/      Design sequences for a fixed backbone
│   ├── genmol-deploy/        Install GenMol and its weights
│   │   └── scripts/
│   │       └── preflight.py  Env/import/weights/checkpoint readiness
│   ├── molecule-generation/ Generate small molecules with GenMol
│   │   └── scripts/
│   │       └── generate.py  de novo / fragment completion / linking
│   ├── foldseek-deploy/      Install Foldseek
│   │   └── scripts/
│   │       └── preflight.py  Binary/version/smoke-search readiness
│   └── structure-comparison/ Compare structures with Foldseek
├── webapp/
│   ├── run_viewer.sh         Launch the viewer
│   └── static/
│       ├── index.html        The viewer (3Dmol.js, no backend)
│       ├── 3Dmol-min.js
│       ├── current.cif       ← published structure
│       ├── current.json      ← published metadata
│       └── status.json       ← published progress
├── envs/                     Conda environment recipes
└── structures/               Locally cached PDB entries
```

## Quick start

```bash
# 1. Check the environment (add --fix to repair)
bash skills/viewer-launch/scripts/bootstrap.sh

# 2. Start the viewer
bash webapp/run_viewer.sh --port 8282
#    → http://localhost:8288

# 3. Ask your agent:
#    "design a binder for NK1R"
```

## How it fits together

```
        agent (any harness)
              │  reads SKILL.md, runs commands
              ▼
      ┌───────────────┐
      │  biobench/    │  entry: bootstrap + routing
      │  SKILL.md     │
      └───────┬───────┘
              │ routes to
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
viewer-    structure-    boltzgen-
launch     viewer        deploy
    │         │              │
    │         │              │ runs BoltzGen on the local GPU
    │         │              │
    └─────────┴──────────────┘
              │ publishes
              ▼
      webapp/static/current.cif + current.json + status.json
              │ 2s poll
              ▼
        BioBench Viewer
```

The viewer has no backend and no LLM. It is a static page that renders
whatever a skill publishes. This keeps the app trivial to run and lets any
agent harness drive it.

## Skills

| Skill | Role |
|-------|------|
| `biobench` (root) | Entry point — bootstrap, route, orchestrate |
| `viewer-launch` | Start/restart the viewer; environment bootstrap |
| `structure-viewer` | Publishing contract for structures and progress |
| `boltzgen-deploy` | BoltzGen installation and weights |
| `binder-design` | Running a BoltzGen binder design |
| `proteinmpnn-deploy` | ProteinMPNN installation |
| `sequence-design` | ProteinMPNN sequence design |
| `genmol-deploy` | GenMol installation |
| `molecule-generation` | GenMol small-molecule generation |
| `foldseek-deploy` | Foldseek installation |
| `structure-comparison` | Foldseek structure search and clustering |

## Installing a skill elsewhere

Skills follow the open Agent Skills spec, so they install with the standard
CLI or by copying:

```bash
# Symlink into an agent's skill directory
ln -s "$PWD/skills/binder-design" ~/.claude/skills/binder-design

# Or copy
cp -r skills/binder-design ~/.claude/skills/
```

## Hardware

Developed and tested on NVIDIA DGX Spark (GB10, aarch64, CUDA 13.0, 121 GB
unified memory). BoltzGen needs ~40 GB of that memory for a typical design.
