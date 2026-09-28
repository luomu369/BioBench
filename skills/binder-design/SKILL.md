---
name: binder-design
description: '**WORKFLOW SKILL** — Design protein binders against a target using BoltzGen, then publish the result to the BioBench Viewer. USE FOR: designing a binder for a protein target; generating de novo binders; running a BoltzGen design pipeline; choosing design parameters; interpreting design metrics. DO NOT USE FOR: installing BoltzGen (use boltzgen-deploy); publishing a structure without running a design (use structure-viewer); sequence design on a fixed backbone (use proteinmpnn-deploy). Requires BoltzGen already installed — run boltzgen-deploy first.'
---

# Binder Design

Run a BoltzGen binder design against a protein target and publish the result.

**Prerequisite:** BoltzGen installed and weights present. If not, follow
`boltzgen-deploy` first.

## When to Use

- "Design a binder for X"
- "Generate binders against this structure"
- "Run a BoltzGen design"
- Interpreting or ranking design results

## Confirm before running

**A design run costs 20–30 minutes of GPU time. Never start one without
confirming the parameters with the user first.**

Before running, state the plan and ask for confirmation:

```
Target:      NK1R (6HLL), chain B — 469 residues
Binder type: cyclic peptide, 12–18 residues
Hotspots:    8 residues you selected (B:430, B:435, ...)
Designs:     4
Est. time:   ~25 minutes on this machine

Proceed?
```

Confirm these four things:

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Number of designs** | Ask the user. More designs = better odds, linearly more time | 4 |
| **Binder type / length** | From the request; peptide 12–18, protein 70–110 | protein 70–110 |
| **Hotspots** | From `selection.json` if the user selected residues | none |
| **Protocol** | From the binder type | `protein-anything` |

If the user gave a number ("design 8 binders"), use it — do not re-ask. If
they did not, **ask** rather than assuming.

**"Cyclic" is a spec field, not a description.** If the request says cyclic
peptide, the entity needs `cyclic: true` — see "A cyclic peptide needs
`cyclic: true`" below. Leaving it out produces a linear peptide that passes
every check.

Only proceed to Step 5 after the user confirms.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Obtain the target structure        (.cif / .pdb)
2. Write the design spec              (YAML)
3. Validate the spec                  (boltzgen check)
4. Report progress                    (structure-viewer publisher)
5. Run the design                     (boltzgen run)
6. Publish the top-ranked result      (structure-viewer publisher)
```

## Step 1 — Obtain the target

Local file, or fetch from RCSB by PDB ID:

```bash
curl -sL -o target.cif "https://files.rcsb.org/download/6HLL.cif"
```

Note which chain to design against — most PDB entries put the receptor in
chain A.

## Step 2 — Write the design spec

```yaml
entities:
  # The designed binder: a length range, BoltzGen samples within it
  - protein:
      id: B
      sequence: 70..110
  # The target, taken from a local file
  - file:
      path: target.cif
      include:
        - chain:
            id: A
```

The designed chain's `id` is arbitrary; BoltzGen renames chains in its output,
so do not rely on it downstream.

### Designing around user-selected residues

If the user selected residues in the viewer and asked to design around them,
the selection is already in the request as a `[当前结构：...]` block — use the
residues it lists.

**The hotspot key is `binding_types`, not `binding`.** A `binding` block on a
`file` entity is rejected by `boltzgen check` with `Found invalid keys in yaml
file: {'binding'}`. The form that passes:

```yaml
entities:
  - protein:
      id: B
      sequence: 70..110
  - file:
      path: target.cif
      include:
        - chain:
            id: A
      binding_types:
        - chain:
            id: A
            binding: "216,217,220"
