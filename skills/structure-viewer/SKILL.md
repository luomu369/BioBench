---
name: structure-viewer
description: '**OUTPUT CONVENTION** — How BioBench skills publish results to the BioBench Viewer web app. USE FOR: any skill that produces a protein structure the user should see; writing current.cif / current.json; reporting task progress to the viewer; understanding the viewer contract. DO NOT USE FOR: installing models (see boltzgen-deploy); analyzing sequences without producing a structure. Every BioBench skill that generates or loads a structure MUST follow this convention.'
---

# Structure Viewer Output Convention

The BioBench Viewer is a static web app that renders whatever structure a
skill publishes. It has no backend and no LLM — it only watches two files.

## The contract

Every skill that produces a structure for the user to see MUST write:

| File | Purpose | Direction |
|------|---------|-----------|
| `webapp/static/current.cif` | The structure to render (mmCIF or PDB format) | skill → viewer |
| `webapp/static/current.json` | Metadata describing what was produced | skill → viewer |
| `webapp/static/status.json` | Progress bar and status message | skill → viewer |
| `webapp/static/selection.json` | Residues the user selected in the viewer | viewer → skill |

The viewer polls `current.json` every 2 seconds. When its `updated` timestamp
changes, the viewer reloads `current.cif` and re-renders.

`selection.json` flows the other way: the viewer writes it whenever the user
changes their residue selection, and skills read it to learn what the user is
pointing at.

## selection.json schema

```json
{
  "structure": "nk1r_binder_rank0",
  "count": 3,
  "residues": [
    {"chain": "B", "resi": 216, "resn": "TYR", "color": "#ff6b6b"},
    {"chain": "B", "resi": 217, "resn": "GLU", "color": "#4ecdc4"},
    {"chain": "A", "resi": 12,  "resn": "LEU", "color": "#ffe66d"}
  ],
  "updated": "2026-09-22T14:30:00"
}
```

| Field | Meaning |
|-------|---------|
| `structure` | Name from `current.json` — which structure was selected from |
| `count` | Total selected items across all lists |
| `residues[].chain` | Chain ID |
| `residues[].resi` | Residue number (author numbering) |
| `residues[].resn` | Three-letter residue name |
| `residues[].color` | Highlight color assigned by the viewer |
| `view` | What the user is currently looking at — see below |

### `view` — which structure is on screen

The viewer keeps every structure of the session as a tab, and the contract
files hold only the latest one. Without `view`, "design a binder for this"
is ambiguous the moment more than one structure is open.

```json
{
  "view": {
    "tab": 1,
    "tab_name": "pockets_A",
    "tabs": ["docked_F", "pockets_A", "mutant_B"],
    "sel_mode": "res",
    "style": "surface",
    "color": "chain",
    "ligand_style": "stick",
    "hidden_chains": ["C"]
  }
}
```

| Field | Meaning |
|-------|---------|
| `tab` | Index into `tabs` of the structure on screen |
| `tab_name` | Its name — the same value as `structure` |
| `tabs` | Every structure open, oldest first |
| `sel_mode` | `res`, `chain`, `atom`, or `bond` — what a click selects |
| `style` | `surface`, `cartoon`, `stick`, `sphere`, or `line` |
| `color` | `chain`, `spectrum`, or `secondary` |
| `ligand_style` | `stick`, `ballstick`, `spacefill`, or `line` |
| `hidden_chains` | Chains the user hid with the eye toggles |

**Read `view.tab_name` before acting on a selection.** A residue selected
on a tab the user has since left is not what they are pointing at. When
`view.tabs` has more than one entry, say which structure you are working
on rather than assuming.

### Selections on the other open tabs

The top-level fields describe the tab on screen. Every other open tab that
has a selection is reported under `other_tabs`, keyed by tab name:

```json
{
  "structure": "pep15_rank1",
  "chains": [{"chain": "B", "whole_chain": true}],
  "view": {"tab": 0, "tab_name": "pep15_rank1", "tabs": ["pep15_rank1", "gb_docked"]},
  "other_tabs": {
    "gb_docked": {"count": 1, "chains": [{"chain": "A", "whole_chain": true}]}
  }
}
```

This is what makes "superimpose the two chains I selected" answerable: the
two selections are almost always made on different tabs, and the active tab
alone carries only one of them. Tabs with nothing selected are omitted, so
an absent key means "nothing picked there", not "unknown".

