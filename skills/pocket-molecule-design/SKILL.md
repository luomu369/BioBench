---
name: pocket-molecule-design
description: '**WORKFLOW SKILL** — Design small molecules that fit a protein binding pocket, using ResGen pocket-aware 3D generation. USE FOR: designing a molecule to bind a chosen pocket; generating drug-like candidates for a target; exploring what chemistry fits a binding site; proposing ligands for residues the user selected in the viewer; scaffold hopping around a known ligand. DO NOT USE FOR: installing ResGen (use resgen-deploy); docking or scoring a ligand you already have (use molecular-docking); designing a protein binder (use binder-design); generating molecules without a pocket (use molecule-generation). Requires ResGen installed — run resgen-deploy first.'
---

# Pocket Molecule Design

Generate small molecules that fit a protein pocket with ResGen, then
publish them alongside the receptor.

**Prerequisite:** ResGen installed. If not, follow `resgen-deploy` first.

## When to Use

- "Design a molecule that binds here"
- "What could fit in this pocket?"
- "Propose ligands for the residues I selected"
- "Explore chemistry around this scaffold"

## Confirm before running

Generation takes ~10 minutes for 20 molecules, so confirm first:

```
Receptor:    7XWQ.cif (ERβ ligand-binding domain)
Reference:   I1M (docked, -8.55 kcal/mol)
Pocket:      87 residues within 10 Å of the reference
Molecules:   20
Beam size:   20
Est. time:   ~10 minutes

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Receptor** | The structure the user is looking at | required |
| **Reference ligand** | Anything that marks the pocket centre — a docked pose, a co-crystallised ligand, or a fragment | required |
| **Molecules** | Ask the user | 20 |
| **Beam size** | Higher = better molecules, much slower | 20 |

> **The reference ligand only supplies the centre.** ResGen uses its atoms
> to place a 10 Å box; the generated molecules are not constrained to
> resemble it. A docked pose, a crystal ligand, or even a single placed
> fragment all work.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Prepare the inputs                 (PDB + reference SDF)
2. Report progress                    (structure-viewer publisher)
3. Run the generation
4. Publish the pocket + molecules     (structure-viewer publisher)
```

## Step 1 — Prepare the inputs

### The receptor must be PDB

ResGen parses the protein with Biopython's `PDBParser`, which cannot read
mmCIF. Convert first:

```bash
python3 -c "
import gemmi
st = gemmi.read_structure('7XWQ.cif')
st.write_pdb('/tmp/receptor.pdb')
"
```

### The reference ligand must be SDF

From a SMILES, embed 3D coordinates:

```bash
python3 -c "
from rdkit import Chem
from rdkit.Chem import AllChem
m = Chem.MolFromSmiles('CCOc1ccc(C[NH+]2CCN(c3ccccc3)CC2)cc1')
m = Chem.AddHs(m)
AllChem.EmbedMolecule(m, randomSeed=42)
w = Chem.SDWriter('/tmp/ref.sdf'); w.write(m)
"
```

From an existing PDBQT pose (e.g. a Vina result), extract it with
`parse_pdbqt` and write an SDF — see `molecular-docking`.

