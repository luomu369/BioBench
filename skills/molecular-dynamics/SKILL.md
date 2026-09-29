---
name: molecular-dynamics
description: '**WORKFLOW SKILL** — Run a molecular dynamics simulation of a protein (optionally with a ligand) using OpenMM, then analyse the trajectory for stability. USE FOR: simulating a protein over time; checking whether a structure is stable; relaxing a designed complex; measuring RMSD or radius of gyration; seeing how a binding site moves; preparing a structure for further analysis. DO NOT USE FOR: docking a ligand (use molecular-docking); designing a sequence (use sequence-design); scanning a single torsion (use conformational-analysis); publishing structures (use structure-viewer). Requires OpenMM in the `openmm` conda environment.'
---

# Molecular Dynamics

Simulate a protein in explicit water with OpenMM and report whether it holds
together.

**Prerequisite:** the `openmm` conda environment. If it is missing:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
bash $BB/skills/molecular-dynamics-deploy/scripts/install.sh
```

No model weights to download — the force fields ship with OpenMM. The install
script handles the awkward parts: openff-toolkit's releases are yanked on PyPI
so versions must be pinned, `python-constraint` is Python 2 code that will not
build (a shim is installed), and am1-bcc charges need AmberTools' `sqm`, which
only conda-forge ships for aarch64.

For a ligand complex, the `docking` env is also used — `prepare_complex.py`
calls it for the gemmi work, since gemmi is not in the `openmm` env.

## When to Use

- "Simulate this structure"
- "Is this design stable?"
- "Relax this complex"
- "How much does the binding site move?"
- "Run 10 ns on this receptor"

## Confirm before running

Simulation cost scales with system size and length, so state both:

```
Structure:  NK2R receptor, 474 residues
Box:        1 nm padding, 0.15 M NaCl  →  ~86,000 atoms
Length:     1 ns
Platform:   OpenCL
Est. time:  ~12 minutes at 118 ns/day

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Length** | From the request; 1 ns is a smoke test, 10–100 ns for real sampling | 1 ns |
| **Box padding** | 1 nm is enough for a protein alone; 1.5 nm if a ligand is present | 1.0 nm |
| **Salt** | Physiological is 0.15 M | 0.15 M |
| **Platform** | `auto` picks the fastest available | auto |

## Workflow

```
1. Prepare the structure    (.pdb — convert from .cif if needed)
2. Report progress          (structure-viewer publisher)
3. Run the simulation
4. Analyse and publish
```

## Step 1 — Prepare the structure

**Protein alone** — extract the chain and let the script do the rest:

```bash
python3 -c "
import gemmi
st = gemmi.read_structure('complex.cif')
st.setup_entities()
AA = set('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL MSE'.split())
new = gemmi.Structure(); new.spacegroup_hm = 'P 1'; new.cell = st.cell
m = gemmi.Model('1'); m.add_chain(gemmi.Chain('A'))
for r in st[0]['A']:
    if r.name in AA: m['A'].add_residue(r)
new.add_model(m); new.setup_entities()
new.write_pdb('receptor.pdb')
"
```

`simulate.py` runs PDBFixer itself, which adds the C-terminal OXT and any
other missing heavy atoms — a PDB archive structure without OXT fails with
`No template found for residue N (XXX) ... missing 1 C atom`.

**Protein + ligand** — use `prepare_complex.py`, which does the whole
preparation in one call:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/molecular-dynamics/scripts/prepare_complex.py \
  --receptor 6HDX.cif --chain A \
  --ligand-pdbqt docking/mol_14/ligand.pdbqt \
  --ligand-pose docking/mol_14/ligand_pose1.pdb \
  --ligand-smiles "Cc1nc2ccc(C(=O)NCc3ccc(NC(=O)OC(C)(C)C)cc3)cc2o1" \
  --ligand-resname UNL --out complex.pdb