Each entry has the same shape as the top level — `residues`, `chains`,
`atoms`, `bonds`, `pockets` — plus a `count`. Residue and chain selections
are stable across publishes; atom and bond indices are only meaningful
against the structure named by the key.

The Selection panel has four modes, and a selection can mix them. Two more
lists appear when the user picks atoms or bonds:

```json
{
  "atoms": [{"index": 3521, "label": "C — UNL 1 · L", "color": "#ff6b6b"}],
  "bonds": [{"indices": [3521, 3524], "label": "C1#3521–C1#3524", "color": "#4ecdc4"}]
}
```

| Field | Meaning |
|-------|---------|
| `atoms[].index` | 3Dmol's global atom index — unique across the model |
| `atoms[].label` | `ATOM — RESN RESI · CHAIN`, as shown in the panel |
| `bonds[].indices` | The two atom indices the bond connects |
| `bonds[].label` | `ATOM<resi>–ATOM<resi>`, with `#index` when both ends read alike |

> **Atom indices are not stable across publishes.** They are positions in
> the file 3Dmol parsed, so re-publishing the same structure with a
> different atom order renumbers everything. Residue and chain selections
> are stable; atom and bond selections are only meaningful against the
> structure named in `structure`. If a skill needs to act on an atom
> selection, resolve the index against the current model rather than
> storing it.

### Reading the selection

When the user says something like "design around my selected residues" or
"use the residues I picked", read this file:

```bash
python3 -c "
import json
d = json.load(open('/home/asus_gx10/biobench/webapp/static/selection.json'))
v = d.get('view', {})
print('on screen:', v.get('tab_name'), f\"(tab {v.get('tab')} of {len(v.get('tabs', []))})\")
print('structure:', d['structure'], '| count:', d['count'])
for r in d['residues']:
    print(f\"  {r['chain']}:{r['resi']} {r['resn']}\")
"
```

If the file is missing or `count` is 0, the user has not selected anything —
ask them to select residues in the viewer first.

**Check `view` before acting.** When more than one tab is open, the
selection may belong to a structure the user has since navigated away
from. `view.tab_name` says which one is on screen; if it differs from
`structure`, say so rather than silently working on the wrong one.

The same file answers "what am I looking at" without any selection at all
— `view.tabs`, `view.style`, `view.sel_mode` and `view.hidden_chains` are
written on every tab switch, style change and chain toggle, not just on
selection.

### Using it in a design

BoltzGen accepts hotspot residues in the design spec. Convert the selection
into the `binding_site` block:

```yaml
entities:
  - protein:
      id: B
      sequence: 70..110
  - file:
      path: target.cif
      include:
        - chain:
            id: B
      binding:
        - chain:
            id: B
            residue: [216, 217, 220]
```

Generate that `residue` list from `selection.json`, filtered to the target
chain. See `binder-design` for the full spec format.

## current.json schema

```json
{
  "name": "nk2r_binder_01",
  "source": "boltzgen",
  "format": "cif",
  "chains": [
    {"id": "A", "role": "target", "residues": 336},
    {"id": "B", "role": "binder", "residues": 84}
  ],
  "metrics": {
    "plddt": 0.87,
    "iptm": 0.72,
    "rmsd": 1.9
  },
  "notes": "Designed against NK2R (7xwo) chain A",
  "highlight": [
    {"chain": "A", "resi": 145},
    {"chain": "A", "resi": 146}
  ],
  "updated": "2026-09-22T12:30:00"
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Short identifier shown in the viewer header |
| `source` | yes | Which skill produced it (`boltzgen`, `rcsb`, `esmfold`, …) |
| `format` | yes | `cif` or `pdb` — tells the viewer how to parse |
| `chains` | no | Chain list; drives the chain dropdown |
| `metrics` | no | Key numbers shown in the metrics panel |
| `notes` | no | Free-text context for the user |
| `highlight` | no | Residues to select on arrival; see below |
| `updated` | yes | ISO timestamp; the viewer's change detector |

## Progress reporting

Long-running tasks should report progress so the viewer can show it. **The
agent reports progress — it knows what it is doing, so it should say so
directly rather than making a script guess from log output.**

Use the bundled publisher:

```bash
python3 /home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py \
  --status --state running --step "生成 binder 主链骨架" --index 1 --total 6 \
  --skill boltzgen-deploy --message "Diffusion sampling, batch 1/4"
