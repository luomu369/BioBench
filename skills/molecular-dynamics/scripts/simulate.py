#!/usr/bin/env python3
"""Run a molecular dynamics simulation with OpenMM.

Prepares a receptor (optionally with a ligand), minimises, equilibrates, then
runs a production trajectory. Writes a trajectory, a log, and a summary of
the run's stability metrics.

Usage:
  python3 simulate.py --structure receptor.pdb --out md_out --ns 1

  # with a ligand present in the structure
  python3 simulate.py --structure complex.pdb --out md_out --ns 2 --platform auto

Exit codes: 0 ok, 1 simulation failed, 2 bad input.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import env_python

# OpenMM lives in its own env; the path is resolved so this script runs from
# any shell without the env activated.
OPENMM_PY = str(env_python("openmm"))


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", required=True,
                    help="PDB or mmCIF of the system to simulate")
    ap.add_argument("--out", default="md_out", help="output directory")
    ap.add_argument("--ns", type=float, default=1.0,
                    help="production length in nanoseconds")
    ap.add_argument("--timestep", type=float, default=2.0,
                    help="integration step in femtoseconds")
    ap.add_argument("--temperature", type=float, default=300.0,
                    help="Kelvin")
    ap.add_argument("--friction", type=float, default=1.0,
                    help="Langevin friction coefficient, 1/ps")
    ap.add_argument("--platform", default="auto",
                    help="auto, CPU, OpenCL, CUDA, or Reference")
    ap.add_argument("--threads", type=int, default=0,
                    help="CPU threads; 0 = all cores")
    ap.add_argument("--salt-molar", type=float, default=0.15,
                    help="NaCl concentration in mol/L")
    ap.add_argument("--padding", type=float, default=1.0,
                    help="water box padding around the solute, nm")
    ap.add_argument("--forcefield", default="amber14/protein.ff14SB.xml",
                    help="OpenMM force field; amber14-protein.ff14SB for "
                         "proteins, plus explicit water")
    ap.add_argument("--water-model", default="amber14/tip3pfb.xml")
    ap.add_argument("--report-interval", type=int, default=1000,
                    help="steps between trajectory frames")
    ap.add_argument("--ligand-smiles", default="",
                    help="SMILES of a ligand present in the structure. Its "
                         "GAFF2 template is generated and loaded into the "
                         "force field, without which the ligand residue "
                         "fails with 'No template found'. Pass the same "
                         "SMILES prepare_complex.py was given.")
    ap.add_argument("--ligand-resname", default="UNL",
                    help="residue name the ligand carries in the structure")
    return ap


def main():
    args = build_parser().parse_args()

    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # The simulation itself runs in the openmm env. This wrapper stays in
    # whatever env the calling skill activated, so the two never fight over
    # numpy versions.
    runner = out / "_run.py"
    runner.write_text(RUNNER)

    import subprocess
    cmd = [OPENMM_PY, str(runner),
           "--structure", str(src),
           "--out", str(out),
           "--ns", str(args.ns),
           "--timestep", str(args.timestep),
           "--temperature", str(args.temperature),
           "--friction", str(args.friction),
           "--platform", args.platform,
           "--threads", str(args.threads),
           "--salt-molar", str(args.salt_molar),
           "--padding", str(args.padding),
           "--forcefield", args.forcefield,
           "--water-model", args.water_model,
           "--report-interval", str(args.report_interval),
           "--ligand-smiles", args.ligand_smiles,
           "--ligand-resname", args.ligand_resname]
    r = subprocess.run(cmd)
    return r.returncode


# The simulation body, written to a file and run by the openmm interpreter.
# Kept as a string so this wrapper needs no openmm import of its own.
RUNNER = r'''
import argparse, json, sys, time
from pathlib import Path

def log(msg):
    print(msg, flush=True)

ap = argparse.ArgumentParser()
ap.add_argument("--structure", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--ns", type=float, default=1.0)
ap.add_argument("--timestep", type=float, default=2.0)
ap.add_argument("--temperature", type=float, default=300.0)
ap.add_argument("--friction", type=float, default=1.0)
ap.add_argument("--platform", default="auto")
ap.add_argument("--threads", type=int, default=0)
ap.add_argument("--salt-molar", type=float, default=0.15)
ap.add_argument("--padding", type=float, default=1.0)
ap.add_argument("--forcefield", default="amber14/protein.ff14SB.xml")
ap.add_argument("--water-model", default="amber14/tip3pfb.xml")
ap.add_argument("--report-interval", type=int, default=1000)
ap.add_argument("--ligand-smiles", default="")
ap.add_argument("--ligand-resname", default="UNL")
args = ap.parse_args()

out = Path(args.out)

from openmm import Platform, app, unit, LangevinMiddleIntegrator, MonteCarloBarostat
from openmm.app import PDBFile, Modeller, ForceField, Simulation, PME
import openmm

# ---- platform ----------------------------------------------------------
# auto prefers the fastest available. CUDA is absent on ARM64 (no
# openmm-cuda wheel), so the real choice is between OpenCL on the GPU and
# the CPU platform; OpenCL only appears if the GPU ICD resolves.
names = [Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]
if args.platform == "auto":
    for want in ("CUDA", "OpenCL", "CPU"):
        if want in names:
            args.platform = want
            break
    else:
        args.platform = "Reference"
log(f"platforms available: {names}")
log(f"using: {args.platform}")

props = {}
if args.platform == "CPU" and args.threads:
    props["Threads"] = str(args.threads)
platform_obj = Platform.getPlatformByName(args.platform)

# ---- system ------------------------------------------------------------
# A structure straight out of a PDB archive usually lacks the C-terminal
# OXT, and amber14's C-terminal template requires it — without it OpenMM
# reports "missing 1 C atom" on the last residue and refuses to build the
# system. PDBFixer adds it (and any other missing heavy atoms) before the
# force field ever sees the topology.
from pdbfixer import PDBFixer

log("fixing structure (missing atoms, termini)")
fixer = PDBFixer(filename=str(args.structure))
fixer.findMissingResidues()
fixer.findMissingAtoms()
fixer.addMissingAtoms()
if fixer.missingAtoms:
    log(f"  added atoms to {len(fixer.missingAtoms)} residues")

# The fixed topology and positions are what everything downstream uses.
pdb = PDBFile(str(args.structure))
ff = ForceField(args.forcefield, args.water_model)

# ---- ligand parameters -------------------------------------------------
# A ligand residue has no template in the protein force field, and every
# downstream step that builds a system — addHydrogens, addSolvent,
# createSystem — fails on it with "No template found for residue N (UNL)".
# GAFF2 with am1-bcc charges covers it; openmmforcefields runs
# antechamber/sqm itself, which takes a minute or two for a small molecule.
# The template is generated once and loaded into the same force field the
# protein uses, so nothing downstream needs to know a ligand is present.
if args.ligand_smiles:
    log(f"parameterising ligand {args.ligand_resname} with GAFF2")
    from openmmforcefields.generators import SystemGenerator
    from openff.toolkit.topology import Molecule
    ligand = Molecule.from_smiles(args.ligand_smiles)
    generator = SystemGenerator(
        forcefields=[args.forcefield, args.water_model],
        small_molecule_forcefield="gaff-2.11",
    )
    gaff_xml = generator.template_generator.generate_residue_template(ligand)
    gaff_file = out / "ligand_gaff.xml"
    gaff_file.write_text(gaff_xml)
    ff.loadFile(str(gaff_file))
    log(f"  ligand template written to {gaff_file.name}")

log("adding solvent")
modeller = Modeller(fixer.topology, fixer.positions)
modeller.addHydrogens(ff)
modeller.addSolvent(
    ff,
    model="tip3p",
    padding=unit.Quantity(args.padding, unit.nanometer),
    ionicStrength=unit.Quantity(args.salt_molar, unit.molar),
    neutralize=True,
)

n_wat = sum(1 for r in modeller.topology.residues() if r.name == "HOH")
log(f"system: {modeller.topology.getNumAtoms()} atoms, {n_wat} waters")

system = ff.createSystem(
    modeller.topology,
    nonbondedMethod=PME,
    nonbondedCutoff=unit.Quantity(1.0, unit.nanometer),
    constraints=app.HBonds,
    rigidWater=True,
)

# ---- minimise ----------------------------------------------------------
steps_per_ns = int(1e6 / args.timestep)
prod_steps = int(args.ns * steps_per_ns)

integ = LangevinMiddleIntegrator(
    unit.Quantity(args.temperature, unit.kelvin),
    unit.Quantity(1.0 / args.friction, unit.picosecond**-1),
    unit.Quantity(args.timestep, unit.femtosecond),
)
integ.setConstraintTolerance(1e-5)

sim = Simulation(modeller.topology, system, integ, platform_obj, props)
sim.context.setPositions(modeller.positions)

log("minimising")
sim.minimizeEnergy(maxIterations=500)
e0 = sim.context.getState(getEnergy=True).getPotentialEnergy()
log(f"  potential energy after minimisation: {e0}")

# ---- equilibrate -------------------------------------------------------
# NVT first, then NPT, so the density settles before production.
log("equilibrating (NVT 50 ps)")
sim.context.setVelocitiesToTemperature(args.temperature, 12345)
sim.step(int(0.05 * steps_per_ns))

baro = MonteCarloBarostat(unit.Quantity(1.0, unit.bar), args.temperature)
system.addForce(baro)
sim.context.reinitialize(preserveState=True)
log("equilibrating (NPT 100 ps)")
sim.step(int(0.1 * steps_per_ns))

# ---- production --------------------------------------------------------
# OpenMM's reporters count from the simulation's total step count, which
# includes equilibration — so a 1 ns production run after 150 ps of
# equilibration logs steps 75000..575000 and times 150..1150 ps. That reads
# as a 1.15 ns run. Record the offset so the log and the summary can say
# what the numbers actually are.
equil_steps = sim.currentStep
equil_ps = equil_steps * args.timestep / 1000.0

log(f"production: {args.ns} ns ({prod_steps} steps, "
    f"after {equil_ps:.0f} ps equilibration)")
sim.reporters = []
sim.reporters.append(app.StateDataReporter(
    str(out / "md.log"), int(steps_per_ns / 20),
    step=True, time=True, potentialEnergy=True, temperature=True,
    volume=True, density=True, speed=True))
sim.reporters.append(app.DCDReporter(
    str(out / "trajectory.dcd"), args.report_interval))

t0 = time.time()
sim.step(prod_steps)
wall = time.time() - t0

state = sim.context.getState(getEnergy=True, getPositions=True)
final = out / "final.pdb"
PDBFile.writeFile(sim.topology, state.getPositions(), str(final))

# ---- metrics -----------------------------------------------------------
# RMSD of the solute against the first production frame, and its radius of
# gyration, are the two numbers that say whether the run is usable.
import numpy as np
from openmm.app import DCDReporter

ref = None
rmsds, rgyrs = [], []
try:
    import mdtraj
except ImportError:
    mdtraj = None

summary = {
    "structure": str(args.structure),
    "platform": args.platform,
    "atoms": modeller.topology.getNumAtoms(),
    "waters": n_wat,
    "ns": args.ns,
    "steps": prod_steps,
    # Equilibration is not production, but it is time spent. Both numbers
    # are reported so the log's step column (which counts from the start of
    # equilibration) can be read against them.
    "equilibration_steps": equil_steps,
    "equilibration_ps": round(equil_ps, 1),
    "timestep_fs": args.timestep,
    "temperature_K": args.temperature,
    "wall_seconds": round(wall, 1),
    "ns_per_day": round(args.ns / (wall / 86400.0), 4) if wall > 0 else None,
    "potential_energy_kj_mol": float(state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)),
    "trajectory": str(out / "trajectory.dcd"),
    "final_pdb": str(final),
    "log": str(out / "md.log"),
}

if mdtraj is not None:
    log("computing RMSD and Rg")
    traj = mdtraj.load(str(out / "trajectory.dcd"), top=str(final))
    protein = traj.topology.select("protein")
    if len(protein):
        rmsd = mdtraj.rmsd(traj, traj, 0, atom_indices=protein)
        summary["rmsd_nm"] = {
            "mean": round(float(np.mean(rmsd)), 4),
            "max": round(float(np.max(rmsd)), 4),
            "final": round(float(rmsd[-1]), 4),
        }
        # compute_rg takes masses, not atom indices, so slice the trajectory
        # to the protein first.
        rg = mdtraj.compute_rg(traj.atom_slice(protein))
        summary["rg_nm"] = {
            "mean": round(float(np.mean(rg)), 4),
            "final": round(float(rg[-1]), 4),
        }
    summary["frames"] = traj.n_frames
else:
    log("mdtraj not installed — skipping RMSD/Rg")

(out / "summary.json").write_text(json.dumps(summary, indent=2))
log("")
log(json.dumps(summary, indent=2))
'''


if __name__ == "__main__":
    sys.exit(main())