```

It renumbers the receptor, adds hydrogens and the OXT, places the ligand at
its docked pose, and writes CONECT records for the ligand's bonds. Three
things it fixes that each break a simulation silently:

| Problem | Symptom without the fix |
|---------|------------------------|
| Residue numbering starts below 1 or has gaps | OpenMM's template matching walks the chain expecting contiguity |
| No C-terminal OXT | `No template found ... missing 1 C atom` on the last residue |
| Ligand has no bonds in the PDB | `the residue has no bonds between its atoms` |

> **The ligand pose comes from the PDBQT, not the PDB.** Vina's PDB output
> is only partly hydrogenated (28 heavy atoms + 2 polar H), its atom names
> are all `C`/`N`/`O` with no unique identifier, and its atom order follows
> AD4 types rather than the SMILES. Aligning by assumed order gives a 6 Å
> RMSD. The PDBQT's `REMARK SMILES IDX` lines hold `(smiles_idx, pdbqt_idx)`
> pairs — the only record of which docked atom is which — and using them
> brings the RMSD to under 2 Å. `prepare_complex.py` reads them; do not
> reconstruct the mapping by hand.

## Step 2 — Report progress

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "动力学模拟" --index 1 --total 2 \
  --skill molecular-dynamics --message "正在溶剂化并最小化 NK2R（约 86000 原子）"
```

A run of minutes has real intermediate stages worth reporting — solvent,
minimisation, equilibration, production. Update the message as each begins.

## Step 3 — Run

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/molecular-dynamics/scripts/simulate.py \
  --structure receptor.pdb --out md_out --ns 1
```

| Flag | Meaning |
|------|---------|
| `--ns` | Production length in nanoseconds |
| `--padding` | Water box padding around the solute, nm |
| `--salt-molar` | NaCl concentration |
| `--temperature` | Kelvin |
| `--platform` | `auto`, `CPU`, `OpenCL`, `CUDA`, `Reference` |
| `--threads` | CPU threads; 0 = all cores |
| `--forcefield` / `--water-model` | Override the amber14 defaults |

The script runs minimisation, 50 ps NVT, 100 ps NPT, then production, and
writes `trajectory.dcd`, `final.pdb`, `md.log` and `summary.json`.

### Simulating a complex

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/molecular-dynamics/scripts/simulate_complex.py \
  --structure complex.pdb \
  --ligand-smiles "Cc1nc2ccc(C(=O)NCc3ccc(NC(=O)OC(C)(C)C)cc3)cc2o1" \
  --ligand-resname UNL \
  --out md_out --ns 1
```

The ligand is parameterised with GAFF2 and am1-bcc charges; the protein with
amber14. Charge generation runs `sqm` and takes a minute or two, so the first
call is slower than a protein-only run of the same size.

### Run it in the background, and do not wait in the turn

A 1 ns run on this machine is 50 minutes at 26 ns/day. That is far past the
point where a turn should still be open, and the way to spend it is not to hold
the turn open:

```bash
setsid nohup python3 $BB/skills/molecular-dynamics/scripts/simulate_complex.py \
  --structure complex.pdb --ligand-smiles "..." --ligand-resname UNL \
  --out md_out --ns 1 > md.log 2>&1 &
PID=$!
echo "started pid $PID"

python3 $PUB --status --state running --step "产出运衈" \
  --index 4 --total 5 --skill molecular-dynamics --job <run-id> \
  --pid $PID --message "正在运行 1 ns 分子动力学"
```

Then end the turn. The panel watches the pid, shows the step in the compose
box, and wakes the model with the log's tail when the run finishes — so the
report is written from the ending rather than from a poll.

> **Do not `sleep` in the turn to wait for it.** `sleep 280; tail md.log` holds
> the turn open for five minutes at a time, and a run that needs an hour is
> twelve of those. Each one is a tool call that produces nothing the panel did
> not already know, and the user watching the panel sees a turn that never
> ends. Worse, a question asked during the wait queues behind it — "how far
> along is it?" is answered after the next sleep finishes, not when it is
> asked.
>
> The one thing worth checking before ending the turn is that the run actually
> started: the log exists, the pid is alive, and the first minimisation line
> has appeared. That is one call, and it catches the failures that would
> otherwise sit silent for an hour — a missing `sqm`, a ligand that will not
> parameterise, a platform that will not initialise.

