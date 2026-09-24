---
name: structure-comparison
description: '**WORKFLOW SKILL** — Compare protein structures with Foldseek: search a query against a set of targets, cluster structures by fold, find reciprocal best hits, search the PDB database, or superimpose a hit onto the query. USE FOR: finding structurally similar proteins; checking whether a designed binder resembles a known fold; clustering a set of designs; identifying what a novel structure looks like; comparing a design against its template; finding what known protein a selected stretch of residues resembles; overlaying a hit and a query in one view. DO NOT USE FOR: installing Foldseek (use foldseek-deploy); generating structures (use binder-design); publishing results (use structure-viewer); sequence-level motif search (Foldseek is structural — short motifs need a sequence search). Requires Foldseek installed — run foldseek-deploy first.'
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
- "What known protein do these residues I selected look like?"
- "Overlay this hit on my structure so I can see them together"

## Confirm before running

Searches are fast (seconds for local files, a few seconds against the PDB
database), so confirm intent:

```
Query:      nk1r_binder_0.cif (2 chains)
Targets:    PDB database (/home/asus_gx10/foldseek-db)
Mode:       easy-search
Sensitivity: default

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Mode** | From the request | `search` |
| **Targets** | Local files, a directory, or the PDB database | required |
| **Sensitivity** | `-s` 0–9; higher finds weaker matches | 7.5 |

Pick the target from what the user is actually asking:

| Question | Target |
|----------|--------|
| "Does my design look like any known protein?" | PDB database |
| "Is this the same as that structure?" | The specific local file |
| "Did these designs converge on one fold?" | The directory of designs |
| "What do the residues I selected look like?" | Extracted fragment — check its length first |

> **If the user selected residues in the viewer, check the length before
> promising anything.** Foldseek cannot search a short motif; see
> [Search the residues the user selected](#search-the-residues-the-user-selected).

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Report progress                    (structure-viewer publisher)
2. Run the comparison
3. Interpret and report the results
4. Publish the hit, and superimpose it if the user wants to compare
```

## Step 1 — Report progress

```bash
PUB=/home/asus_gx10/biobench/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "搜索结构数据库" --index 1 --total 3 \
  --skill structure-comparison --message "正在比对结构"
```

The viewer's status bar shows this, so keep it moving — a search that
takes a minute with a stale "running 1/3" looks hung.

## Step 2 — Run

```bash
FS=/home/asus_gx10/tools/foldseek/bin/foldseek
DBDIR=/home/asus_gx10/foldseek-db
```

### Search one query against targets

```bash
$FS easy-search query.cif target.cif result.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

`target.cif` can also be a **directory** — every structure inside is searched.

### Search against the PDB database

Answers "what known protein does this look like?" — the question a local
file comparison cannot:

```bash
cd $DBDIR
$FS easy-search /path/to/query.cif pdb result.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

Two things are easy to get wrong here:

- **Pass the prefix `pdb`, not the directory.** The database is a flat set
  of files sharing a `pdb` prefix (`pdb`, `pdb_ca`, `pdb_h`, …); `pdb`
  itself is the main data file, not a folder. Giving the directory path
  makes foldseek treat it as a folder of structures and fail with
  "No structures found in given input".
- **Run from inside `$DBDIR`**, or pass an absolute prefix
  (`/home/asus_gx10/foldseek-db/pdb`).

The database is 6.0 GB on disk, PDB data as of 2025-01-01. A single query
takes a couple of seconds.

Target names come back as PDB entries, e.g. `7xwr-assembly1_A` — strip the
`-assembly1_A` suffix to get the four-character PDB ID for RCSB lookups.

> **`pdb100` means 100% sequence identity clustering.** Only chains with
> identical sequences were merged, so no distinct sequence is lost: 1.56 M
> chains collapse to 324 k representatives (4.8:1). This is *not* a
> non-redundant set — remote homologues are all still there.

If the database is missing, download it with:

```bash
mkdir -p /home/asus_gx10/foldseek-db
$FS databases PDB /home/asus_gx10/foldseek-db /home/asus_gx10/foldseek-db/dltmp
```

> **Check the download before deleting `dltmp`.** `foldseek databases` has
> been observed to unpack incompletely. Two separate things go missing:
>
> 1. the large data files `pdb_ca` and `pdb_seq_ca.1`
> 2. the `*.lookup` symlinks for `pdb_ca`, `pdb_h`, `pdb_ss`, `pdb_clu`
>
> Symptom of (1) is a mid-search failure; symptom of (2) is
> `Cannot open lookup file .../pdb_ca.lookup!`. Repair with:
>
> ```bash
> cd /home/asus_gx10/foldseek-db
> tar xzf dltmp/latest/pdb.tar.gz pdb_ca pdb_seq_ca.1   # if missing
> ln -sf pdb_ca pdb_seq_ca.0
> for f in pdb_ca pdb_h pdb_ss pdb_clu; do ln -sf pdb.lookup $f.lookup; done
> ```
>
> Then `rm -rf dltmp` to reclaim the ~2.3 GB cached tarball.
>
> `skills/foldseek-deploy/scripts/preflight.py` checks all of this.

