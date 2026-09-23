---
name: sequence-design
description: '**WORKFLOW SKILL** — Design amino acid sequences for a fixed protein backbone using ProteinMPNN, then publish the result to the BioBench Viewer. USE FOR: designing a sequence for a given backbone; inverse folding; redesigning a chain; scoring an existing sequence against a backbone; fixing or biasing specific positions. DO NOT USE FOR: installing ProteinMPNN (use proteinmpnn-deploy); designing binders de novo (use binder-design); publishing a structure without designing (use structure-viewer). Requires ProteinMPNN installed — run proteinmpnn-deploy first.'
---

# Sequence Design

Design sequences for a fixed backbone with ProteinMPNN and publish the result.

**Prerequisite:** ProteinMPNN installed. If not, follow `proteinmpnn-deploy`
first.

## When to Use

- "Design a sequence for this backbone"
- "Run inverse folding on this structure"
- "Redesign chain A, keep chain B fixed"
- "Score this sequence against the backbone"
- Fixing, biasing, or tying specific positions

## Confirm before running

ProteinMPNN is fast (~1 s), so a confirmation is less about cost and more
about intent. Still state the plan before running:

```
Backbone:   nk1r_binder_0.cif (2 chains)
Designing:  chain A (83 residues)
Fixed:      chain B, plus residues you selected
Sequences:  8, sampling temp 0.1, model v_48_020

Proceed?
```

Confirm:

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Number of sequences** | Ask if not given | 8 |
| **Which chains to design** | From the request | all chains |
| **Fixed positions** | From `selection.json` if the user selected residues | none |
| **Model** | Backbone confidence — `v_48_030` for generated backbones | `v_48_020` |

If the user gave a number, use it. Otherwise ask.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Prepare the backbone      (.pdb — convert from .cif if needed)
2. Choose what to design     (which chains, which positions)
3. Report progress           (structure-viewer publisher)
4. Run ProteinMPNN
5. Publish the backbone      (structure-viewer publisher)
```

## Step 1 — Prepare the backbone

**ProteinMPNN only reads PDB format.** mmCIF input crashes with
`ValueError: could not convert string to float`. Convert first:

```bash
python3 -c "
import biotite.structure.io as strucio
s = strucio.load_structure('backbone.cif')
strucio.save_structure('backbone.pdb', s)
print('chains:', sorted(set(s.chain_id)))
"
```

## Step 2 — Choose what to design

By default ProteinMPNN designs **every** chain. To design only some:

```bash
# Design chain A only; B is held fixed
--pdb-path-chains A

# Design A and C; B fixed
--pdb-path-chains A C
```

To fix individual residues, write a JSONL mapping the PDB name to the
positions to keep:

```jsonl
{"backbone.pdb": [3, 4, 5, 30]}
```

```bash
--fixed-positions-jsonl fixed.jsonl
```

### Using residues the user selected in the viewer

If the user selected residues and asked to design around / keep them, read
`webapp/static/selection.json` and turn it into a fixed-positions JSONL:

```bash
python3 -c "
import json
sel = json.load(open('/home/asus_gx10/biobench/webapp/static/selection.json'))
if not sel.get('count'):
    raise SystemExit('no residues selected — ask the user to pick some')
# Positions are per-chain in the viewer; ProteinMPNN wants one flat list
# per PDB, so keep only the chain being designed.
chain = 'A'
pos = sorted(r['resi'] for r in sel['residues'] if r['chain'] == chain)
print(json.dumps({'backbone.pdb': pos}))
" > fixed.jsonl
```

Then pass `--fixed-positions-jsonl fixed.jsonl`.

**If `selection.json` is missing or `count` is 0**, the user has not selected
anything — ask them to click residues in the viewer rather than guessing which
positions matter.

## Step 3 — Report progress

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step sequence_design --index 1 --total 2 \
  --skill sequence-design --message "正在为骨架设计氨基酸序列"
```

ProteinMPNN finishes in about a second, so there is little intermediate
progress to report — but still write the message in biological terms rather
than naming the model or the algorithm.

## Step 4 — Run

