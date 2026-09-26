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

## Reading a selection from the viewer

When the user points at residues in the viewer, they are in
`webapp/static/selection.json`:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 -c "
import json
sel = json.load(open('$BB/webapp/static/selection.json'))
print(f\"{sel['count']} residues on structure {sel['structure']}\")
for r in sel['residues']:
    print(f\"  {r['chain']}/{r['resi']} {r['resn']}\")
"
```

Turn them into a centre, and check the spread before committing to a box
size — a selection that spans 15 Å or more is probably two sites, not one:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 -c "
import gemmi, math, json, sys
st = gemmi.read_structure(sys.argv[1]); st.setup_entities()
sel = json.load(open('$BB/webapp/static/selection.json'))
want = {(r['chain'], r['resi']) for r in sel['residues']}
pts = [a.pos for ch in st[0] for r in ch for a in r
       if (ch.name, r.seqid.num) in want]
c = [sum(getattr(p, k) for p in pts)/len(pts) for k in 'xyz']
print(f'{len(pts)} atoms, centroid {c[0]:.2f},{c[1]:.2f},{c[2]:.2f}')
print(f'max radius {max(math.dist(p, c) for p in pts):.2f} A')
" /tmp/cur.pdb
```

> **The published file may be PDB text under a `.cif` name.** The viewer's
> contract file is always `current.cif`, but `publish.py` writes whatever
> format the source was. If gemmi rejects it with `expected block header`,
> copy it to a `.pdb` name and read that.

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
| **Pocket centre** | A reference ligand, or explicit coordinates | required |
| **Molecules** | Ask the user | 20 |
| **Beam size** | Higher = better molecules, much slower | 20 |

> **The reference ligand only supplies the centre.** ResGen uses its atoms
> to place a 10 Å box; the generated molecules are not constrained to
> resemble it. A docked pose, a crystal ligand, or even a single placed
> fragment all work.
>
> **When the user selected residues in the viewer, use their centroid and
> skip the reference ligand entirely.** `--center` takes coordinates
> directly, so no SDF is needed:
>
> ```bash
> $PY gen.py --pdb_file /tmp/receptor.pdb --sdf_file /tmp/anything.sdf \
>   --center "16.33,-9.59,-32.43" --outdir /tmp/resgen_out
> ```
>
> `--sdf_file` is a required argument but its contents are ignored when
> `--center` is given — the `if args.center is not None` branch overwrites
> the data built from the SDF. It still names the output directory, so
> give it a meaningful stem. A one-atom SDF at the centroid works if you
> prefer to keep the two paths identical.
>
> Compute the centroid from the selected residues, and check the spread
> first: if the selection spans more than ~15 Å the 10 Å box will not
> cover it, and either a larger `box_size` or a tighter selection is
> needed.

### Expect small molecules without a reference ligand

ResGen grows each molecule outward from a single seed atom and stops when
`focal_threshold` is met. With a reference ligand present the seed sits in
a pocket shaped by something drug-sized, and the result is multi-ring. With
only a centroid the model has nothing to grow against, and a hydrophobic
pocket satisfies the stop condition early — a run on a six-residue
all-leucine selection produced 11 molecules averaging 5.5 heavy atoms
(acetone, acetic acid, propanol, phenol).

That is the model behaving as designed, not a fault. If the user wants
drug-sized output, the options in order of effort:

1. **Give it a reference ligand** — any molecule that marks the pocket,
   even a fragment or a docked pose. This is what changes the outcome.
2. **Raise `focal_threshold`** in `configs/sample.yml` (default 0.5) so
   generation runs longer.
3. **Dock a library instead** — `molecular-docking` scores molecules you
   already have, which suits a hydrophobic pocket where the goal is to
   find something that fits rather than to invent chemistry.

Say which you did and what it implies; do not present 5-atom molecules as
design candidates without the caveat.

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
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "分子生成" --index 1 --total 3 \
  --skill pocket-molecule-design --message "正在生成口袋适配分子"
```

## Step 3 — Run

```bash
CONDA="${CONDA:-$HOME/miniconda3}"
PY=$CONDA/envs/boltzgen/bin/python
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
ref.setup_entities()
# Drop the co-crystallised ligand and waters before combining. Leaving
# them in puts the original ligand in the same pocket as the new
# molecules, and the viewer shows them overlapping.
ref.remove_ligands_and_waters()
ref.remove_alternative_conformations()

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

out.setup_entities()
out.make_mmcif_document().write_file('/tmp/combined.cif')
"
```

> **`remove_ligands_and_waters()` is not optional.** A PDB entry's pocket
> already contains its co-crystallised ligand. Copying the receptor
> verbatim keeps it, and any molecule you then place in that pocket
> overlaps it — the viewer shows a physically impossible structure and
> the docking scores no longer describe what is on screen. 7XWQ carries
> I1M in chains A and B plus 96 waters; leaving them in put every generated
> molecule 0.4 Å from the original ligand's atoms.

Then publish:

```bash
python3 $PUB --structure /tmp/combined.cif \
  --name "receptor_with_designs" --source resgen \
  --roles "A=receptor,B=receptor,ra=gen_1,rb=gen_2" \
  --metrics '{"molecules_generated": {"label": "生成分子数", "value": 11}, "pocket_residues": {"label": "口袋残基", "value": 87}, "reference": "I1M", "beam_size": {"label": "束搜索宽度", "value": 20, "hint": "越大越慢"}}' \
  --notes "生成 11 个分子（模型成功产出的数量），口袋由 87 个残基界定
（距参考配体 10 A 内的受体残基），参考配体 I1M 仅用于定位口袋中心，
束搜索宽度 20（每步保留的候选数，越大越慢）。
建议：分子数量充足，挑选 3–5 个用分子对接验证结合能力。" \
  --status --state done --step "分子生成" --index 3 --total 3 \
  --skill pocket-molecule-design --message "生成完成：11 个口袋适配分子"
```

> **Highlight the pocket residues.** They are what defined the generation,
> and the viewer draws them with side chains out so the user can see the
> cavity the molecules were built for. 87 residues is a lot to select by
> hand:
>
> ```bash
> POCKET=$(python3 -c "
> import gemmi, math, sys
> st = gemmi.read_structure(sys.argv[1]); st.setup_entities()
> # centroid of the reference ligand
> xs, ys, zs = [], [], []
> for ch in st[0]:
>     for res in ch:
>         if res.name == 'I1M':
>             for a in res:
>                 xs.append(a.pos.x); ys.append(a.pos.y); zs.append(a.pos.z)
> if not xs:
>     raise SystemExit('reference ligand I1M not found')
> c = (sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs))
> out = []
> for ch in st[0]:
>     for res in ch:
>         if gemmi.find_tabulated_residue(res.name) is None: continue
>         pts = [(a.pos.x, a.pos.y, a.pos.z) for a in res]
>         m = [sum(p[i] for p in pts)/len(pts) for i in range(3)]
>         if math.dist(m, c) < 10.0:
>             out.append(f'{ch.name}:{res.seqid.num}')
> print(','.join(out))
> " structures/7XWQ.cif)
> python3 $PUB --structure /tmp/combined.cif --highlight "$POCKET" ...
> ```
>
> 7XWQ carries I1M on both A and B; the snippet averages every copy into
> one centre, which lands between the two pockets. If the target is a
> single chain, filter on `ch.name` so the centre comes from that chain's
> ligand only.

### Writing `--notes`

See `structure-viewer` for how `--notes` is shown. What to put in it:

| Result | Usually means |
|--------|---------------|
| molecules_generated ≥ 10 | Enough; pick 3–5 for docking validation |
| molecules_generated < 5 | Too few; raise `beam_size` or loosen the pocket |
| pocket_residues > 150 | Pocket may be too large; tighten the cutoff |
| pocket_residues < 20 | Pocket may be too tight to fit a drug-like molecule |
| Molecules share one scaffold | The model converged; try a different seed or reference |

Worth stating when it applies: the reference ligand only sets the box
centre, the designs are free to differ from it.

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
