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

### Mutating the residues the user selected

"Mutate what I selected" is a different job from "design a sequence for this
backbone": the positions are given, and the question is what to put there.
`scripts/mutate_selection.py` does the whole thing — reads the selection,
converts the numbering, runs the scan, writes the mutant:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/sequence-design/scripts/mutate_selection.py \
  --pdb backbone.pdb --out mutant_dir --chain A
```

| Mode | What it answers |
|------|-----------------|
| `--mode per-position` (default) | Best replacement at each site, one run per position |
| `--mode combined` | Several sequences with every site mutated at once |

It prints the mutation table, writes `mutant.pdb` with the replacements
applied, and saves `mutations.json`. Publish that PDB with `--highlight` on
the mutated positions.

> **Check where the selected residues are before promising a result.** A
> selection can be a stretch of surface far from the binding site — mutating
> it will not change ligand binding, and saying so up front is better than
> letting the user assume otherwise. Compare against the pocket residues
> (`pocket-detection`) or the co-crystallised ligand's centroid.

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

> **Positions are indices into ProteinMPNN's own expansion of the chain,
> not PDB residue numbers.** `parse_PDB` walks `min_resn..max_resn` and
> fills every gap with `X`, so a chain numbered 263–498 with 219 residues
> present has 236 slots, and PDB 385 sits at index 123. Passing PDB
> numbers either crashes (`IndexError: index 384 is out of bounds for axis
> 0 with size 236`) or, when the numbers happen to be in range, silently
> constrains the wrong residues — a run meant to mutate 385–388 instead
> omitted S/R/F/R at indices 101–104, which are D/R/D/E.
>
> Derive the indices from the parser instead of assuming numbering starts
> at 1:
>
> ```bash
> python3 -c "
> import sys
> sys.path.insert(0, '<site-packages>/proteinmpnn')
> from protein_mpnn_utils import parse_PDB
> seq = parse_PDB('backbone.pdb')[0]['seq_chain_A']
> print(len(seq), seq[122:126])   # 236 SRFR
> "
> ```
>
> The JSONL key is the file stem (`backbone`), not the filename
> (`backbone.pdb`) — `parse_PDB` strips the extension, and a mismatched key
> raises `KeyError`.

### Mutating positions rather than fixing them

`--fixed-positions-jsonl` *keeps* the wild-type residue. To force a change,
omit the wild-type amino acid at that position with `--omit-AA-jsonl`, whose
format is per-chain and per-position:

```jsonl
{"backbone": {"A": [[[123], ["S"]], [[124], ["R"]], [[125], ["F"]], [[126], ["R"]]]}}
```

Each entry is `[[positions], [amino acids to exclude]]`. The model then
picks freely among the remaining 19. To force a *specific* substitution
instead, use `--bias-by-res-jsonl` to weight the target residue strongly.

> **`--omit-AA-jsonl` positions are 1-based; `parse_PDB` indices are
> 0-based.** `tied_featurize` does `idx_AA = np.array(item[0])-1`, so the
> number in the JSONL is one higher than the index you read out of the
> sequence. Passing the 0-based index mutates the residue *before* the one
> you meant — and nothing complains. The run succeeds, the FASTA looks
> normal, and the position you asked to change still holds its wild-type
> amino acid. We saw exactly that: a scan meant to mutate PDB 11–22 instead
> reported D→D, E→E, P→P, A→A, D→D, S→S unchanged, because every omit had
> landed one residue early.
>
> Convert once, and verify the conversion before running:
>
> ```bash
> python3 -c "
> import sys
> sys.path.insert(0, '<site-packages>')
> from proteinmpnn.protein_mpnn_utils import parse_PDB
> seq = parse_PDB('backbone.pdb')[0]['seq_chain_A']
> pdb, wt = 11, 'D'          # what you want to mutate
> idx = pdb - min_resn        # 0-based index into seq
> assert seq[idx] == wt, f'index {idx} is {seq[idx]}, expected {wt}'
> print(idx + 1)              # <- this is what goes in the JSONL
> "
> ```
>
> The `assert` is the part that matters. It catches an off-by-one, a wrong
> `min_resn`, or a chain whose numbering is not what you assumed — all three
> produce a plausible-looking run against the wrong residues.

### Scanning many positions at once

A saturation scan omits every position's wild-type amino acid in **one run** —
the JSONL takes as many entries as you like, and the model replaces them all
together:

```bash
read_selection.py --chain A --format mpnn-omit --pdb-path backbone.pdb > omit.jsonl

/path/to/env/bin/protein_mpnn_run \
  --pdb-path backbone.pdb --out-folder scan \
  --model-name v_48_020 --num-seq-per-target 8 \
  --sampling-temp 0.1 --seed 42 --batch-size 1 \
  --omit-AA-jsonl omit.jsonl
