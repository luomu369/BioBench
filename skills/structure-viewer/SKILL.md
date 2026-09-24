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

1. **Declare the plan** before starting, so the user sees the route.
2. **Report each step** as it is reached, with matching `--step` names.
3. **Mark it done** with the result in `--message`.

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

# 1. the whole route, before anything runs
python3 $PUB --status --state queued --skill <your-skill> --job <run-id> \
  --plan "步骤一,步骤二,步骤三" --message "已规划：<what>，预计 <how long>"

# 2. each step as it is reached — same --job, --step matching the plan
python3 $PUB --status --state running --step "步骤一" --index 1 --total 3 \
  --skill <your-skill> --job <run-id> --message "正在<doing what>"

# 3. finished
python3 $PUB --status --state done --step "步骤三" --index 3 --total 3 \
  --skill <your-skill> --job <run-id> --message "<the result>"
```

**Declare a plan for anything over a few minutes.** It costs one flag and
turns "正在生成主链骨架" into a route the user can read from the first
second. A run of seconds does not need one — a single `--state running` is
enough.

**Give every run its own `--job` id.** Several skills can proceed at once —
a GPU design alongside a CPU docking run — and without distinct ids they
share one entry and clobber each other's progress. `--job` defaults to the
skill name, so re-running a skill updates its entry rather than adding a
duplicate; pass a distinct value to run two of the same skill side by side.

The viewer shows one job on the pill and the rest in the pull-up menu,
grouped into 排队中 / 运行中 / 已完成.

### Status fields

| Field | Meaning |
|-------|---------|
| `state` | `queued`/`idle` (waiting), `running`, `done`, `error` |
| `step` | 当前阶段的说明文字，直接显示在进度条上 |
| `step_index` / `step_total` | Drives the progress bar |
| `message` | Human-readable detail |
| `skill` | Which skill is running, for attribution |
| `job` | Identifies the run so several can proceed at once |
| `plan` | The whole route, declared up front |

**`--step` names must match the plan's spellings exactly.** That is how the
viewer knows which step to light up. A name that does not appear in the plan
leaves every step grey rather than lighting the wrong one — so if the plan
says `结构预测`, report `--step "结构预测"`, not `--step "folding"`.

A plan is declared once and carried forward: re-passing it on every step
update is unnecessary, and omitting it after the first call does not lose it.

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
from pathlib import Path

def publish(cif_text: str, meta: dict, viewer: Path):
    viewer.mkdir(parents=True, exist_ok=True)
    tmp = viewer / ".current.cif.tmp"
    tmp.write_text(cif_text)
    os.replace(tmp, viewer / "current.cif")

    meta["updated"] = datetime.now().isoformat(timespec="seconds")
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

```bash
BIOBENCH=/home/asus_gx10/biobench
RUN=$BIOBENCH/runs/my-design

# 1. Report progress
cat > $BIOBENCH/webapp/static/status.json <<'EOF'
{"state":"running","skill":"boltzgen-deploy","step":"folding",
 "step_index":4,"step_total":6,"message":"Refolding designs"}
EOF

# 2. Publish the top-ranked design
cp $RUN/intermediate_designs/rank0_*.cif \
   $BIOBENCH/webapp/static/.current.cif.tmp
mv $BIOBENCH/webapp/static/.current.cif.tmp \
   $BIOBENCH/webapp/static/current.cif

# 3. Publish metadata
cat > $BIOBENCH/webapp/static/.current.json.tmp <<'EOF'
{"name":"nk2r_binder_rank0","source":"boltzgen","format":"cif",
 "metrics":{"plddt":0.87},"notes":"Top-ranked of 8 designs"}
EOF
mv $BIOBENCH/webapp/static/.current.json.tmp \
   $BIOBENCH/webapp/static/current.json

# 4. Mark done
cat > $BIOBENCH/webapp/static/status.json <<'EOF'
{"state":"done","skill":"boltzgen-deploy","step":"filtering",
 "step_index":6,"step_total":6,"message":"Published rank0"}
EOF
```