```

Call it whenever the task moves to a new phase. Typical points:

- before starting a long computation
- after each pipeline step completes
- when a partial result is worth showing
- on success (`--state done`) or failure (`--state error`)

### Every skill reports the same way

This is the convention, not one skill's habit. Whatever you are running —
a design, a docking sweep, a pocket search — the sequence is the same:

1. **Publish the input structure first**, so the viewer has something to
   show from the first second.
2. **Declare the plan**, so the user sees the route.
3. **Report each step** as it is reached, with matching `--step` names.
4. **Mark it done** with the result in `--message`.

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

# 1. the input, before anything runs — the user watches THIS while waiting
python3 $PUB --structure receptor.pdb --name nk2r_input \
  --source rcsb --roles "A=receptor" \
  --job <run-id> --message "输入结构"

# 2. the whole route
python3 $PUB --status --state queued --skill <your-skill> --job <run-id> \
  --label "<what the user should read>" \
  --plan "步骤一,步骤二,步骤三" --message "已规划：<what>，预计 <how long>"

# 3. each step as it is reached — same --job, --step matching the plan
python3 $PUB --status --state running --step "步骤一" --index 1 --total 3 \
  --skill <your-skill> --job <run-id> --message "正在<doing what>"

# 4. finished
python3 $PUB --status --state done --step "步骤三" --index 3 --total 3 \
  --skill <your-skill> --job <run-id> --message "<the result>"
```

> **Step 1 is not optional.** A task that declares a plan but publishes
> nothing leaves the viewer showing the *previous* task's structure — so for
> the twenty minutes a simulation runs, the user is looking at something
> unrelated and has no way to see what the current task is even working on.
> Publishing the input costs one call and binds the task to the viewer
> immediately. If the task's input is a structure you already published
> under another job, republish it under this job's id so the tab groups
> correctly.

**Declare a plan for anything over a few minutes.** It costs one flag and
turns "正在生成主链骨架" into a route the user can read from the first
second. A run of seconds does not need one — a single `--state running` is
enough. Where the line falls is your call: a docking sweep that takes ninety
seconds probably is not worth a plan, a pocket search that takes four minutes
probably is.

**A plan is a list of small tasks, not one big one.** Each step becomes its
own entry in the viewer's task list, with its own start time, message and
state. That is deliberate: a long run is a sequence of small tasks, and the
ones already finished are the record of what it did. Collapsing them into a
single row with a roadmap beside it hides everything but the step in
progress — the user cannot go back and look at what the run already
produced. So split the plan to the granularity the user would want to
inspect: "获取结构,口袋发现,环肽设计,序列验证,性质评估,发布结果" rather than
"设计,验证".

**Give every run its own `--job` id.** Several skills can proceed at once —
a GPU design alongside a CPU docking run — and without distinct ids they
share one entry and clobber each other's progress. `--job` defaults to the
skill name, so re-running a skill updates its entry rather than adding a
duplicate; pass a distinct value to run two of the same skill side by side.

**Use `--label` for anything the user should read.** The `--job` id is what a
structure records as its task, so it has to stay short and stable —
`glp1r`, not `GLP-1R 环肽设计流程`. Pass the readable name separately:

```bash
python3 $PUB --status --state queued --skill biobench --job glp1r \
  --label "GLP-1R 环肽设计" --plan "..."
```

The viewer shows one step on the pill and the rest in the pull-up menu,
grouped by task into 排队中 / 运行中 / 已完成. A finished step is clickable
and jumps to the structure its task published.

### Reporting a multi-skill pipeline

When one request spans several skills — "design a cyclic peptide for GLP-1R
and validate it" runs pocket detection, binder design, sequence search and
ADMET — keep them under **one `--job`** with one plan, and let each skill
report its own step:

```bash
# The pipeline declares the whole route once
python3 $PUB --status --state queued --skill biobench --job glp1r \
  --label "GLP-1R 环肽设计" \
  --plan "获取结构,口袋发现,环肽设计,序列验证,性质评估,发布结果" \
  --message "已规划：GLP-1R 从靶点发现到环肽设计全流程"

# Each skill reports its own step, naming itself in --skill
python3 $PUB --status --state running --step "口袋发现" --index 2 --total 6 \
  --skill pocket-detection --job glp1r --message "正在用 fpocket 搜索结合口袋"

python3 $PUB --status --state done --step "口袋发现" --index 2 --total 6 \
  --skill pocket-detection --job glp1r --message "Pocket 3 为 GLP-1 结合位点"
```