```

> **`binding` takes a string, not a list.** `"216,217,220"` passes;
> `[216, 217, 220]` does not. The same applies to `res_index`.

> **`res_index` and `binding` are 1-based positions within the chain as it
> appears in the file — not the PDB's auth numbers.** Auth 582 on a chain
> whose first residue is auth 531 is position 52. Getting this wrong does not
> fail: the run completes and the design is biased toward the wrong residues,
> which is worse than an error because nothing says so.
>
> Convert with the chain's own numbering rather than assuming it starts at 1:
>
> ```bash
> $PY -c "
> import gemmi
> st = gemmi.read_structure('target.cif')
> ch = st[0]['A']                      # the chain named in include
> auth = [r.seqid.num for r in ch]
> print('first auth:', auth[0], 'last:', auth[-1], 'count:', len(auth))
> for want in (582, 586, 590):
>     print(f'auth {want} -> res_index {auth.index(want) + 1}')
> "
> ```
>
> **Never put `include_proximity` in a spec whose result will be published.**
> It crops the target to the residues within `radius` of the hotspots and
> deletes the rest of every chain, so the design output is a receptor in
> pieces — one 1RUZ run left chain E as 103-111, 265-268, 301-303, 309-310,
> and the viewer drew a structure the user read as broken. The file is
> valid and the metrics are real, which is what makes the mistake so easy
> to publish. Design against the whole target: it is slower, and what
> reaches the viewer is the complete structure. `binding_types` alone gives
> the same pocket bias without the crop. A cropped spec is acceptable only
> for a throwaway run whose output is never published, and the spec must
> say so in a comment.

### A cyclic peptide needs `cyclic: true`

**A peptide entity is linear by default, and nothing warns you otherwise.**
`boltzgen check` passes, the run completes, the metrics look normal — and the
result is an open chain. The field is a boolean on the protein entity:

```yaml
entities:
  - protein:
      id: B
      sequence: 12..18
      cyclic: true      # head-to-tail bond: first residue's N to last's C
```

Without it BoltzGen sets `cyclic_period = 0` and adds no closing bond. A
comment saying "cyclic peptide" does nothing — the field is the only thing
that does.

### A complete cyclic-peptide spec

The shape of the whole file. Every value in `<angle brackets>` is something to
fill in — the chain ids and the indices are the two that are easy to get wrong,
and each has its own section below:

```yaml
entities:
  # The designed binder: 12 residues, head-to-tail closed
  - protein:
      id: P
      sequence: 12..12
      cyclic: true
  # The target, complete: every chain in the file, uncropped. No `include`,
  # no `include_proximity` — the design runs against the whole target so the
  # published complex shows the complete receptor.
  - file:
      path: <target.cif>
      # The hotspots themselves — 1-based positions in that chain of this file
      binding_types:
        - chain: {id: <label chain>, binding: "<i,j,k>"}
```

`sequence: 12..12` is a fixed length; `12..18` lets BoltzGen sample within the
range. A user who asked for "12 aa" wants the former.

> **Do not go reading BoltzGen's source to work out the schema.** The keys
> above are the ones that pass `boltzgen check`. Reading `schema.py` to
> confirm them costs a dozen tool calls and reaches the same answer — if a
> spec is rejected, the error message names the invalid key and lists the
> valid ones, which is faster than the source and always current.

### Which chain id: `label_asym_id`, not the auth chain

**This is the one that costs the most when it is wrong.** An mmCIF file names
each chain twice: `auth_asym_id` is the PDB's own chain name (`H`, `I`, `J`,
`K`, `L`, `M` for 1RUZ), and `label_asym_id` is the file's internal one
(`A`, `B`, `C`, `D`, `E`, `F`, …). They are not the same, and they do not
correspond in order — a glycan gets its own `label_asym_id` inside an auth
chain, so auth `K` can be label `D` while auth `H` is label `K`.

**`include` and `binding_types` take `label_asym_id`** — as does
`include_proximity`, which a published spec must not contain (see the
warning above). The viewer shows auth names, the selection arrives with
auth names, and the spec wants label names — so the conversion has to
happen explicitly.

Get the mapping in one call, and get the `res_index` in the same call:

```bash
$PY -c "
import gemmi
st = gemmi.read_structure('<target.cif>')
# gemmi's chain.name is the auth name; the label name comes from the block
block = st.make_mmcif_document().sole_block()
label_of = {}
for row in block.find('_atom_site.', ['auth_asym_id', 'label_asym_id']):
    label_of.setdefault(row.str(0), row.str(1))
print('auth -> label:', label_of)
# residues of one chain, in file order — gemmi indexes by auth name
want = '<auth chain>'
ch = [c for c in st[0] if c.name == want][0]
auth = [r.seqid.num for r in ch]
print(f'auth {want} = label {label_of[want]}: {len(auth)} res, '
      f'auth {auth[0]}..{auth[-1]}')
for n in (<582>, <586>, <590>):
    print(f'  auth {n} -> res_index {auth.index(n) + 1}')
