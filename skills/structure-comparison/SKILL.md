---
name: structure-comparison
description: '**WORKFLOW SKILL** — Compare protein structures with Foldseek: search a query against a set of targets, cluster structures by fold, or find reciprocal best hits. USE FOR: finding structurally similar proteins; checking whether a designed binder resembles a known fold; clustering a set of designs; identifying what a novel structure looks like; comparing a design against its template. DO NOT USE FOR: installing Foldseek (use foldseek-deploy); generating structures (use binder-design); publishing results (use structure-viewer). Requires Foldseek installed — run foldseek-deploy first.'
---

# Structure Comparison

Compare protein structures with Foldseek and report what matches.

**Prerequisite:** Foldseek installed. If not, follow `foldseek-deploy` first.

## When to Use

- "Does my design look like any known protein?"
- "Find structures similar to this one"
- "Cluster these designs by fold"
- "Compare this structure against that one"
- "What is this novel structure?"

## Confirm before running

Searches are fast (seconds for local files), so confirm intent:

```
Query:      nk1r_binder_0.cif (2 chains)
Targets:    7XWO.cif
Mode:       easy-search
Sensitivity: default

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Mode** | From the request | `search` |
| **Targets** | Local files, or a directory of structures | required |
| **Sensitivity** | `-s` 0–9; higher finds weaker matches | 7.5 |

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Report progress                    (structure-viewer publisher)
2. Run the comparison
3. Interpret and report the results
```

## Step 1 — Report progress

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step search --index 1 --total 2 \
  --skill structure-comparison --message "正在比对结构"
```

## Step 2 — Run

```bash
FS=/home/asus_gx10/tools/foldseek/bin/foldseek
```

### Search one query against targets

```bash
$FS easy-search query.cif target.cif result.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

`target.cif` can also be a **directory** — every structure inside is searched.

### Reciprocal best hit

Confirms the match is mutual, which is stronger evidence than a one-way hit:

```bash
$FS easy-rbh query.cif target.cif rbh.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

### Cluster a set of structures

Groups structures by fold — useful for seeing whether a batch of designs
converged on the same shape:

```bash
$FS easy-cluster designs/ cluster_out tmp
```

### Multimer search

For complexes (e.g. a designed binder bound to its target):

```bash
$FS easy-multimersearch complex.cif target.cif multi.m8 tmp
```

### Modes

| Mode | Use |
|------|-----|
| `easy-search` | One query against one or many targets |
| `easy-rbh` | Reciprocal best hit — mutual best match |
| `easy-cluster` | Group structures by fold |
| `easy-multimersearch` | Complex-level search |

### Useful flags

| Flag | Meaning |
|------|---------|
| `-s 7.5` | Sensitivity; raise to 9 for weak/remote matches |
| `--min-seq-id 0.3` | Filter by sequence identity |
| `-e 1e-3` | E-value cutoff |
| `--format-output ...` | Choose output columns |
| `--threads 8` | Parallelism |

## Step 3 — Interpret the results

Output is BLAST-like tab-separated (`.m8`):

```
query_B  target_B  32.7  460  1.07E-16  692
query_A  target_A  18.7   16  7.23E+00   -1
```

| Column | Meaning |
|--------|---------|
| `query` / `target` | Chain identifiers |
| `pident` | Sequence identity over the aligned region (%) |
| `alnlen` | Aligned residues |
| `evalue` | Expectation value; **lower is better** |
| `bits` | Bit score; **higher is better** |

### Reading the result

- **High bits + low e-value + long alnlen** → real structural match
- **Short alnlen (< 20) with a poor e-value** → noise, ignore
- `pident` is *sequence* identity over the structural alignment, so a low
  value with a strong e-value means the fold is shared but the sequence diverged

Report in biological terms:

```
NK1R binder (chain B) matches 7XWO chain B:
  - 460 residues aligned, 32.7% sequence identity
  - e-value 1.1e-16, bit score 692
  → Same fold (class A GPCR, 7 transmembrane helices)

The designed binder (chain A) has no significant match —
it is a novel fold, as expected for a de novo design.
```

Mark the task done:

```bash
python3 $PUB --status --state done --step search --index 2 --total 2 \
  --skill structure-comparison --message "比对完成"
```

## Output format

One line per hit, tab-separated, best hits first. Columns are controlled by
`--format-output`; the defaults include `query,target,fident,alnlen,evalue,bits`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `foldseek: command not found` | Not on PATH | Use the absolute path, or install via `foldseek-deploy` |
| Empty result file | Query too short, or no real match | Check `alnlen`; try `-s 9` for higher sensitivity |
| `cannot execute binary file` | Wrong architecture | Install the arm64 build |
| Very slow on many targets | Single-threaded | Add `--threads 8` |
| Hits look like noise | Default sensitivity too high | Raise `-e` (stricter e-value) |

## References

- [Foldseek GitHub](https://github.com/steineggerlab/foldseek)
- van Kempen et al., *Nature Biotechnology* (2023), doi:10.1038/s41587-023-01773-0
