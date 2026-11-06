---
name: conformational-analysis
description: '**WORKFLOW SKILL** — Analyse the conformational energy of a protein structure with RDKit and an MMFF94 force field: scan the torsional profile around a selected bond, find the lowest-energy rotamer, and report what it costs to rotate a side chain. USE FOR: asking what the lowest-energy state around a bond is; how much energy it costs to rotate a side chain; whether a residue is sitting in a strained conformation; comparing a crystal rotamer against its preferred one; exploring which way a side chain wants to point. DO NOT USE FOR: docking a ligand (use molecular-docking); designing a sequence (use sequence-design); predicting structure (use binder-design); publishing structures (use structure-viewer). Requires RDKit — present in the docking and boltzgen conda environments.'
---

# Conformational Analysis

Scan the torsional energy profile around a bond with an MMFF94 force field
and report the lowest-energy state.

**Prerequisite:** RDKit. Any Python with it works — no GPU, no model
weights. If it is not importable, the `admet-prediction` skill documents the
one-command environment setup.

## When to Use

- "What is the lowest-energy state around this bond?"
- "How much energy does it cost to rotate this side chain?"
- "Is this residue in a strained conformation?"
- "Which way does this side chain want to point?"
- "Compare this rotamer against the preferred one"

## Confirm before running

A scan is seconds, so confirm intent rather than parameters:

```
Residue:  B:464 HIS
Bond:     CB-CG
Dihedral: CA-CB-CG-ND1  (χ1)
Step:     15° → 24 samples

Proceed?
```

The one thing worth checking is **which bond**. A residue has several, and
"rotate this side chain" usually means the first one (χ1) but not always —
a user pointing at the tip of a long side chain may mean the bond nearest
the tip. If the selection is ambiguous, say which bond you are scanning and
why.

## Workflow

```
1. Take the selected bond from the request's `[当前结构：...]` block
2. Pick the four atoms of the dihedral
3. Run the scan
4. Report the profile, and publish the minimum if it is interesting
```

## Step 1 — Read the selection

The user selects a bond in the viewer (`sel_mode: bond`). `selection.json`
carries it as two atom indices plus a label:

```json
{"bonds": [{"indices": [3108, 3105], "label": "CB464–CA464", "color": "#4ecdc4"}]}
```

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 -c "
import json
d = json.load(open('$BB/webapp/static/selection.json'))
b = d.get('bonds', [])
if not b:
    raise SystemExit('no bond selected — switch the panel to Bond mode')
print('selected bond:', b[0]['label'], b[0]['indices'])
"
```

**The label names the two atoms but not their order**, and the scan needs a
direction — rotating about b-c from the a side is a different scan from
rotating from the d side. Resolve the indices against the structure to get
the atoms and their residue, then choose the four.

## Step 2 — Pick the dihedral

For a side-chain scan the natural choice is χ1, the first torsion of the
side chain:

| Residue type | χ1 dihedral |
|--------------|-------------|
| Most (HIS, ARG, LYS, ASP, GLU, ASN, GLN, SER, THR, CYS, MET, PHE, TYR, TRP, LEU, ILE, VAL, ALA) | `N-CA-CB-CG` |
| The next one along | `CA-CB-CG-<first atom past CG>` |

The example below scans `CA-CB-CG-ND1` on a HIS — the same bond as χ1 but
with ND1 as the far reference, which is equivalent for a ring and more
explicit about what is being rotated.

## Step 3 — Run the scan

Use whichever `python` has RDKit — on this machine that is the `docking` or
`boltzgen` environment, but the script needs nothing else from either.

```bash
python skills/conformational-analysis/scripts/torsion_scan.py \
  --pdb runs/dock_gb/out/complex.pdb \
  --chain B --resi 464 \
  --a CA --b CB --c CG --d ND1 \
  --step 15 \
  --out runs/torsion_464/scan.json \
  --write-pdb runs/torsion_464/minimum.pdb
```

| Flag | Why |
|------|-----|
| `--a --b --c --d` | The four atoms of the dihedral. `b` and `c` are the bond being scanned |
| `--step` | Degrees between samples. 15 gives 24 points; 10 gives 36 and a smoother curve |
| `--write-pdb` | Also write the structure with the side chain at its minimum |

## Step 4 — Read the output

```json
{
  "minimum_angle": 30,
  "minimum_energy": 604.14,
  "max_barrier_kcal": 11.104,
  "start_angle": -99.83,
  "profile": [{"angle": 0, "energy": 607.37, "rel_kcal": 3.23}, ...]
}
```

| Field | Meaning |
|-------|---------|
| `minimum_angle` | The lowest-energy dihedral, in degrees |
| `start_angle` | The angle in the input structure — what the crystal actually has |
| `max_barrier_kcal` | Highest point above the minimum |
| `rel_kcal` | Each sample's energy above the minimum, so the numbers read as barriers |

### Interpreting the barrier

| Barrier | Meaning |
|---------|---------|
| < 5 kcal/mol | The bond rotates freely at room temperature |
| 5–15 | Rotates, but with a preference |
| > 20 | Effectively locked |

### `start_angle` above the minimum is the interesting case

A side chain sitting 3 kcal/mol off its preferred rotamer is being held
there by something — a ligand, a neighbouring residue, a crystal contact.
That is the one result that says something about the **complex** rather
than about the residue in isolation, and it is worth putting in `--notes`.

Report it in those terms:

```
B:464 HIS, χ1 = -99.8° in the crystal, 3.2 kcal/mol above the vacuum
minimum at 30°. The side chain is held off its preferred rotamer by the
complex — most likely the ligand 2.8 Å from CE1.
```

## Publishing

Publish the written minimum as a second tab so it can be compared against
the original:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --structure runs/torsion_464/minimum.pdb \
  --name "torsion_B464_min" --source rdkit-mmff \
  --roles "A=receptor,B=receptor,C=nanobody,D=peptide,L=ligand" \
  --highlight "B:464" \
  --metrics '{"min_angle": {"label": "最低能角度", "value": "30°"},
             "crystal_angle": {"label": "晶体角度", "value": "-99.8°"},
             "barrier": {"label": "能垒", "value": "11.1 kcal/mol"}}' \
  --notes "..." \
  --status --state done --step "导出最低能构象" --index 1 --total 1 \
  --skill conformational-analysis --message "已导出 B:464 最低能构象（30°）"
```