The `--skill` on each report is what attributes the step, so the menu shows
which tool did what. The `--job` stays constant across all of them, which is
what keeps the six steps in one group instead of six unrelated entries.

**A step that produces no structure still gets reported.** Sequence search
and property analysis produce findings, not files — report them with
`--state done` and put the finding in `--message`. You do **not** need to
pass `--structure` for such a step: the publisher gives every step of the
task a structure to point at, so all of them are clickable.

The structure a step gets is the one that matches **where the task was at
that point**, not simply the newest one:

- A step that published its own structure keeps it.
- A step that published none takes the structure from the latest step **at
  or before its own index** — so "preparing the receptor" points at the
  receptor being prepared, and "docking complete" points at the docked
  complex.
- A step before every publish takes the first one, which is the task's
  input.

This is why publishing the input structure as step 1 matters beyond keeping
the viewer busy: it is what the early steps point at. Publish it with
`--index 1` so the publisher knows where it sits:

```bash
python3 $PUB --structure receptor.pdb --name nk2r_input --source rcsb \
  --job nk2r --step "获取结构" --index 1 --total 6 --message "输入结构"
```

So the rule is: **publish each structure as the step that produced it, with
its `--index`, and do not hand-wire `--structure` onto steps that have
none.** The only case where a step stays unclickable is a task that never
published any structure at all.

### Keep `--message` short

The pill at the bottom of the screen is one line tall. A long message does
not get truncated — it wraps, and the pill grows from a slim bar into a
block six lines tall that covers the middle of the viewer. The task menu
rows have the same problem, and there the long text pushes the other steps
out of view.

**Aim for under 30 characters. Hard ceiling 60.** Count Chinese characters
and digits as one each.

| | |
|---|---|
| `正在预测复合物结构并计算 pLDDT` | 17 — good |
| `Pocket 3 为 GLP-1 结合位点` | 18 — good |
| `20/20 分子对接成功，最佳 -8.91 kcal/mol` | 28 — good |
| `MW 1533 Da、cLogP -3.6、TPSA 634、QED 0.03、SA 6.46。Lipinski/Ghose/Veber 全未过，Brenk 警示 2 处` | 70 — too long |

The numbers still have a home. Put them in `--metrics`, which renders on the
step's row in the menu and in the metrics panel, both of which are built for
tabular data:

```bash
# The message says what happened; the numbers go where they can be read.
python3 $PUB --status --state done --step "性质评估" --index 5 --total 6 \
  --skill admet-prediction --job glp1r \
  --metrics '{"mw": {"label": "分子量", "value": 1533, "hint": "Da"},
             "qed": {"label": "QED", "value": 0.03},
             "lipinski": {"label": "Lipinski", "value": "未过"}}' \
  --message "类药性评估完成，多项规则未过"
```

A message that needs a sentence to be honest is usually two facts. Keep the
one the user is waiting for; the other belongs in `--metrics` or in the
structure's `--notes`, which has no length limit and is shown in full in the
metrics panel.

**`--metrics` goes wherever it can be seen.** The metrics panel reads numbers
off the structure on screen, so a call with no `--structure` has nothing for
it to read — the same flag then attaches them to the step instead, and the
viewer renders them on its row in the panel's format:

```bash
# no --structure: --metrics lands on the step
python3 $PUB --status --state done --step "序列验证" --index 4 --total 6 \
  --skill sequence-search --job glp1r \
  --metrics '{"hits": {"label": "PDB 同源命中", "value": 0, "hint": "1.56M 序列中"}}' \
  --message "0 命中 — 全新序列"

# with --structure: --metrics lands on the structure, in the panel
python3 $PUB --structure pocket.cif --name my_pocket --source fpocket \
  --job glp1r --step "口袋发现" --index 2 --total 6 \
  --metrics '{"druggability": 0.57}'
```

So there is one flag to remember. `--step-metrics` exists only for the call
that publishes a structure *and* wants numbers on the step as well — pass
both and each goes to its own place.