> **Both files must share a coordinate frame.** If the receptor comes from
> one structure and the ligand from another, superimpose them first
> (`structure-comparison`'s `superimpose.py`). A mismatched frame makes
> ResGen report `pocket residues is None`.

## Step 2 — Report progress

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step generation --index 1 --total 3 \
  --skill pocket-molecule-design --message "正在生成口袋适配分子"
```

## Step 3 — Run

```bash
PY=/home/asus_gx10/miniconda3/envs/boltzgen/bin/python
cd ~/tools/ResGen

$PY gen.py --pdb_file /tmp/receptor.pdb --sdf_file /tmp/ref.sdf \
  --outdir /tmp/resgen_out
```

Output lands in `/tmp/resgen_out/<sdf_stem>/`:

```
i1m_ref_gen.sdf        # all molecules in one file
SDF/                   # one file per molecule
7XWQ.pdb               # the pocket ResGen extracted
```

Each successful molecule is logged as
`successfully generate: <SMILES>` — grep the log to list them.

> **Run it in the background.** With `beam_size: 20` expect ~10 minutes;
> the log is silent until molecules finish. Use `setsid nohup ... &` and
> poll, or the call will look hung.

## Step 4 — Publish

Combine the receptor with the generated molecules so both are visible:

```bash
python3 -c "
import gemmi
from rdkit import Chem

ref = gemmi.read_structure('7XWQ.cif')
out = gemmi.Structure()
out.name = 'receptor_with_designs'
out.spacegroup_hm = 'P 1'
out.cell = ref.cell
out.add_model(gemmi.Model('1'))
for ch in ref[0]:
    out[0].add_chain(ch)

supp = Chem.SDMolSupplier('/tmp/resgen_out/i1m_ref/i1m_ref_gen.sdf')
names = 'abcdefghijklmnopqrstuvwxyz'
for i, mol in enumerate(supp):
    if mol is None: continue
    mol = Chem.AddHs(mol, addCoords=True)
    ch = gemmi.Chain('r' + names[i])
    res = gemmi.Residue()
    res.name = 'LIG'; res.seqid = gemmi.SeqId(1, ' '); res.het_flag = 'H'
    for a in mol.GetAtoms():
        p = mol.GetConformer().GetAtomPosition(a.GetIdx())
        at = gemmi.Atom()
        at.name = a.GetSymbol(); at.element = gemmi.Element(a.GetSymbol())
        at.pos = gemmi.Position(p.x, p.y, p.z)
        res.add_atom(at)
    ch.add_residue(res)
    out[0].add_chain(ch)

out.make_mmcif_document().write_file('/tmp/combined.cif')
"
```

Then publish:

```bash
python3 $PUB --structure /tmp/combined.cif \
  --name "receptor_with_designs" --source resgen \
  --roles "A=receptor,B=receptor,ra=gen_1,rb=gen_2" \
  --metrics '{"molecules_generated":11,"pocket_residues":87,"reference":"I1M","beam_size":20}' \
  --notes "ResGen pocket-aware generation. r-prefixed chains are the designs." \
  --status --state done --step generation --index 3 --total 3 \
  --skill pocket-molecule-design --message "生成完成：11 个口袋适配分子"
```

> **Write `.cif`, not `.pdb`.** PDB chain IDs are one character, so `ra`
> is silently truncated to `a` and the designs become indistinguishable
> from the receptor's own chains.

> **Read molecules from ResGen's SDF, not from the viewer's `current.cif`.**
> The latter already contains whatever was last published, so re-reading it
> double-prefixes chain names (`ra` → `rra`).

## Interpreting the results

ResGen optimises for pocket fit, not drug-likeness. Check each molecule
before presenting it:

| Check | How |
|-------|-----|
| Chemically sensible | Look for the functional groups the pocket favours |
| Not a duplicate | Compare SMILES; the beam often converges |
| Synthesisable | Reject strained rings, peroxides, metals |
| Actually binds | Dock the best candidates with `molecular-docking` |

Report in biological terms:

```
Generated 11 molecules for the ERβ pocket (87 residues, I1M-centred):

  9 of 11 carry a carboxylic acid, and all have a naphthalene/anthracene
  core — the same aromatic-plus-acidic motif as estradiol and tamoxifen.

  Best candidates by fit:
    rg  CCC(C)c1cc2cc(C(=O)O)ccc2cc1CC12C=CCC=C1CC(C)(C(=O)O)C(C)C2
    rk  CC(CC(=O)O)c1cc2cc(C(=O)O)ccc2cc1CC12CC=CC=C1C(CO)CC(CO)C2

  → The model learned the pocket's preference for an acidic group paired
    with a flat aromatic system, which is exactly ER's pharmacology.
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pocket residues is None` | Reference ligand far from the protein | Check both files share a coordinate frame |
| `invalid literal for int() ... 'EU'` | Biopython was given mmCIF | Convert to PDB with gemmi first |
| No output for a long time | `beam_size` too high | Lower to 20; run in background |
| Molecules look unlike the reference | Expected — the reference only sets the centre | Nothing to fix |
| Chains named `a`,`b`,... after publishing | PDB truncated two-character IDs | Publish as `.cif` |
| Chains named `rra`,`rrb`,... | Molecules read back from `current.cif` | Read from ResGen's SDF instead |
| `Weights only load failed` | PyTorch 2.6+ default | See `resgen-deploy` |

## References

- [ResGen GitHub](https://github.com/OdinZhang/ResGen)
- Pocket-based 3D molecular generation, *Nature Machine Intelligence* (2024)