On top of the protein-only metrics it reports `ligand_rmsd_nm` and
`ligand_protein_min_dist_nm` — the latter is the one that says whether the
pose survived: a ligand that stays bound keeps a small, steady minimum
distance to the protein.

### GPU on this machine

**There is no CUDA platform on ARM64.** `openmm-cuda` publishes no aarch64
wheel — PyPI's latest is 8.1.1.12 with only x86_64 and Windows builds, and
conda-forge's linux-aarch64 repository has none. So
`Platform.getNumPlatforms()` returns Reference, CPU and OpenCL only.

**OpenCL does use the GPU, and it is the fast path.** Measured on an 86,000
atom system, 5000 steps with no trajectory output:

| Platform | 5000 steps | Throughput |
|----------|-----------|------------|
| OpenCL | 5.9 s | ~145,000 ns/day |
| CPU | 143.4 s | ~6,000 ns/day |

`--platform auto` picks OpenCL, which is what you want.

> **`OpenCLPlatformName` comes back empty, and that is not a fault.** The
> NVIDIA ICD resolves fine — `clGetPlatformIDs` reports one platform and the
> kernels run on the GPU — but OpenMM's OpenCL plugin does not fill in the
> device-name property on this driver. Do not read the empty string as "no
> GPU" and fall back to CPU; that costs 24×.

> **A production run reports far less than the table above, and that is the
> trajectory writer, not the GPU.** Writing a DCD frame every 1000 steps
> drops an 86,000-atom run from ~145,000 to ~120 ns/day — the I/O dominates.
> The number in `summary.json`'s `ns_per_day` is the honest end-to-end
> figure; the table is what the integrator alone achieves. Raise
> `--report-interval` if throughput matters more than frame density.

## Step 4 — Analyse and publish

`summary.json` carries the numbers that say whether the run is usable:

| Metric | What it means |
|--------|---------------|
| `rmsd_nm.mean` / `.max` | Backbone drift from the start; a stable run stays under ~0.3 nm |
| `rg_nm.mean` | Radius of gyration; a sudden change means unfolding or collapse |
| `ns_per_day` | Throughput — the number to quote when asked about performance |
| `potential_energy_kj_mol` | Should be large and negative after minimisation |

Publish the **solute** frame — protein plus ligand, without the water:

```bash
# Strip the solvent first. final.pdb is the whole solvated system: a 1 ns run
# of a trimer is 375,290 atoms, of which 117,328 are water.
$PY - <<'EOF'
import gemmi
st = gemmi.read_structure('md_out/final.pdb')
st.setup_entities()
keep = {'HOH', 'WAT', 'DOD', 'NA', 'CL', 'K', 'NA+', 'CL-'}
out = gemmi.Structure()
for mi, model in enumerate(st):
    m = gemmi.Model(str(mi + 1))
    for ch in model:
        c = gemmi.Chain(ch.name)
        for res in ch:
            if res.name in keep:
                continue
            c.add_residue(res)
        if len(c):
            m.add_chain(c)
    out.add_model(m)
out.setup_entities()
out.write_pdb('md_out/final_solute.pdb')
print('solute atoms:', sum(1 for ch in out[0] for r in ch for a in r))
EOF

python3 $PUB --structure md_out/final_solute.pdb --name nk2r_md_relaxed \
  --source openmm --roles "A=nk2r_receptor" \
  --notes "1 ns 动力学模拟后的结构（去水后的某质帧）。RMSD 平均 0.098 nm，Rg 2.30 nm，OpenCL 平台，118 ns/天。" \
  --job nk2r_md --step "动力学模拟" --index 2 --total 2 \
  --metrics '{"ns": 1, "rmsd_mean_nm": 0.098, "rg_mean_nm": 2.30, "ns_per_day": 118.6, "platform": "OpenCL"}' \
  --status --state done --skill molecular-dynamics \
  --message "1 ns 模拟完成，RMSD 0.098 nm，结构稳定"
```