### Status fields

| Field | Meaning |
|-------|---------|
| `state` | `queued`/`idle` (waiting), `running`, `done`, `error` |
| `step` | 当前阶段的说明文字，直接显示在进度条上 |
| `index` / `total` | Drives the progress bar |
| `message` | Human-readable detail |
| `skill` | Which skill is running, for attribution |
| `group` | The task this step belongs to; set from `--job` |
| `plan` | The whole route, declared up front |

**`--step` names must match the plan's spellings exactly.** That is how the
viewer knows which step a report is about. A name that does not appear in the
plan becomes a step of its own rather than overwriting an unrelated one — so
if the plan says `结构预测`, report `--step "结构预测"`, not
`--step "folding"`.

A plan is declared once and carried forward: re-passing it on every step
update is unnecessary, and omitting it after the first call does not lose it.

**Reaching a step finishes the ones before it.** Reporting step 3 marks
steps 1 and 2 done, so a skill that reports only its last step does not
leave earlier ones waiting forever.

**The plan is yours to write.** Nothing checks it against what the run
actually does. Phrase the steps for the user rather than after a tool's
internal stage names, and split or merge them to match the run — a quick
check might be three steps, a thorough one seven. A plan that reads as a
route the user recognises is worth more than one that mirrors the pipeline.

### Publishing a structure

```bash
python3 .../publish.py \
  --structure /path/to/design.cif \
  --name nk1r_binder_rank0 \
  --source boltzgen \
  --roles "A=target,B=binder" \
  --metrics '{"plddt": 0.87, "iptm": 0.72}' \
  --notes "Top-ranked of 4 designs"
```

Chain counts are parsed from the file automatically — do not hand-write them.
Use `--roles` to label chains (e.g. `A=target,B=binder`) so the viewer's
dropdown shows their purpose.

Both actions can be combined in one call by passing `--structure` and
`--status` together.

### Ligands sharing a chain with the protein

**A ligand must never be dropped, and it must be visible as a ligand.** Many
entries put the small molecule in the same `auth_asym_id` as the protein it
binds — 1HNN has SAH and SKF inside chains A and B. `parse_chains` decides
whether a chain is a ligand by asking whether *most* of its residues are
non-amino-acid, so a ligand sharing a chain with 260 residues is never
marked: it vanishes from the chain panel, renders in the protein's cartoon
style instead of its own stick style, and the ligand style control has
nothing to act on.

The atoms are not missing — only the metadata. Split them out first:

```bash
python3 /home/asus_gx10/biobench/skills/structure-viewer/scripts/split_ligands.py \
  --structure 1HNN.cif --out 1HNN_split.pdb
```

```
wrote 1HNN_split.pdb
  protein residues : 528
  ligand residues  : 4 in 4 chains
  waters           : 126 (dropped)
  ligand chains    : L1 (C), L2 (D), L3 (E), L4 (F)
```

It groups by mmCIF subchain (`label_asym_id`), which already records each
ligand separately, so two copies of the same ligand in one entry become two
chains rather than collapsing into one. Waters are dropped by default — they
dominate the atom count and render as noise; `--keep-waters` keeps them.

Then publish the split file with roles naming each ligand:

```bash
python3 .../publish.py --structure 1HNN_split.pdb --name 1hnn \
  --roles "A=protease,B=protease,L1=sah,L2=skf,L3=sah,L4=skf"
```

> **Check the chain panel after publishing.** If a ligand you know is in the
> file does not appear there, it is sharing a chain with the protein — split
> it. The 3D view will still show the atoms, which is exactly what makes this
> easy to miss: the structure looks complete while the ligand is unlabelled
> and unstyleable.

**Pass `--step` with `--structure` when the run has a plan.** That records
which step produced this structure, so the task list's finished steps each
lead to what they made rather than all falling back to the task's first
publish:

```bash
python3 .../publish.py --structure pocket.cif --name my_pocket \
  --source fpocket --job glp1r --step "口袋发现" --index 2 --total 6
```

Every publish is also archived under `webapp/static/structures/` and listed
in `structures.json`, so a reload of the viewer reopens the whole session's
structures as tabs instead of only the latest one.

### Highlighting what you just changed

`--highlight` names residues the viewer should select on arrival, so the
user sees the result of your work without hunting for it:

