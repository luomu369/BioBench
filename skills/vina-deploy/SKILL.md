---
name: vina-deploy
description: '**DEPLOYMENT SKILL** — Install AutoDock Vina (molecular docking) and its Python toolchain on any machine, and verify the installation. USE FOR: setting up Vina from scratch; installing meeko/rdkit for ligand and receptor preparation; checking that docking works; diagnosing PDBQT conversion errors. DO NOT USE FOR: running a docking job (use molecular-docking); protein design (use binder-design); publishing results (use structure-viewer). INVOKES: shell tools (curl/tar/conda/pip), file system tools. No GPU required.'
---

# AutoDock Vina Deployment

AutoDock Vina docks a small-molecule ligand into a protein receptor and scores
the predicted binding poses. This skill covers **installation only**.

To actually run a docking job, see the `molecular-docking` skill.

## When to Use

- User wants to install Vina
- `vina: command not found` or a PDBQT parsing error
- User asks whether docking is ready to use

## Requirements

| Resource | Need |
|----------|------|
| GPU | none — Vina is CPU-only |
| Disk | ~500 MB (conda env + binaries) |
| RAM | 4 GB+ |
| Python | 3.10+ (for meeko/rdkit) |

## Installation

### Step 1: Install the Vina binary

Official pre-compiled executables exist for several architectures — no
compilation needed:

```bash
mkdir -p ~/tools/vina && cd ~/tools/vina
VERSION=1.2.7

# ARM64 (local GPU workstation, Apple Silicon, Graviton)
curl -sL -o vina "https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v${VERSION}/vina_${VERSION}_linux_aarch64"
curl -sL -o vina_split "https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v${VERSION}/vina_split_${VERSION}_linux_aarch64"

# x86-64: replace _linux_aarch64 with _linux_x86_64
# macOS:  replace with _mac_aarch64 or _mac_x86_64

chmod +x vina vina_split
./vina --version
```

Check the [releases page](https://github.com/ccsb-scripps/AutoDock-Vina/releases)
for the current version and available architectures.

### Step 2: Add to PATH

```bash
echo 'export PATH="$HOME/tools/vina:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Step 3: Install the Python toolchain

Vina needs PDBQT input. `meeko` converts SMILES → PDBQT and PDB → PDBQT;
`rdkit` handles molecule manipulation; `gemmi` reads mmCIF.

```bash
conda create -n docking python=3.12 -y
conda activate docking
pip install numpy meeko rdkit scipy gemmi
```

> **Do not `pip install vina`.** The PyPI package builds from source and needs
> Boost; it fails on most systems. The release binary from Step 1 is all you
> need — the Python bindings are optional.

### Step 4: Verify

```bash
vina --version
python -c "import meeko, rdkit, gemmi; print('toolchain OK')"
```

## Verifying an existing installation

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/vina-deploy/scripts/preflight.py
```

Checks the binary, the Python toolchain, and that a ligand can be converted to
PDBQT. Exits non-zero on failure.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `vina: command not found` | Not on PATH | Add `~/tools/vina` to PATH |
| `cannot execute binary file` | Wrong architecture | Download the matching release asset |
| `PDBQT parsing error: Unknown or inappropriate tag` | Receptor is PDB, not PDBQT | Convert with `mk_prepare_receptor.py` |
| `No template matched for residue_key=...` | Non-standard residue (ligand, modified AA) | Re-run with `-a` to drop unmatched residues |
| `pip install vina` fails to build | Boost missing | Skip it — use the release binary |
| `ModuleNotFoundError: gemmi` | meeko needs it | `pip install gemmi` |

## References

- [AutoDock Vina GitHub](https://github.com/ccsb-scripps/AutoDock-Vina)
- [Vina documentation](https://autodock-vina.readthedocs.io/)
- Eberhardt et al., *J. Chem. Inf. Model.* (2021), doi:10.1021/acs.jcim.1c00203