Review it before the viewer shows it: spawn the `structure-reviewer`
subagent with `--structure runs/torsion_464/minimum.pdb`, `--source` the
structure the scan started from, and `--frozen-chains` the protein chains
only. The scanned bond's two sides are *supposed* to have moved — that is
the scan — so the ligand chain stays out of `--frozen-chains` and the
protein must not have moved at all. The check that matters for this skill
is `clash`: the writer places the minimum conformer geometrically, and a
rotamer that fits the torsion can still land a heavy atom on its
neighbour.

## The scan is in vacuum — say so

The residue is scanned alone. No neighbouring chains, no ligand, no
solvent. Two consequences worth stating whenever the result is reported:

- The barrier is the **intrinsic** cost of rotating that side chain, not
  the cost in the complex. A pocket can raise it arbitrarily.
- The minimum is where the side chain *would* prefer to sit, which is often
  not where it *is* sitting. The gap between the two is the environment's
  contribution, and it is the more informative number.

A full-complex scan needs a force field over the whole assembly (OpenMM or
similar), which is not installed.

## How it works, and what does not

The far side of the bond is rotated as a **rigid body** and the force field
is evaluated at each angle. Minimisation with a torsion constraint was
tried first and does not work: the constraint is a harmonic term competing
with everything else in the force field, and at any constant strong enough
to hold the angle the rest of the molecule tears itself apart. The profile
came out rising linearly to 2.6e7 kcal/mol — a stretched bond, not a
barrier.

The residue is rebuilt from the PDB with an explicit topology table rather
than guessed bonds. A residue read out of a structure is a fragment whose N
and C termini have no neighbours, and RDKit's bond determination refuses to
assign a valence to an atom with one bond.

Four RDKit details are needed to get a usable profile, each found by
hitting the error:

| Call | Without it |
|------|-----------|
| `UpdatePropertyCache` | `SetNoImplicit` leaves the explicit valence uncalculated; MMFF reads it on the first atom |
| `GetSSSR` | `RingInfo not initialized` on HIS, PHE, TYR, TRP, PRO |
| `AddHs` | MMFF wants explicit hydrogens; PDB depositions omit them |
| Rebuild the force field each sample | `PyForceField` copies coordinates at construction and `CalcEnergy` reads that copy — a force field built once reports the same number however the conformer moves, giving a perfectly flat profile |

Only the atoms on the far side move. `--write-pdb` maps the conformer's
heavy atoms back to the file by position, since `build_mol` sets no atom
names and `AddHs` appends after the heavy atoms — index order is file
order. Hydrogens are skipped: the source has none, and inventing them would
renumber every serial after them.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Torsion profile is perfectly flat | Force field built once; `PyForceField` copied the coordinates at construction | Rebuild it at every sample |
| Torsion energies rise linearly to ~1e7 | Minimising with a torsion constraint tears the molecule apart | Rotate rigidly instead; do not minimise |
| `getValence(EXPLICIT) ... without calcExplicitValence` | `SetNoImplicit` skips the valence calculation MMFF reads | Call `UpdatePropertyCache(strict=False)` |
| `RingInfo not initialized` | MMFF reads ring membership, computed on demand | Call `GetSSSR(mol)` |
| `Molecule does not have explicit Hs` | MMFF wants hydrogens; the PDB has none | `AddHs(mol, addCoords=True)` |
| `Unable to determine valence of atom 0 ... 1 bonds` | RDKit cannot guess bonds on a residue fragment | Use the `RESIDUE_BONDS` table in `torsion_scan.py` |
| `--write-pdb` output has a different atom count | Hydrogens were written into a source that had none | Skip them; map heavy atoms by position |
| `no topology table for XXX` | Residue not in `RESIDUE_BONDS` | Add it — the table covers the 20 standard residues |

## References

- [RDKit Book — Force Fields](https://www.rdkit.org/docs/RDKit_Book.html#force-fields)
- Halgren, *J. Comput. Chem.* (1996), doi:10.1002/(SICI)1096-987X(199605)17:5/6<490::AID-JCC1>3.0.CO;2-X — MMFF94