```bash
--highlight "A:385,A:386,A:387,A:388"
```

Format is `chain:resi`, comma-separated. The viewer selects them exactly as
if the user had clicked — same colours, same Selection panel rows, same
`selection.json` — so they can be dismissed individually or cleared in one
go.

Use it whenever a run changed specific positions:

| Skill | Highlight |
|-------|-----------|
| `sequence-design` | the residues you mutated |
| `molecular-docking` | the flexible residues that moved |
| `pocket-molecule-design` | the pocket residues that defined the box |
| `binder-design` | the hotspot residues from the spec |

Residues the published structure does not contain are skipped silently — a
skill may name a chain it dropped, or numbering that shifted between the
input and the output. Derive the list from the file you are actually
publishing, not from the input.

### Selecting residues in what is already on screen

`--select` does the same job as `--highlight` but applies to the structure
the user is already looking at, without publishing anything:

```bash
python3 $PUB --select "hA:466,hA:468,hA:463"
```

This is the answer to "select everything within 5 A of this residue" and
anything else a skill computes from the loaded model. `selection.json` only
flows viewer → skill, so without this channel a computed result has nowhere
to go.

The viewer polls for the request, applies it, then deletes the file — so a
stale request cannot re-apply later and re-select residues the user has
since dismissed by hand. Residues already selected are left alone rather
than duplicated.

### `--metrics` and `--notes` are yours to shape

The viewer renders whatever you put in `--metrics` and `--notes` — it keeps
no vocabulary of its own, so nothing is translated or reformatted. You
decide what the user sees.

A metric is either a bare value or an object you write yourself:

```bash
# bare — the key is shown as-is
--metrics '{"best_affinity": -8.55}'

# labelled — you name it and say which way is good
--metrics '{"best_affinity": {"label": "结合亲和力", "value": -8.55, "hint": "kcal/mol，越负越强"}}'
```

`label` replaces the key, `hint` is dimmed text after the number. Both are
optional; a bare number still renders.

`--notes` is free text shown under the rows. Use it for whatever the numbers
alone don't say — what the run did, what the result means, what to try next.
There is no required structure:

```bash
--notes "亲和力 -8.55 kcal/mol，5 个姿态。建议：与共晶配体对比打分。"
```

Write as much or as little as the result deserves. A quick progress publish
can carry metrics alone; a final result usually wants a sentence or two.

## Writing these files

Use atomic writes so the viewer never reads a half-written file:

```bash
# Copy to a temp name in the same directory, then rename
cp result.cif webapp/static/.current.cif.tmp
mv webapp/static/.current.cif.tmp webapp/static/current.cif
```

In Python:

```python
import json, os
from datetime import datetime, timezone
from pathlib import Path

def publish(cif_text: str, meta: dict, viewer: Path):
    viewer.mkdir(parents=True, exist_ok=True)
    tmp = viewer / ".current.cif.tmp"
    tmp.write_text(cif_text)
    os.replace(tmp, viewer / "current.cif")

    # UTC with the zone spelled out. A bare isoformat() gives local time with
    # no offset, and the browser reads it in its own zone — on a UTC machine
    # serving a GMT+0800 browser that puts every timestamp eight hours off.
    meta["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = viewer / ".current.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2))
    os.replace(tmp, viewer / "current.json")
```

## Rules

1. **Never write directly to `current.cif`** — always write-then-rename, or the
   viewer may render a truncated file.
2. **Always bump `updated`** — the viewer uses it to detect changes; a stale
   timestamp means the viewer ignores your update.
3. **`format` must match the file** — the viewer passes `mmcif` or `pdb` to
   3Dmol.js based on this field. `publish.py` detects it from the file's
   first line rather than its name, since the contract file is always
   called `current.cif` even when it holds PDB text.
4. **Clean up on failure** — set `status.json` state to `error` with a message
   so the user sees why nothing appeared.
5. **Keep structures under ~5 MB** — larger files slow the browser. If a
   structure is huge, publish a representative chain instead and note it.
6. **`--notes` is free text** — the viewer shows it verbatim. Use it for
   whatever the numbers alone don't say; there is no required format.
7. **Highlight what you changed** — if the run touched specific residues,
   pass them with `--highlight`. The viewer selects them on arrival with
   their side chains drawn, so the user sees the result instead of hunting
   for it. Derive the list from the file you are publishing.