### Search the residues the user selected

The viewer writes the user's residue clicks to `selection.json`. To ask
"what known protein looks like this bit?", extract those residues into a
structure of their own and search with it:

```bash
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python
EXT=/home/asus_gx10/biobench/skills/structure-comparison/scripts/extract_selection.py

$PY $EXT --out fragment.cif --context 2
cd /home/asus_gx10/foldseek-db
$FS easy-search /path/to/fragment.cif pdb result.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

`--context N` pulls in N residues either side of each selected stretch,
which usually helps — see the length limit below.

> **Foldseek needs ~60 residues to find anything.** It has no
> "search only this stretch" option, so a selection has to be written out
> as a standalone query, and a short query yields nothing. Measured
> against the PDB database at `-s 9.5`:
>
> | Query length | Hits |
> |---|---|
> | 10 residues | 0 |
> | 20 residues | 5 |
> | 40 residues | 5 |
> | 60 residues | 840 |
> | 219 residues | 1000 |
>
> Below ~60 residues the 3Di structural alphabet produces too few k-mer
> seeds to survive prefiltering. This is not tunable — raising sensitivity
> does not help.
>
> So before promising a result, check how long the selection is:
>
> ```bash
> python3 /home/asus_gx10/biobench/skills/structure-viewer/scripts/read_selection.py
> ```
>
> - **Selection spans a long chain** → search that whole chain, then filter
>   hits by whether they cover the selected region (`--cov-mode 2`).
> - **Selection is a short motif** (< ~60 residues) → Foldseek is the wrong
>   tool. Say so and suggest a *sequence* search instead, which is far more
>   sensitive on short motifs.
> - **Selection is a short chain in a complex** → search the whole complex
>   with `easy-multimersearch` and report what the complex resembles.

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

Against the PDB database, name the entries you found:

```
Searched the NK1R binder against the PDB (324 k representatives):

  chain B → 7xwr-assembly1_A   100.0% id, 219 res, e-value 4.4e-30
  chain B → 7xvy-assembly1_A    99.5% id, 219 res, e-value 4.2e-30
  chain B → 1u3r-assembly1_A    93.8% id, 229 res, e-value 3.7e-28

  → The target is a class A GPCR; 7xwr is the same receptor in a
    different conformational state, 1u3r a close homologue.
```

## Step 4 — Publish the hit and the report

A search result is only useful if the user can see it. Download the top
hit, publish it to the viewer, and put the numbers in the metrics panel.

```bash
# 1. Fetch the top hit (strip the -assembly1_A suffix foldseek appends)
HIT=7xwr
curl -s "https://files.rcsb.org/download/${HIT}.cif" -o hit_${HIT}.cif

# 2. Publish it, with the search numbers as metrics
python3 $PUB --structure hit_${HIT}.cif \
  --name "hit_${HIT}" --source foldseek \
  --roles "A=hit" \
  --metrics '{"pident": {"label": "序列一致性", "value": 100.0, "hint": "%，越高越相似"}, "alnlen": {"label": "比对长度", "value": 225, "hint": "残基"}, "evalue": {"label": "期望值", "value": 1.4e-30, "hint": "越小越可信"}, "bits": {"label": "比特分数", "value": 1516, "hint": "越大越好"}, "hits": {"label": "命中数", "value": 1000}, "unique_pdb": {"label": "独立条目", "value": 616}}' \
  --notes "序列一致性 100%（比对区域内序列相同的比例），比对 225 个残基，
e-value 1.4e-30（越小越可信，此值几乎不可能是偶然），比特分数 1516（越大越好）。
共 1000 个命中、616 个独立 PDB 条目。
建议：该折叠很常见，取 Top 20 按 e-value 排序后去重分析。"

# 3. Close out the status bar
python3 $PUB --status --state done --step search --index 3 --total 3 \
  --skill structure-comparison --message "比对完成：1000 命中，最佳 7xwr (100%)"
```

The viewer then shows the hit structure with the scores in the Metrics
panel, and the status bar reads done.

### Writing `--notes`

See `structure-viewer` for how `--notes` is shown. What to put in it:

| Result | Usually means |
|--------|---------------|
| e-value > 1e-3 | Not significant; raise `-s 9.5` or try a sequence search |
| e-value < 1e-30, pident > 95% | Same protein; look at conformational differences |
| pident < 30% but strong e-value | Shared fold, diverged sequence — report the fold |
| hits ≥ 1000 | Very common fold; take the top 20 and deduplicate |
| unique_pdb small (< 10) | Rare fold — worth a closer look |
| CA RMSD > 10 Å (after superposition) | Domain-level match only; check the aligned region |

> **Publishing replaces what is on screen.** The viewer holds one
> structure at a time, so the hit replaces the query. If the user wants to
> compare the two, say so and publish the query back afterwards — or
> download both and publish a combined file.

### Reporting several hits

For a shortlist, publish the best hit and list the rest in the message:

```bash
python3 $PUB --status --state done --step "搜索结构数据库" --index 3 --total 3 \
  --skill structure-comparison \
  --message "Top 5: 7xwq 100% | 7xwr 99.5% | 4j26 97.3% | 1u9e 97.7% | 1u3r 98.2%"