```bash
protein_mpnn_run \
  --pdb-path backbone.pdb \
  --out-folder out \
  --model-name v_48_020 \
  --num-seq-per-target 8 \
  --sampling-temp 0.1 \
  --seed 42 \
  --batch-size 1
```

| Flag | Meaning |
|------|---------|
| `--num-seq-per-target` | How many sequences to sample per backbone |
| `--sampling-temp` | 0.1 is near-deterministic; higher explores more |
| `--seed` | Set for reproducibility |
| `--batch-size` | Lower if VRAM is tight |
| `--pdb-path-chains` | Which chains to design (default: all) |
| `--ca-only` | Backbone has Cα atoms only |
| `--use-soluble-model` | Soluble-protein-trained weights |
| `--save-score` | Write per-residue `-log_prob` `.npy` files |
| `--save-probs` | Write full per-position probability tensors |

### Model choice

| Model | Backbone noise | Use |
|-------|---------------|-----|
| `v_48_002` | 0.02 Å | High-confidence backbones |
| `v_48_020` | 0.20 Å | **Default** |
| `v_48_030` | 0.30 Å | Noisy or predicted backbones |

For a backbone that came out of a generative model (e.g. BoltzGen), prefer
`v_48_030` — it tolerates backbone error better.

### Output

```
out/seqs/<pdb_name>.fa
```

FASTA with one entry per sampled sequence:

```
>T=0.1, sample=1, score=0.8758, global_score=0.8758, seq_recovery=0.4511
MQIFVKTLTGKTITLEVE...
```

| Field | Meaning |
|-------|---------|
| `score` | Mean per-residue `-log_prob`; **lower is better** |
| `global_score` | Same, over all residues |
| `seq_recovery` | Fraction matching the input sequence (if one was given) |

Multiple chains are joined with `/` in one entry.

## Step 5 — Publish

ProteinMPNN outputs sequences, not structures. Publish the **backbone** so the
user can see what was designed, and put the sequence metrics in the metadata:

```bash
python3 $PUB \
  --structure backbone.pdb \
  --name "$(basename backbone.pdb .pdb)_designed" \
  --source proteinmpnn \
  --roles "A=designed,B=fixed" \
  --metrics '{"sequences": {"label": "序列数", "value": 8}, "best_score": {"label": "最佳得分", "value": 0.8758, "hint": "负对数似然，越低越好"}}' \
  --notes "设计 8 条序列（模型产出的候选数量），最佳得分 0.876
（ProteinMPNN 的负对数似然，越低越好，表示序列与骨架越匹配）。
建议：得分良好，可挑选前 3 条用结构预测验证折叠。" \
  --status --state done --step design --index 2 --total 2 \
  --skill sequence-design --message "8 sequences designed"
```

Adjust `--roles` to the actual chain IDs in your PDB.

### Writing `--notes`

The viewer shows `--notes` verbatim, so write whatever the numbers alone
don't say. No required structure. Reference material if you want it:

| Metric | What it is |
|--------|------------|
| `sequences` | 设计的候选序列数量 |
| `best_score` | 负对数似然，**越低越好** |
| `recovery` | 与天然序列的一致比例，越高越好 |

| Result | Usually means |
|--------|---------------|
| best_score < 1.0 | 序列与骨架匹配良好；挑前几条做结构预测 |
| best_score > 1.5 | 匹配较差；检查骨架质量或提高采样温度 |
| recovery > 50% | 与天然序列高度一致，设计偏保守 |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ValueError: could not convert string to float` | mmCIF input | Convert to PDB with biotite |
| `UnboundLocalError: checkpoint_path` | Passed `--path-to-model-weights` | Remove that flag; weights are bundled |
| `ModuleNotFoundError: pkg_resources` | setuptools 81+ | `pip install "setuptools<81"` |
| CUDA OOM | batch too large | Lower `--batch-size` |
| All sequences identical | `--sampling-temp` too low | Raise to 0.2–0.5 |

## References

- [ProteinMPNN GitHub](https://github.com/dauparas/ProteinMPNN)
- Dauparas et al., *Science* 378, 49–56 (2022)