"
```

Write the **label** name in the spec and the **res_index** this prints. For
1RUZ that is `id: D` with `binding: "82,86,90"` — the auth names the viewer
shows (`K`, `582`) appear nowhere in the yaml.

> **A wrong chain id does not say so.** `Specified end 79,83,86,90 is higher
> than the length of the chain` is what a chain id that resolved to the wrong
> (shorter) chain looks like — not a missing chain, which would be a clearer
> error. When that message appears, the chain id is the first thing to check,
> and the mapping above is how to check it. Reading BoltzGen's `parse_range`
> to understand the message will not help: the numbers are fine, the chain is
> not.

**Verify the ring in the output before publishing.** Count the covalent bonds
between the designed chain's first and last residue:

```bash
$PY -c "
import gemmi
st = gemmi.read_structure('rank1_design.cif')
ch = [c for c in st[0] if len(c) < 40][0]   # the designed peptide
res = list(ch)
first, last = res[0], res[-1]
n1 = {a.name for a in first if a.element.name == 'N'}
c1 = {a.name for a in last if a.element.name == 'C'}
print(f'{ch.name}: {len(res)} res, first {first.name} last {last.name}')
print('N atoms in first:', n1, ' C atoms in last:', c1)
"
```

A ring shows the closing bond in the structure's bond records; a linear
peptide has a free N-terminus and C-terminus. Check it before publishing —
calling a linear peptide cyclic in `--notes` is a claim about the molecule
that the file does not support.

**If the request has no `[当前结构：...]` block**, the user has not selected
anything — ask them to click residues in the viewer first rather than guessing
a binding site.

## Step 3 — Validate

```bash
MODELS="${MODELS:-$HOME/models}"
HF_HOME=$MODELS/boltzgen-1 HF_HUB_OFFLINE=1 \
  boltzgen check design.yaml
```

This reports unresolved residues it will trim, the total designed length, and
writes a visualization `.cif` you can inspect before committing to a run.

## Step 4 — Report progress

A design run is 20–30 minutes, so declare the whole route before starting it
— the viewer then shows the plan from the first second instead of revealing
one step at a time. The reporting convention is in `structure-viewer`.

Name the steps for the user, not after BoltzGen's stages. For a standard
six-stage run:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state queued --skill binder-design --job boltz1 \
  --plan "生成主链,折叠验证,结构预测,亲和力评估,汇总指标,筛选排序" \
  --message "已规划：4 个 binder 设计，预计 25 分钟"
```

Adjust the route to the run — fewer designs, an extra analysis the user asked
for, a protocol that skips a stage. Then report each step as it is reached
with the same `--job` and a `--step` matching what you declared.

### What to report at each step

Write messages a biologist reads — describe the biology, not the algorithm.
Avoid raw terms like "diffusion sampling" or "DataLoader".

The pipeline has six stages, but **name the steps yourself**. The plan is
what the user reads, so phrase it for them rather than copying BoltzGen's
internal stage names — `design_folding` means nothing to a biologist, while
"折叠验证" says what is happening. A typical route:

```
生成主链 → 折叠验证 → 结构预测 → 亲和力评估 → 汇总指标 → 筛选排序
```

Split or merge stages when the run warrants it. A quick two-design check
might be three steps; a run where the user asked for extra analysis might
have seven. The only rule is that the `--step` you report matches what you
declared.

Update the message whenever the run moves to a new step, and include the
sub-progress when the log shows it (e.g. `2/4 designs generated`).

## Step 5 — Run

`--num_designs` must match what the user confirmed in Step 0.

```bash
MODELS="${MODELS:-$HOME/models}"
HF_HOME=$MODELS/boltzgen-1 HF_HUB_OFFLINE=1 \
boltzgen run design.yaml \
  --output results/ \
  --protocol protein-anything \
  --num_designs 4 \
  --diffusion_batch_size 1 \
  --num_workers 0 \
  --use_kernels false \
  --skip_inverse_folding \
  --steps design design_folding folding affinity analysis filtering
```

| Flag | Why |
|------|-----|
| `--num_designs` | **Must equal the confirmed count** — each design adds ~2 min |
| `HF_HUB_OFFLINE=1` | Prevents background weight downloads that look like a hang |
| `--num_workers 0` | Avoids DataLoader worker deadlock on some systems |
| `--skip_inverse_folding` | Skips the `ifold` checkpoint |
| `--steps ...` | Explicit pipeline; omitting `inverse_folding` avoids needing `mols.zip` |
| `--use_kernels false` | Disables optimized kernels; set `auto` once a run succeeds |

### Run it in the background, and report the pid

A design run is 20–30 minutes and the log is silent for most of it, so the
call is backgrounded. The pid is what the viewer watches to know the run is
over — a step that only says `running` in status.json is one the viewer
cannot tell from a crashed run, and it waits on work that ended minutes ago.

