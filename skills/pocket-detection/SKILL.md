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
1. Run the detection
2. Interpret the ranking
3. Publish the top pockets             (structure-viewer publisher)
```

## Step 1 — Run

```bash
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python
DETECT=/home/asus_gx10/biobench/skills/pocket-detection/scripts/detect_pockets.py

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

## Step 2 — Interpret

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

## Step 3 — Publish

Publish the receptor with the top pockets' lining residues highlighted, so
the user sees where they are:

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

$PY -c "
import json
d = json.load(open('pockets_out/pockets.json'))
print(','.join(f\"{r['chain']}:{r['resi']}\" for p in d['pockets'][:3]
               for r in p.get('lining', [])))
" > /tmp/pocket_res.txt

python3 $PUB \
  --structure 7XWQ.cif \
  --name "7XWQ_pockets" --source fpocket \
  --highlight "$(cat /tmp/pocket_res.txt)" \
  --metrics '{"pockets_found": {"label": "发现口袋数", "value": 19},
              "best_score": {"label": "最佳药物相似度", "value": 0.81},
              "best_volume": {"label": "最佳口袋体积", "value": 503, "hint": "A^3"}}' \
  --notes "..." \
  --status --state done --step "口袋发现" --index 2 --total 2 \
  --skill pocket-detection --message "发现 19 个口袋，最佳 3 个已发布"
```

> **The pocket atom files are the clearest thing to show.** Each pocket
> gets `<stem>_out/pocketN_atm.pdb` — the alpha spheres that define the
> cavity. Publishing the receptor with those residues highlighted is
> usually more useful than publishing the spheres themselves, which float
> free of the structure.

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
