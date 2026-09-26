---
name: pocket-detection
description: '**WORKFLOW SKILL** — Find binding pockets in a protein structure with fpocket, rank them by drug-likeness, and publish the best ones to the BioBench Viewer. USE FOR: finding where a small molecule could bind; locating binding sites without a known ligand; comparing pockets across structures; choosing a docking box from an apo structure; exploring a new target with no co-crystallised ligand. DO NOT USE FOR: docking a ligand you already have (use molecular-docking); designing a molecule for a known pocket (use pocket-molecule-design); installing fpocket (see the install section below). INVOKES: shell tools, file system tools. No GPU.'
---

# Pocket Detection

Find the cavities in a protein and rank them by how drug-like they are,
using fpocket. This is the skill to reach for when there is **no ligand in
hand** — it answers "where could something bind" rather than "how does this
particular molecule bind".

## When to Use

- "Where could a drug bind on this protein?"
- "Find the binding pockets" / "find binding sites"
- "I have an apo structure, where do I dock?"
- "Which pocket looks most druggable?"
- "Compare the pockets in these two structures"

## Confirm before running

fpocket takes seconds, so confirm intent rather than cost:

```
Structure:   7XWQ.cif (ERβ ligand-binding domain, 4 chains)
Pockets:     all, ranked by drug score
Filters:     none
Publish:     top 3 to the viewer

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Structure** | The receptor to search | required |
| **Top N** | How many to publish | 3 |
| **Min volume** | Drop pockets too small to hold a drug | 200 Å³ |
| **Chain filter** | fpocket reports per-chain composition but searches all | all |

## Installation

fpocket is not packaged for aarch64 — conda-forge has no build, and the
release binaries are x86-64 only. Build from source:

```bash
cd /tmp && rm -rf fpocket-src
git clone --depth 1 https://github.com/Discngine/fpocket.git fpocket-src
cd fpocket-src && make
```

`make` will fail at the final link:

```
/usr/bin/ld: plugins/LINUXAMD64/molfile/libmolfile_plugin.a: file in wrong format
```

That static library is prebuilt x86-64. fpocket only reaches it through
`open_mmcif()` (mmCIF input) and `read_topology()` with an Amber prmtop —
**PDB input never touches it**. Stub the four symbols and link by hand:

```bash
cat > obj/molfile_stub.c <<'EOF'
int molfile_pdbxplugin_init(void) { return -1; }
int molfile_pdbxplugin_register(void *a, void *b) { (void)a; (void)b; return -1; }
int molfile_parm7plugin_init(void) { return -1; }
int molfile_parm7plugin_register(void *a, void *b) { (void)a; (void)b; return -1; }
EOF
gcc -c obj/molfile_stub.c -o obj/molfile_stub.o
gcc obj/*.o src/qhull/src/libqhull/*.o src/qhull/src/qvoronoi/*.o \
    src/qhull/src/qconvex/*.o -o bin/fpocket -lm -lstdc++
mkdir -p ~/tools/fpocket && cp -r bin ~/tools/fpocket/
```

Verify:

```bash
~/tools/fpocket/bin/fpocket --help    # prints the 4.0 usage banner
```

> **This build reads PDB only.** The stub makes an mmCIF input fail rather
> than silently return nothing, so convert first — `detect_pockets.py`
> does it for you.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Report progress                    (structure-viewer publisher)
2. Run the detection
3. Interpret the ranking
4. Publish the top pockets             (structure-viewer publisher)
```

## Step 1 — Report progress

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "口袋发现" --index 1 --total 2 \
  --skill pocket-detection --message "正在搜索结合口袋"
```

fpocket finishes in seconds, so there is little intermediate progress —
but the status bar has to move off whatever the previous skill left there.
Without this the viewer keeps showing the last run's message, which reads
as though the new one never happened.

## Step 2 — Run

```bash
CONDA="${CONDA:-$HOME/miniconda3}"
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PY=$CONDA/envs/docking/bin/python
DETECT=$BB/skills/pocket-detection/scripts/detect_pockets.py

$PY $DETECT --structure 7XWQ.cif --out pockets_out --top 5
```

Output:

```
found 5 pockets; best drug_score 0.8093 (volume 503 A^3, centroid [0.61, -15.6, -9.23])
  pocket   3  score 0.8093  vol    503.2  centroid [0.61, -15.6, -9.23]
  pocket   2  score 0.7064  vol    494.6  centroid [-3.08, -7.64, -39.74]
  ...
```

`pockets.json` holds every descriptor fpocket reported, plus the path to
each pocket's atom file and its centroid.

### What the ranking means

`drug_score` is fpocket's own composite — volume, hydrophobicity, polarity,
charge and local hydrophobic density combined. It is **relative**, not a
probability: 0.8 does not mean "80% druggable". Use it to order pockets
against each other in the same structure, not to compare across structures.

| Descriptor | Reads as |
|------------|----------|
| `drug_score` | Composite druggability; higher is better |
| `volume` | Cavity size in Å³; a drug-sized pocket is roughly 200–1000 |
| `hydrophobicity_score` | Higher = greasier cavity, favours hydrophobic ligands |
| `polarity_score` | Higher = more polar lining, favours H-bonding ligands |
| `charge_score` | Net electrostatic character of the lining |
| `n_abpa` | Alpha-sphere count; a proxy for how enclosed the cavity is |
| `flex` | Mean B-factor of the lining residues; high = mobile rim |

> **Pocket IDs are not ranks.** fpocket numbers them by size, so pocket 1
> is the largest, not the best. The script sorts by `drug_score` before
> reporting; read the order it prints, not the IDs.

## Step 3 — Interpret

Say what the top pockets are, in the user's terms:

- **Which chain** lines each pocket, and whether it sits at an interface
  (`inter_chain` > 0 means the cavity spans two chains — often the most
  interesting site, and often missed by single-chain docking).
- **How it compares to a known site.** If the structure has a
  co-crystallised ligand, check whether a pocket's centroid lands on it.
  A pocket that does is a sanity check that the detection is working; a
  pocket that does not is a candidate allosteric site.
- **What the chemistry suggests.** A hydrophobic, enclosed pocket suits
  hydrophobic scaffolds; a polar, open one suits H-bonding ligands.

### When the structure has a co-crystallised ligand

**Match pockets against the ligand, not against `drug_score`.** The
highest-scoring pocket is often not the one the ligand occupies — a
co-crystallised complex has its binding site already filled, so fpocket
reports the *empty* cavities around it and ranks those highest.

Compute the ligand's centroid and find the pocket nearest to it:

```bash
$PY -c "
import gemmi, numpy as np, glob, os, re, json
AA = set('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL MSE'.split())
st = gemmi.read_structure('receptor.pdb')
# The ligand chain — exclude waters and standard amino acids
lig = [r for ch in st[0] for r in ch if r.name not in AA and r.name not in ('HOH','WAT','DOD')]
if not lig:
    raise SystemExit('no ligand chain found')
pc = np.array([[a.pos.x,a.pos.y,a.pos.z] for r in lig for a in r]).mean(axis=0)
print('ligand centroid:', pc.round(1))

d = json.load(open('pockets_out/pockets.json'))
rows = []
for p in d['pockets']:
    dist = float(np.linalg.norm(np.array(p['centroid']) - pc))
    rows.append((dist, p))
rows.sort(key=lambda r: r[0])
for dist, p in rows[:3]:
    print(f\"  pocket {p['id']:3d}  {dist:6.1f} A from ligand  score {p['drug_score']:.3f}  vol {p['volume']:.0f}\")
"
```

A pocket within ~15 Å of the ligand centroid is the binding site. Report
both numbers — the distance and the score — because a pocket that is close
but scores poorly is still the right one to design against.

### Verify which chain is the receptor before extracting one

**Do not assume chain A is the target.** Multi-chain PDB entries routinely
put the receptor in a later chain, and the first chain is often a G-protein
partner, an antibody, or a second conformation.

In 6X18 (GLP-1R + GLP-1 + G protein) the receptor is chain **R**, not
chain A. Extracting "A + peptide" produced two chains 50 Å apart with no
residue of A anywhere near the peptide, and every pocket found was
meaningless. The check that catches it takes one command:

```bash
$PY -c "
import gemmi, numpy as np
st = gemmi.read_structure('complex.cif')
for ch in st[0]:
    pts = np.array([[a.pos.x,a.pos.y,a.pos.z] for r in ch for a in r])
    print(f'  {ch.name}: {len(ch):4d} res  centroid {pts.mean(axis=0).round(1)}')
"
```

The chain whose centroid sits nearest the ligand's is the receptor. Confirm
it before handing anything downstream — a design run against the wrong chain
costs twenty minutes of GPU time and produces a binder for nothing.

## Step 4 — Publish

Publish the receptor with the top pockets' lining residues highlighted, so
the user sees where they are:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

Build the visualisation first — the receptor with the top pockets' alpha
spheres as their own chains — then publish that:

```bash
CLOUD=$BB/skills/pocket-detection/scripts/pocket_cloud.py

$PY $CLOUD --pockets pockets_out/pockets.json --receptor 7XWQ.cif \
  --out pockets_out/pockets_vis.pdb --top 3

python3 $PUB \
  --structure pockets_out/pockets_vis.pdb \
  --name "7XWQ_pockets" --source fpocket \
  --roles "A=receptor,B=receptor,p=pocket_1,q=pocket_2,r=pocket_3" \
  --metrics '{"pockets_found": {"label": "发现口袋数", "value": 40},
              "best_score": {"label": "最佳药物相似度", "value": 0.81},
              "best_volume": {"label": "最佳口袋体积", "value": 508, "hint": "A^3"}}' \
  --notes "..." \
  --status --state done --step "口袋发现" --index 2 --total 2 \
  --skill pocket-detection --message "发现 40 个口袋，最佳 3 个已发布"
```

The viewer draws `STP` residues as white spheres, so the cavities read as
volumes rather than as loose atoms. Clicking one selects it — it takes the
selection colour and grows slightly, without changing opacity, which would
reorder the transparent pass and fade the others with it.

> **`--top` is capped at 26.** `pocket_cloud.py` names pocket chains from a
> 26-letter alphabet, and fpocket routinely reports 40 pockets on a
> multi-chain structure. Pass the number you actually want to see; the
> ranking in `pockets.json` is already by `drug_score`, so the first N are
> the best N.

> **Publish the count you found, not the count you drew.** 40 pockets were
> detected and 3 shown is a useful sentence; "3 pockets" is wrong.

## Handing a pocket to the next step

The centroid is what a docking box or a generation run needs:

```bash
$PY -c "
import json
d = json.load(open('pockets_out/pockets.json'))
p = d['pockets'][0]
print(' '.join(str(round(c)) for c in p['centroid']))
"
```

Feed it to `molecular-docking` as `--center`, or to
`pocket-molecule-design` as `--center`. A box of 20–22 Å around the
centroid covers a pocket of this size with room for the ligand to move.

### Handing hotspots to a design run

A design run needs residues, not a centroid. When the structure has a
co-crystallised ligand, the interface residues are the receptor residues
within a cutoff of the ligand's centroid:

```bash
$PY -c "
import gemmi, numpy as np
AA = set('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL MSE'.split())
st = gemmi.read_structure('receptor.pdb')
lig = [r for ch in st[0] for r in ch if r.name not in AA and r.name not in ('HOH','WAT','DOD')]
pc = np.array([[a.pos.x,a.pos.y,a.pos.z] for r in lig for a in r]).mean(axis=0)
hot = []
for ch in st[0]:
    for r in ch:
        if r.name not in AA: continue
        d = min(np.linalg.norm([a.pos.x,a.pos.y,a.pos.z] - pc) for a in r)
        if d <= 8.0: hot.append((ch.name, r.seqid.num, r.name))
print(' '.join(f'{c}:{n}' for c,n,nm in hot))
"
```

8 Å from the centroid is the cutoff that worked on the GLP-1R/GLP-1
interface and returned 7 residues — enough to bias a design without
over-constraining it. Pass them to `binder-design` as the `binding` block,
and to the publisher as `--highlight` so the user sees where the design was
built.

> **Exclude waters and non-standard residues from the ligand set.** A
> co-crystallised complex carries dozens of waters, and including them drags
> the centroid into the solvent. Filter on the standard amino-acid names as
> above rather than assuming the ligand is the only non-protein chain.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `fpocket: command not found` | Not installed or not on PATH | Build from source above; or set `FPOCKET_BIN` |
| `file in wrong format` at link | Prebuilt x86-64 molfile library | Stub the four symbols and link by hand |
| `Invalid pdb name given` | No `-f` argument | Pass `--structure` |
| Empty output directory | Input was mmCIF and the stub rejected it | Convert to PDB first; the script does this |
| No pockets found | Structure has no enclosed cavity | Check the input is a folded protein, not a peptide |
| Pocket centroid in empty space | Pocket is a surface groove, not a cavity | Lower `--min-volume`, or inspect the pocket's atom file |
| Hundreds of pockets | Very large or multi-chain structure | Raise `--min-volume` and use `--top` |