```bash
setsid nohup env HF_HOME=$MODELS/boltzgen-1 HF_HUB_OFFLINE=1 \
  boltzgen run design.yaml --output results/ ... > run.log 2>&1 &
PID=$!
echo "started pid $PID"

python3 $PUB --status --state running --step "生成主铯" \
  --index 2 --total 5 --skill binder-design --job my_design \
  --pid $PID --message "正在运行 BoltzGen"
```

`setsid` rather than plain `&` so the run survives the shell that started it,
and `$!` immediately after so the pid is the one just launched. Pass the same
`--pid` again on the `--state done` report only if the process is still
yours; otherwise leave it out and the viewer clears it.

### Protocols

| Protocol | Use |
|----------|-----|
| `protein-anything` | Protein binder for any target |
| `protein-protein` | Protein–protein binder |
| `ligand-binder` | Small-molecule binder |
| `nanobody-anything` | Nanobody design |
| `peptide-anything` | Peptide binder |
| `protein-redesign` | Redesign an existing binder |

### Pipeline steps

| Step | Produces |
|------|----------|
| `design` | Backbone structures via diffusion |
| `inverse_folding` | Sequences for designed backbones (needs `ifold` + `mols.zip`) |
| `design_folding` | Refolds designed sequences |
| `folding` | Structure + confidence (pLDDT) |
| `affinity` | Binding affinity estimates |
| `analysis` | Aggregated metrics |
| `filtering` | Ranked, filtered final designs |

### Choosing parameters

Read available memory first:

```bash
python3 -c "import torch; f,t = torch.cuda.mem_get_info(); print(f'{f/1024**3:.1f}/{t/1024**3:.1f} GB free')"
```

| Available VRAM | diffusion_batch_size | num_designs |
|----------------|---------------------|-------------|
| < 24 GB | 1 | 2–4 |
| 24–40 GB | 1 | 4–8 |
| 40–80 GB | 4 | 8–16 |
| > 80 GB | 8 | 16–32 |

Each design takes ~2 minutes on a DGX Spark; a 4-design run with folding and
affinity takes roughly 20–30 minutes.

## Step 6 — Publish the result

```bash
RUN=results

# Top-ranked design, falling back to the final complex
BEST=$(ls "$RUN"/intermediate_designs/rank0_*.cif 2>/dev/null | head -1)
[[ -z "$BEST" ]] && BEST="$RUN"/<spec_name>.cif

python3 $PUB \
  --structure "$BEST" \
  --name "$(basename "$BEST" .cif)" \
  --source boltzgen \
  --roles "A=binder,B=target" \
  --metrics '{"designs": {"label": "设计数", "value": 4}, "ipTM": {"label": "界面 TM-score", "value": 0.43, "hint": ">0.7 为好"}}' \
  --notes "生成 4 个设计（模型产出的候选数量），界面预测 TM-score 0.43
（复合物界面置信度，>0.7 为好，当前值说明界面可信度中等）。
建议：增加设计数量或调整约束残基以提升界面置信度。" \
  --status --state done --step "筛选并排序设计" --index 5 --total 5 \
  --skill binder-design --message "Published $(basename "$BEST")"
```

**The published structure must be the complete target.** If the spec
cropped the receptor, the output is a receptor in pieces and the viewer
shows a broken structure — valid file, real metrics, wrong picture. Two
checks before publishing:

