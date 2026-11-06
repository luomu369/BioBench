---
name: molecular-dynamics-deploy
description: '**DEPLOY SKILL** — Install and verify the OpenMM environment for molecular dynamics simulation on this machine. USE FOR: installing OpenMM; setting up the openmm conda environment; checking whether MD can run; diagnosing missing force fields, platforms, or analysis tools; verifying GPU availability for simulation. DO NOT USE FOR: running a simulation (use molecular-dynamics); docking (use molecular-docking); publishing structures (use structure-viewer).'
---

# OpenMM Deployment

Install and verify the `openmm` conda environment.

## When to Use

- "Install OpenMM"
- "Set up molecular dynamics"
- "Can this machine run MD?"
- "OpenMM is broken"

## Requirements

No model weights — the force fields ship inside the OpenMM package. The only
download is the wheel itself.

| Component | Why |
|-----------|-----|
| `openmm` | The simulator |
| `mdtraj` | Trajectory analysis (RMSD, Rg) |
| `pdbfixer` | Missing atoms and chain termini |

## Installation

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
bash $BB/skills/molecular-dynamics-deploy/scripts/install.sh
```

Idempotent — safe to re-run. It installs the conda env, the PyPI packages,
the openff stack, the `constraint` shim, and AmberTools, then verifies each
one.

### The four things that need special handling

**1. openff-toolkit is yanked.** Every release — 0.18.0, and its dependencies
openff-units 0.3.2 and openff-utilities 0.1.16 — is yanked on PyPI, so pip
reports `No matching distribution found` and skips them. Each does ship a
`py3-none-any` wheel, so pinning the version works:

```bash
pip install "openff-toolkit==0.18.0" "openff-units==0.3.2" "openff-utilities==0.1.16"
```

**2. `python-constraint` will not build.** It is Python 2 code
(`except OSError, ex:`). openff imports `constraint.Problem` inside
`_assign_rdmol_bonds_stereo`, before it checks whether the molecule has any
stereogenic bonds — so the import fails even for molecules that need no
stereo solving. The install script writes a shim whose `Problem` raises if
actually used, which is the honest outcome.

**3. am1-bcc charges need AmberTools.** GAFF parameterisation calls
`sqm`, which only conda-forge ships for aarch64:

```bash
conda install -c conda-forge ambertools
```

Without it: `partial_charge_method 'am1bcc' is not available from
RDKitToolkitWrapper`.

**4. gemmi is not in the `openmm` env.** It is in `docking`.
`prepare_complex.py` shells out to each env for the part it has, so run it
from anywhere.

## Verifying an existing installation

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/molecular-dynamics-deploy/scripts/preflight.py
```

It checks the env, the OpenMM import, the available platforms, mdtraj,
pdbfixer, and that the force-field pair carries ion templates.

Exit codes: 0 all checks passed, 1 one or more failed.

## The force-field pair matters

`amber14-all.xml` looks like the obvious default but **contains no ion
templates**. A solvated system with counter-ions then fails:

```
No template found for residue 26612 (NA). The residue contains Na atoms,
which are not supported by any template in the force field.
```

Use the pair that works:

```python
ForceField('amber14/protein.ff14SB.xml', 'amber14/tip3pfb.xml')
```

`tip3pfb.xml` carries the NA and CL templates; `protein.ff14SB.xml` carries
the protein ones. The `molecular-dynamics` skill defaults to this pair.

## GPU: OpenCL, not CUDA

`openmm-cuda` has **no aarch64 wheel** — PyPI's latest is 8.1.1.12 with only
`x86_64` and `win_amd64`, and conda-forge's `linux-aarch64` repository has
none. So `Platform.getNumPlatforms()` returns Reference, CPU and OpenCL.

OpenCL is the GPU path and it is fast, measured on an 86,000 atom system:

| Platform | 5000 steps | Throughput |
|----------|-----------|------------|
| OpenCL | 5.9 s | ~145,000 ns/day |
| CPU | 143.4 s | ~6,000 ns/day |

> **`OpenCLPlatformName` comes back empty, and that is not a fault.** The
> GPU ICD resolves fine and the kernels run on the GPU; the driver just
> does not fill in the property. Do not read the empty string as "no GPU" —
> that costs 24×.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No matching distribution found for openmm-cuda` | No aarch64 wheel exists | Expected; use OpenCL |
| `No template found ... NA ... not supported` | `amber14-all.xml` has no ions | Use `amber14/protein.ff14SB.xml` + `amber14/tip3pfb.xml` |
| `No template found ... missing 1 C atom` | No C-terminal OXT in the PDB | PDBFixer adds it |
| `ModuleNotFoundError: openmm.vecmath` | Removed in recent OpenMM | Do not import it |
| `compute_rg() got an unexpected keyword argument` | mdtraj's signature is `(traj, masses=None)` | `compute_rg(traj.atom_slice(protein))` |
| OpenCL runs but is slow | A stage fell back to CPU | Check `ns_per_day` in `summary.json` |

## References

- [OpenMM](https://openmm.org/)
- [PDBFixer](https://github.com/openmm/pdbfixer)
- [MDTraj](https://mdtraj.org/)
