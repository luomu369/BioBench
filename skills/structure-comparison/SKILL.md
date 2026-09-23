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
| Database search errors on `pdb_ca` | Incomplete download | Re-extract `pdb_ca` and `pdb_seq_ca.1` from `dltmp/latest/pdb.tar.gz` |
| `Cannot open lookup file .../pdb_ca.lookup` | Missing `*.lookup` symlinks | `for f in pdb_ca pdb_h pdb_ss pdb_clu; do ln -sf pdb.lookup $f.lookup; done` |
| `No structures found in given input` | Passed the directory instead of the `pdb` prefix | `cd /home/asus_gx10/foldseek-db` and pass `pdb` |
| Database search finds nothing | Wrong path | Check `/home/asus_gx10/foldseek-db/pdb.lookup` exists |

## References

- [Foldseek GitHub](https://github.com/steineggerlab/foldseek)
- van Kempen et al., *Nature Biotechnology* (2023), doi:10.1038/s41587-023-01773-0