1. `boltzgen check` reports the residues it trims ("Removing leading
   and/or trailing unresolved residues"). The design output may be short
   only by those.
2. Every target chain in the output carries the source's residue count
   minus that trim, with no interior residues missing:

```bash
$PY -c "
import gemmi, sys
src = gemmi.read_structure(sys.argv[1])
out = gemmi.read_structure(sys.argv[2])[0]
# boltzgen names output chains by label_asym_id; count the source the same way
counts = {}
for ch in src[0]:
    for sub in ch.subchains():
        counts[sub.subchain_id()] = len(sub)
for c in out:
    if len(c) < 40:
        continue                        # the designed binder
    want = counts.get(c.name)
    assert want is not None, f'chain {c.name} is not a source chain'
    # 8 residues of slack for the leading/trailing trim check reported
    assert len(c) >= want - 8, \
        f'chain {c.name}: {len(c)} of {want} residues — context was cropped'
    print(f'{c.name}: {len(c)} of {want} residues, complete')
print('every target chain is complete')
" <source.cif> <rank1_design.cif>
```

A chain short by more than the trim was cropped. Re-run the design against
the whole target — do not publish the crop, and do not "repair" it by
merging fragments back together after the fact; the design was made
against the crop, and the merged pose is not the pose that was scored.

**Check the chain IDs in the generated `.cif` before setting `--roles`** —
BoltzGen does not preserve the IDs from your spec.

> **Highlight the hotspots.** If the spec constrained specific target
> residues, pass them so the viewer shows where the binder was designed to
> sit:
>
> ```bash
>   --highlight "B:145,B:146,B:149"
> ```
>
> The viewer draws them with side chains out in the selection colour, so
> the user can see the interface the design was built around rather than
> guessing which residues the spec named.

### Writing `--notes`

See `structure-viewer` for how `--notes` is shown. What to put in it:

| Metric | What it is |
|--------|------------|
| `designs` | 生成的候选数量 |
| `ipTM` | 界面预测 TM-score，复合物界面置信度，>0.7 为好 |
| `plddt` | 单残基置信度，>70 为好 |
| `ptm` | 整体折叠置信度 |

| Result | Usually means |
|--------|---------------|
| ipTM < 0.5 | 界面置信度偏低；增加设计数量或调整约束残基 |
| ipTM > 0.8 | 界面可信；可进入序列设计阶段 |
| plddt < 70 | 折叠本身不可靠，需重新设计 |

### Reading the metrics CSV

`results/intermediate_designs/aggregate_metrics_analyze.csv` has one row per
design and ~200 columns. The ones worth reading:

```bash
python3 -c "
import csv
for r in csv.DictReader(open('results/intermediate_designs/aggregate_metrics_analyze.csv')):
    print(f\"{r['id']}  {r['designed_sequence']}\")
    print(f\"  ipTM {float(r['iptm']):.3f}  pTM {float(r['ptm']):.3f}  pLDDT {float(r['complex_plddt']):.2f}\")
    print(f\"  min_interaction_pae {float(r['min_interaction_pae']):.2f}  hbonds {r['plip_hbonds']}  saltbridges {r['plip_saltbridge']}\")
    print(f\"  delta_sasa {float(r['delta_sasa_original']):.0f}  liability {r['liability_score']}\")
"
```

**`min_interaction_pae` is the number to check alongside ipTM.** It is the
predicted aligned error across the interface in Å — lower is better, and
anything above ~10 means the model is unsure where the two chains sit
relative to each other. A design with ipTM 0.50 and PAE 10.4 is weaker than
one with ipTM 0.57 and PAE 6.1, even though both ipTMs look similar.

**`plip_hbonds` and `plip_saltbridge` count specific contacts.** Zero of both
does not mean the design failed — a binder held by hydrophobic burial shows
neither. Read `delta_sasa` with them: a large buried surface with no
hydrogen bonds is a hydrophobic interface, which is a real binding mode, not
an absence of one.

**`designability_rmsd_2` / `_4` are booleans in the CSV**, not floats. They
say whether the designed backbone refolds to within 2 or 4 Å of itself —
`True` is the pass. Do not try to `float()` them.

**Ranked output lands in `final_ranked_designs/final_30_designs/`**, named
`rank1_<design>.cif`, `rank2_...`. The rank is BoltzGen's own ordering;
check the metrics before trusting it, since it weighs columns this skill does
not.

## Output layout

```
results/
├── config/                  # Generated per-step YAML
├── intermediate_designs/    # Per-design .cif + .npz
├── steps.yaml
└── <spec_name>.cif          # Final complex structure
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Run appears hung at `[Step 1/5] design` | Background download of missing weights | Set `HF_HUB_OFFLINE=1` |
| Network traffic spikes during a run | Same — BoltzGen fetching artifacts on demand | `HF_HUB_OFFLINE=1` + `--cache <dir>` |
| DataLoader worker at 0% CPU | Worker deadlock | `--num_workers 0` |
| CUDA OOM | batch_size too high | Lower `--diffusion_batch_size` |
| `boltzgen: command not found` | Env not activated | `conda activate boltzgen` |

### Diagnosing a suspected hang

```bash
# Is it downloading? Watch the HF cache grow over 30s
du -sh ~/.cache/huggingface; sleep 30; du -sh ~/.cache/huggingface

# Is the GPU actually working?
nvidia-smi --query-compute-apps=pid,used_memory --format=csv

# Is a child process alive and burning CPU?
pgrep -af boltzgen
```

## References

- [BoltzGen GitHub](https://github.com/HannesStark/boltzgen)
- [BoltzGen weights on HF](https://huggingface.co/boltzgen/boltzgen-1)