> **Publish the solute, not the solvated system.** `final.pdb` holds every
> water and ion — 117,328 waters on a trimer, 375,290 atoms in total. The
> viewer applies its style to *all* atoms (`setStyle({}, ...)`), so that many
> waters stop a surface or stick render outright. And `publish.py`'s
> `parse_chains` skips water residues by name, so a chain that is nothing but
> water never reaches the chain list at all — the user cannot even hide it.
>
> Strip the solvent, publish the solute, and say in `--notes` that the frame is
> desolvated. The full system and the trajectory stay on disk for anyone who
> wants them; the viewer gets the 22,656 atoms that can actually be looked at.

> **A short run says little about stability.** 0.1 ns has not left the local
> potential well the crystal structure sat in — an RMSD of 0.1 nm at that
> length is the expected result, not evidence of stability. Say so rather
> than reporting it as a pass. Real conclusions need 10 ns or more.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No template found for residue N ... missing 1 C atom` | No C-terminal OXT | PDBFixer adds it; the script runs it automatically |
| `No template found ... NA ... not supported` | `amber14-all.xml` has no ion templates | Use `amber14/protein.ff14SB.xml` + `amber14/tip3pfb.xml` (the defaults) |
| `Unit "picosecond" is not compatible with "/picosecond"` | Friction passed as ps | The integrator wants 1/ps |
| `Wrong number or type of arguments for ... new_Context` | Platform passed as a string | Pass `Platform.getPlatformByName(name)` |
| `compute_rg() got an unexpected keyword argument 'atom_indices'` | mdtraj's signature is `(traj, masses=None)` | Slice first: `compute_rg(traj.atom_slice(protein))` |
| `ModuleNotFoundError: openmm.vecmath` | Removed in recent OpenMM | Do not import it |
| Only Reference/CPU/OpenCL listed | No aarch64 `openmm-cuda` wheel | Expected on ARM64; OpenCL is the GPU path |
| `OpenCLPlatformName` is empty | The NVIDIA driver does not fill it in | Not a fault — the GPU is still used |
| `ns_per_day` ~120 while the GPU does 145,000 | DCD writing every 1000 steps | Raise `--report-interval`; the integrator is not the bottleneck |
| `No matching distribution found for openff-toolkit` | Every openff release is yanked | Pin the version: `pip install "openff-toolkit==0.18.0"` |
| `SyntaxError` building `constraint` | `python-constraint` is Python 2 code | Install the shim (the install script does) |
| `partial_charge_method 'am1bcc' is not available` | No AmberTools | `conda install -c conda-forge ambertools` |
| `No module named 'lxml'` | GAFF XML generation needs it | `pip install lxml` |
| `the residue has no bonds between its atoms` | PDB carries no CONECT for the ligand | Use `prepare_complex.py`, which writes them |
| Ligand alignment RMSD above 3 Å | Atom order taken from Vina's PDB | Read the PDBQT's `REMARK SMILES IDX` pairs instead |
| `MonteCarloBarostat` type error | Passed a Quantity | It takes plain floats |
| `'tuple' object has no attribute 'to_smiles'` | `molecules=` given SMILES strings | Pass `Molecule.from_smiles(...)` objects |
| `Did not recognize residue UNL` in `addSolvent` | The plain force field has no ligand template | Load the GAFF XML into the force field first |

## References

- [OpenMM](https://openmm.org/) — the manual covers force fields and platforms
- [PDBFixer](https://github.com/openmm/pdbfixer) — missing atoms and termini
- [MDTraj](https://mdtraj.org/) — trajectory analysis