```

## Step 5 — Superimpose the hit onto the query

Publishing a hit replaces the query on screen, so the two can only be
compared one at a time. To see them together, move the hit into the
query's coordinate system and publish the pair:

```bash
PY=/home/asus_gx10/miniconda3/envs/docking/bin/python
SUP=/home/asus_gx10/biobench/skills/structure-comparison/scripts/superimpose.py

$PY $SUP --query 7XWQ.cif --target hit_4j26.cif \
  --query-chain B --out superposed.cif
```

```
wrote superposed.cif
  alignment: 97.3% id, 225 res, e-value 1.30e-31
  CA RMSD:   7.91 A
  query  chains (unchanged IDs): ['A', 'B', 'C', 'D']
  target chains (prefixed 'h'):  {'A': 'hA', 'I': 'hI', 'B': 'hB', 'J': 'hJ'}
  → in the viewer, plain IDs are 7XWQ, 'h' IDs are hit_4j26
```

The script runs Foldseek itself to get the rotation matrix and translation
vector, applies them to every target atom, and reports the CA RMSD as a
sanity check on the fit.

### Telling the two structures apart

The query keeps its chain IDs; the target's get an `h` prefix. So in the
viewer's chain list, `A`–`D` are the query and `hA`–`hJ` are the hit —
no `--roles` needed to know which is which.

> **Write `.cif`, not `.pdb`.** PDB chain IDs are a single character, so
> `hA` is silently truncated to `A` and the two structures become
> indistinguishable again. mmCIF has no such limit.

Publish with roles that spell it out anyway, since the Metrics panel and
chain dropdown both show them:

```bash
python3 $PUB --structure superposed.cif \
  --name "7XWQ_vs_4j26" --source foldseek \
  --roles "B=query_7XWQ,hA=hit_4j26_A,hB=hit_4j26_B,hI=peptide_I,hJ=peptide_J" \
  --metrics '{"pident":97.3,"ca_rmsd_A":7.91}' \
  --notes "4j26 onto 7XWQ chain B. Plain IDs = query, h-prefixed = hit."
```

> **`--query-chain` matters for multi-chain queries.** Without it the
> script aligns on the first chain, which may not be the one the user
> cares about. Pass the chain that was searched.

### Reading a superposition

| Observation | Meaning |
|---|---|
| Low CA RMSD (< 3 Å) over the core | Same fold, same conformation |
| Moderate RMSD (3–10 Å) | Same fold, conformational differences — usually loops |
| High RMSD (> 10 Å) | Check the alignment; may be a domain-level match only |
| Hit-only chains far from the query | A binding partner the query does not have |

## Output format

One line per hit, tab-separated, best hits first. Columns are controlled by
`--format-output`; the defaults include `query,target,fident,alnlen,evalue,bits`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `foldseek: command not found` | Not on PATH | Use the absolute path, or install via `foldseek-deploy` |
| Empty result file | Query too short, or no real match | Check `alnlen`; try `-s 9` for higher sensitivity |
| Empty result on a short query (< 60 residues) | Foldseek's practical length floor | Not fixable — use a sequence search instead |
| `cannot execute binary file` | Wrong architecture | Install the arm64 build |
| Very slow on many targets | Single-threaded | Add `--threads 8` |
| Hits look like noise | Default sensitivity too high | Raise `-e` (stricter e-value) |
| Database search errors on `pdb_ca` | Incomplete download | Re-extract `pdb_ca` and `pdb_seq_ca.1` from `dltmp/latest/pdb.tar.gz` |
| `Cannot open lookup file .../pdb_ca.lookup` | Missing `*.lookup` symlinks | `for f in pdb_ca pdb_h pdb_ss pdb_clu; do ln -sf pdb.lookup $f.lookup; done` |
| `No structures found in given input` | Passed the directory instead of the `pdb` prefix | `cd /home/asus_gx10/foldseek-db` and pass `pdb` |
| Database search finds nothing | Wrong path | Check `/home/asus_gx10/foldseek-db/pdb.lookup` exists |

## References

- [Foldseek GitHub](https://github.com/steineggerlab/foldseek)
- van Kempen et al., *Nature Biotechnology* (2023), doi:10.1038/s41587-023-01773-0
