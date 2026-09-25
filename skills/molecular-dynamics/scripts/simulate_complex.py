#!/usr/bin/env python3
"""Simulate a protein-ligand complex with OpenMM.

The ligand is parameterised with GAFF2 (am1-bcc charges) via
openmmforcefields; the protein uses amber14. Everything else follows the
same minimise / equilibrate / produce path as simulate.py.

Usage:
  python3 simulate_complex.py --structure complex.pdb --ligand-smiles "..." \
      --ligand-resname UNL --out md_out --ns 1

Exit codes: 0 ok, 1 simulation failed, 2 bad input.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

OPENMM_PY = "/home/asus_gx10/miniconda3/envs/openmm/bin/python"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", required=True,
                    help="PDB of the complex: protein chain(s) + ligand")
    ap.add_argument("--ligand-smiles", required=True,
                    help="SMILES of the ligand, for parameterisation")
    ap.add_argument("--ligand-resname", default="UNL",
                    help="residue name of the ligand in the PDB")
    ap.add_argument("--out", default="md_out")
    ap.add_argument("--ns", type=float, default=1.0)
    ap.add_argument("--timestep", type=float, default=2.0)
    ap.add_argument("--temperature", type=float, default=300.0)
    ap.add_argument("--platform", default="auto")
    ap.add_argument("--padding", type=float, default=1.0)
    ap.add_argument("--salt-molar", type=float, default=0.15)
    ap.add_argument("--report-interval", type=int, default=1000)
    args = ap.parse_args()

    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runner = out / "_run.py"
    runner.write_text(RUNNER)

    r = subprocess.run([
        OPENMM_PY, str(runner),
        "--structure", str(src),
        "--ligand-smiles", args.ligand_smiles,
        "--ligand-resname", args.ligand_resname,
        "--out", str(out),
        "--ns", str(args.ns),
        "--timestep", str(args.timestep),
        "--temperature", str(args.temperature),
        "--platform", args.platform,
        "--padding", str(args.padding),
        "--salt-molar", str(args.salt_molar),
        "--report-interval", str(args.report_interval),
    ])
    return r.returncode


RUNNER = r'''
import argparse, json, sys, time
from pathlib import Path

def log(m): print(m, flush=True)

ap = argparse.ArgumentParser()
ap.add_argument("--structure", required=True)
ap.add_argument("--ligand-smiles", required=True)
ap.add_argument("--ligand-resname", default="UNL")
ap.add_argument("--out", required=True)
ap.add_argument("--ns", type=float, default=1.0)
ap.add_argument("--timestep", type=float, default=2.0)
ap.add_argument("--temperature", type=float, default=300.0)
ap.add_argument("--platform", default="auto")
ap.add_argument("--padding", type=float, default=1.0)
ap.add_argument("--salt-molar", type=float, default=0.15)
ap.add_argument("--report-interval", type=int, default=1000)
args = ap.parse_args()
out = Path(args.out)

from openmm import Platform, app, unit, LangevinMiddleIntegrator, MonteCarloBarostat
from openmm.app import PDBFile, Modeller, ForceField, Simulation, PME
from openmmforcefields.generators import SystemGenerator

# ---- platform ----------------------------------------------------------
names = [Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]
if args.platform == "auto":
    for want in ("CUDA", "OpenCL", "CPU"):
        if want in names:
            args.platform = want
            break
    else:
        args.platform = "Reference"
log(f"platforms: {names} -> using {args.platform}")
platform_obj = Platform.getPlatformByName(args.platform)

# ---- ligand parameters -------------------------------------------------
# GAFF2 with am1-bcc charges. openmmforcefields runs antechamber/sqm itself,
# which takes a minute or two for a small molecule.
log(f"parameterising ligand {args.ligand_resname} with GAFF2")
# MonteCarloBarostat takes plain floats, not Quantities.
barostat = MonteCarloBarostat(1.0, args.temperature)

# SystemGenerator wants Molecule objects, not SMILES strings. The molecule
# is passed again at create_system time — that is where the template
# generator is told to recognise it.
from openff.toolkit.topology import Molecule
ligand = Molecule.from_smiles(args.ligand_smiles)

system_generator = SystemGenerator(
    forcefields=["amber14/protein.ff14SB.xml", "amber14/tip3pfb.xml"],
    small_molecule_forcefield="gaff-2.11",
    forcefield_kwargs={"constraints": app.HBonds, "rigidWater": True},
    barostat=barostat,
)

# ---- system ------------------------------------------------------------
pdb = PDBFile(str(args.structure))
log(f"complex: {pdb.topology.getNumAtoms()} atoms, "
    f"{pdb.topology.getNumResidues()} residues")

modeller = Modeller(pdb.topology, pdb.positions)
log("adding solvent")
# addSolvent builds a throwaway system to check every residue has a
# template, using the plain force field — which knows nothing about the
# ligand. generate_residue_template returns the GAFF parameters as an XML
# string; writing it to a file and loading it into the force field is what
# makes the ligand recognisable at that point.
gaff_xml = system_generator.template_generator.generate_residue_template(ligand)
gaff_file = out / "ligand_gaff.xml"
gaff_file.write_text(gaff_xml)
system_generator.forcefield.loadFile(str(gaff_file))

modeller.addSolvent(
    system_generator.forcefield,
    model="tip3p",
    padding=unit.Quantity(args.padding, unit.nanometer),
    ionicStrength=unit.Quantity(args.salt_molar, unit.molar),
    neutralize=True,
)
n_wat = sum(1 for r in modeller.topology.residues() if r.name == "HOH")
log(f"solvated: {modeller.topology.getNumAtoms()} atoms, {n_wat} waters")

# molecules= tells the template generator to recognise the ligand; without
# it the GAFF parameters are never generated.
system = system_generator.create_system(modeller.topology, molecules=[ligand])

steps_per_ns = int(1e6 / args.timestep)
prod_steps = int(args.ns * steps_per_ns)

integ = LangevinMiddleIntegrator(
    unit.Quantity(args.temperature, unit.kelvin),
    unit.Quantity(1.0, unit.picosecond**-1),
    unit.Quantity(args.timestep, unit.femtosecond),
)
integ.setConstraintTolerance(1e-5)

sim = Simulation(modeller.topology, system, integ, platform_obj, {})
sim.context.setPositions(modeller.positions)

log("minimising")
sim.minimizeEnergy(maxIterations=1000)
e0 = sim.context.getState(getEnergy=True).getPotentialEnergy()
log(f"  potential energy: {e0}")

log("equilibrating (NVT 50 ps)")
sim.context.setVelocitiesToTemperature(args.temperature, 12345)
sim.step(int(0.05 * steps_per_ns))
log("equilibrating (NPT 100 ps)")
sim.step(int(0.1 * steps_per_ns))

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

# ---- analysis ----------------------------------------------------------
# RMSD of the protein, of the ligand, and the distance between them. The
# ligand's RMSD and its distance to the binding site are what say whether
# the pose survived the simulation.
import numpy as np
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
    "wall_seconds": round(wall, 1),
    "ns_per_day": round(args.ns / (wall / 86400.0), 4) if wall > 0 else None,
    "potential_energy_kj_mol": float(
        state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)),
    "trajectory": str(out / "trajectory.dcd"),
    "final_pdb": str(final),
    "log": str(out / "md.log"),
}

try:
    import mdtraj
    log("computing RMSD and contacts")
    traj = mdtraj.load(str(out / "trajectory.dcd"), top=str(final))
    prot = traj.topology.select("protein")
    lig = traj.topology.select(f"resname {args.ligand_resname}")

    if len(prot):
        rmsd = mdtraj.rmsd(traj, traj, 0, atom_indices=prot)
        summary["protein_rmsd_nm"] = {
            "mean": round(float(np.mean(rmsd)), 4),
            "max": round(float(np.max(rmsd)), 4),
            "final": round(float(rmsd[-1]), 4),
        }
        rg = mdtraj.compute_rg(traj.atom_slice(prot))
        summary["protein_rg_nm"] = {
            "mean": round(float(np.mean(rg)), 4),
            "final": round(float(np.mean(rg[-5:])), 4),
        }

    if len(lig):
        lr = mdtraj.rmsd(traj, traj, 0, atom_indices=lig)
        summary["ligand_rmsd_nm"] = {
            "mean": round(float(np.mean(lr)), 4),
            "max": round(float(np.max(lr)), 4),
            "final": round(float(lr[-1]), 4),
        }
        # Minimum distance from the ligand to any protein atom, per frame.
        # A pose that stays bound keeps this small and steady.
        pairs = traj.topology.select_pairs(lig, prot)
        if len(pairs):
            d = mdtraj.compute_distances(traj, pairs)
            dmin = d.min(axis=1)
            summary["ligand_protein_min_dist_nm"] = {
                "mean": round(float(np.mean(dmin)), 4),
                "max": round(float(np.max(dmin)), 4),
                "final": round(float(np.mean(dmin[-5:])), 4),
            }
    summary["frames"] = traj.n_frames
except Exception as e:
    log(f"analysis skipped: {e}")

(out / "summary.json").write_text(json.dumps(summary, indent=2))
log("")
log(json.dumps(summary, indent=2))
'''


if __name__ == "__main__":
    sys.exit(main())