```

One run mutating fifteen positions takes the same ~6 s as one mutating a
single position, so prefer it over a loop. Raise `--num-seq-per-target` to get
several independent combinations of replacements.

**A per-position loop is the alternative when you want each position's best
replacement in isolation** — one run per position, each omitting only that
one. That is the form to use when the question is "what is the best
substitution at each site", rather than "give me a mutated sequence":

```bash
for i in "${!POS[@]}"; do
  cat > omit_${POS[$i]}.jsonl <<EOF
{"backbone": {"A": [[[${IDX[$i]}], ["${WT[$i]}"]]]}}
EOF
  /path/to/env/bin/protein_mpnn_run \
    --pdb-path backbone.pdb --out-folder scan_${POS[$i]} \
    --model-name v_48_020 --num-seq-per-target 4 \
    --sampling-temp 0.1 --seed 42 --batch-size 1 \
    --omit-AA-jsonl omit_${POS[$i]}.jsonl
done
```

> **Use the executable's full path.** `protein_mpnn_run` is not on `PATH`
> unless the conda env is activated, and a loop that redirects to a log file
> swallows the `command not found` — every iteration prints "done" and
> writes nothing. Check that the first run produced a `.fa` before trusting
> the loop:
>
> ```bash
> ls scan_${POS[0]}/seqs/*.fa || echo "LOOP PRODUCED NOTHING"
> ```

Read each position's replacement out of its own FASTA at the same 0-based
index, and take the lowest-score sample. A position whose best replacement
is still its wild-type amino acid means the omit did not land — see the
1-based note above.

### Using residues the user selected in the viewer

If the user selected residues and asked to design around / keep them, read
the selection from the request's `[当前结构：...]` block and turn it into a
fixed-positions JSONL:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/structure-viewer/scripts/read_selection.py \
  --chain A --format mpnn --pdb-name backbone.pdb
```

Then pass `--fixed-positions-jsonl fixed.jsonl`.

**If the request has no `[当前结构：...]` block**, the user has not selected
anything — ask them to click residues in the viewer rather than guessing which
positions matter.

> **`--format mpnn` prints PDB numbers, which are the wrong numbering for
> ProteinMPNN.** Use `--format mpnn-index` or `--format mpnn-omit` with
> `--pdb-path` instead — they convert to ProteinMPNN's indices and verify
> each one against the wild type in the file:
>
> ```bash
> # indices for --fixed-positions-jsonl
> read_selection.py --chain A --format mpnn-index --pdb-path backbone.pdb
> # -> {"backbone": [10, 11, 12, ...]}
>
> # one omit entry per position, for a saturation scan
> read_selection.py --chain A --format mpnn-omit --pdb-path backbone.pdb
> # -> {"backbone": {"A": [[[10], ["V"]], [[11], ["E"]], ...]}}
> ```
>
> The conversion reads the file's lowest residue number and counts from
> there, so a PDB numbered from -1 puts PDB 8 at index 10. It warns when the
> viewer's residue name disagrees with the file's — usually because the
> selection came from a different structure, such as a mutant published
> earlier.

## Step 3 — Report progress

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "设计氨基酸序列" --index 1 --total 2 \
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
  --status --state done --step "设计氨基酸序列" --index 2 --total 2 \
  --skill sequence-design --message "8 sequences designed"
```

### Highlight the positions you changed

If you constrained specific positions — omitted the wild type to force a
mutation, or fixed residues to hold them — pass them with `--highlight` so
the viewer selects them on arrival:

```bash
  --highlight "A:385,A:386,A:387,A:388"
```

The viewer draws those residues with their side chains visible, in the
selection colour, on top of the normal backbone style. Without it the user
has to find the changed positions by reading the sequence panel against
the wild type.

Build the list from the **PDB residue numbers of the file you are
publishing**, not from the ProteinMPNN indices you used in
`--omit-AA-jsonl` — the two are different numberings (see the index note
above).

Adjust `--roles` to the actual chain IDs in your PDB.

### Writing `--notes`

See `structure-viewer` for how `--notes` is shown. What to put in it:

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
| `protein_mpnn_run: command not found` | Env not activated | Use the full path `<env>/bin/protein_mpnn_run` |
| Loop prints "done" but no `.fa` appears | The line above, hidden by `> log 2>&1` | Check `ls scan_*/seqs/*.fa` after the first iteration |
| A scanned position still shows its wild type | `--omit-AA-jsonl` index off by one | Positions there are 1-based; add 1 to the `parse_PDB` index |
| Every position reports the same score | Reading the wrong index out of the FASTA | Re-derive the index and assert it matches the wild type |

## References

- [ProteinMPNN GitHub](https://github.com/dauparas/ProteinMPNN)
- Dauparas et al., *Science* 378, 49–56 (2022)