8. **Check what you built before publishing it.** A viewer renders a
   broken file as happily as a good one, so a silent fault in the merge
   step reaches the user as a wrong structure. Before publishing anything
   assembled from more than one source, confirm:

   ```bash
   python3 -c "
   import gemmi, sys
   st = gemmi.read_structure(sys.argv[1]); st.setup_entities()
   for c in st[0]:
       n = [r.seqid.num for r in c]
       gaps = sum(1 for a, b in zip(n, n[1:]) if b != a + 1)
       print(f'{c.name}: {len(n)} residues, {gaps} gaps')
   " combined.pdb
   ```

   Residue counts should match the inputs, and gaps should match the
   source structure's own unresolved regions — no more. Extra gaps mean
   the merge dropped residues.

9. **Every task reports what it is doing, including the trivial ones.**
   Loading a structure is a task: declare it, report it, publish it. A
   structure that appears with no status history leaves the user guessing
   what produced it, and a task that runs for minutes with no reports
   leaves them watching something unrelated. The minimum for any task:

   ```bash
   # the input, first — the viewer has something to show immediately
   python3 $PUB --structure input.pdb --name my_input --source rcsb --job <id>
   # the route
   python3 $PUB --status --state queued --skill <skill> --job <id> \
     --label "<readable name>" --plan "步骤一,步骤二"
   # each step, with the structure it produced
   python3 $PUB --status --state done --step "步骤一" --index 1 --total 2 \
     --skill <skill> --job <id> --structure out.pdb --name out \
     --message "<what happened>"
   ```

10. **A ligand is never optional.** If the input has one, it reaches the
    viewer — as its own chain, with its own style. See "Ligands sharing a
    chain with the protein" above; the check is whether it appears in the
    chain panel, not whether the 3D view looks full.

## When the user says the structure looks wrong

Ask **where** before theorising. "It's broken" has cost several rounds of
misdiagnosis: a chain rendered in pieces was blamed on residue ordering,
then on a format-detection bug, and only turned out to be 19 residues
deleted from one chain by a number-only match. Each guess was plausible
and each was wrong.

The useful questions, in order:

1. **Which chain, and roughly where?** The viewer's hover tooltip names
   the residue under the cursor — ask the user to hover the break.
2. **Did it look right before the last step?** This localises the fault
   to one stage of the pipeline immediately.
3. **What does the file actually contain?** Read it back with gemmi and
   compare residue counts and ordering against the input. Do not reason
   about the rendering until the data is confirmed.

The viewer's own model is inspectable too, which distinguishes "the file
is wrong" from "the viewer mis-read a correct file":

```js
document.querySelector('#viewer canvas')._3dmol_viewer.models[0].atoms
```

## Example: publishing a BoltzGen result

The whole flow, end to end. Everything goes through the publisher — writing
`status.json` by hand would skip the job bookkeeping and leave the viewer's
task list out of step.

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py
RUN=runs/my-design

# 1. Declare the route before the run starts
python3 $PUB --status --state queued --skill binder-design --job boltz1 \
  --plan "生成主链,折叠验证,结构预测,亲和力评估,汇总指标,筛选排序" \
  --message "已规划：4 个 binder 设计，预计 25 分钟"

# 2. Report each step as it is reached
python3 $PUB --status --state running --step "结构预测" --index 3 --total 6 \
  --skill binder-design --job boltz1 --message "正在预测复合物结构并计算 pLDDT"

# 3. Publish the top-ranked design, with the hotspots highlighted
python3 $PUB --structure $RUN/intermediate_designs/rank0_design_0.cif \
  --name "nk2r_binder_rank0" --source boltzgen \
  --roles "A=binder,B=target" \
  --highlight "B:145,B:146,B:149" \
  --metrics '{"designs": {"label": "设计数", "value": 4},
             "ipTM": {"label": "界面 TM-score", "value": 0.72, "hint": ">0.7 为好"}}' \
  --notes "Top-ranked of 4 designs. ipTM 0.72 — the interface is credible."

# 4. Mark the job done
python3 $PUB --status --state done --step "筛选排序" --index 6 --total 6 \
  --skill binder-design --job boltz1 --message "已发布 rank0，ipTM 0.72"
```

Steps 3 and 4 can be one call — `--structure` and `--status` together.
