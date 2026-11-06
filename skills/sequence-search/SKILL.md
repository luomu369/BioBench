---
name: sequence-search
description: '**WORKFLOW SKILL** — Search protein sequences with MMseqs2: find homologues of a query, identify what a sequence is, search a designed sequence against known proteins, cluster a set of sequences, or search a local database. USE FOR: finding sequences similar to one you have; identifying an unknown sequence; checking whether a designed binder resembles a known family; searching a sequence against UniProt or the PDB; clustering sequences by identity; finding the family a novel sequence belongs to. DO NOT USE FOR: installing MMseqs2 (use mmseqs-deploy); structure-based comparison (use structure-comparison, which uses Foldseek); designing a sequence (use sequence-design); short motif search (MMseqs2 needs ~30+ residues to be sensitive). Requires MMseqs2 installed — run mmseqs-deploy first.'
---

# Sequence Search

Search protein sequences with MMseqs2 and report what matches.

**Prerequisite:** MMseqs2 installed. If not, follow `mmseqs-deploy` first.

## When to Use

- "What is this sequence?"
- "Find sequences similar to this one"
- "Does my designed binder look like a known family?"
- "Search this against UniProt"
- "Cluster these sequences"
- "What family does this belong to?"

## Confirm before running

Searches are fast (seconds locally, longer against a downloaded database), so
confirm intent:

```
Query:      binder_0.fasta (1 sequence, 88 aa)
Targets:    local directory of designs
Sensitivity: default
GPU:        yes (target db is GPU-compatible)

Proceed?
```

| Parameter | How to decide | Default |
|-----------|---------------|---------|
| **Targets** | Local files, a directory, or a downloaded database | required |
| **Sensitivity** | `-s` 0–7; higher finds weaker matches | 5.25 |
| **GPU** | Only if the target db was built with `--createdb-mode 2` | off |

Pick the target from what the user is asking:

| Question | Target |
|----------|--------|
| "What is this sequence?" | `~/mmseqs-db/pdb` — 1.5 M PDB sequences, already built |
| "Is this the same as that one?" | The specific local file |
| "Did these designs converge?" | The directory of designs |
| "Does my binder resemble a known family?" | `~/mmseqs-db/pdb`, or Swiss-Prot if downloaded |

> **The PDB sequence database is already on disk.** It was converted from
> the Foldseek structure database, not downloaded — see `mmseqs-deploy` for
> how, and for what to do if it is missing.

> **Short sequences need a different tool.** MMseqs2's sensitivity falls off
> below ~30 residues, and a 10-residue motif will match nothing meaningful.
> For a short stretch, use a sequence motif search instead.

## Workflow

```
0. Confirm parameters with the user   ← do not skip
1. Check the target database exists
2. Build the query database
3. Run the search
4. Interpret and report the hits
```

## Step 1 — Check the database

```bash
ls ~/mmseqs-db/pdb/pdb.dbtype 2>/dev/null && echo "PDB sequence db present"
```

If it is missing, build it from the Foldseek structure database — twelve
seconds, no download. See `mmseqs-deploy`.

## Step 2 — Report progress

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

python3 $PUB --status --state running --step "搜索序列数据库" --index 1 --total 2 \
  --skill sequence-search --message "正在比对序列"
```

## Step 3 — Build the databases

MMseqs2 works on its own binary database format, not FASTA:

```bash
TOOLS="${TOOLS:-$HOME/tools}"
MM=$TOOLS/mmseqs/bin/mmseqs

$MM createdb targets.fasta tDB
$MM createdb query.fasta qDB
```

**Add `--createdb-mode 2` to the target if you want the GPU path.** Without
it the search fails with `Database tDB is not a valid GPU database`. The
query database does not need it.

## Step 4 — Run

```bash
TOOLS="${TOOLS:-$HOME/tools}"
MM=$TOOLS/mmseqs/bin/mmseqs

$MM search qDB tDB result tmp --threads 20 --gpu 1
```

`target` can also be a **directory** of FASTA files — every sequence inside
is searched.

The result is a binary database. Turn it into a table with `convertalis`:

```bash
$MM convertalis qDB tDB result hits.tsv \
  --format-output "query,target,pident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,evalue,bits"
```

> **`--format-output` belongs to `convertalis`, not `search`.** Passing it to
> `search` fails with `Unrecognized parameter` — the two subcommands take
> different option sets, and only `convertalis` writes text.

### Reading the output

One line per hit, tab-separated, best first:

```
q0	t1234	52.6	300	142	0	1	300	1	300	5.07E-90	299
```

| Column | Meaning |
|--------|---------|
| `pident` | Sequence identity, **percent** (0–100, not 0–1) |
| `alnlen` | Aligned length |
| `evalue` | Expectation value; lower is better |
| `bits` | Bit score; higher is better |

### Interpreting

| Evidence | Meaning |
|----------|---------|
| pident > 90, evalue < 1e-50 | Same protein, or a very close homologue |
| pident 30–50, evalue < 1e-20 | Same family, diverged sequence |
| pident < 20 but strong evalue | Shared fold or domain only |
| Short alignment, high identity | A conserved domain, not a whole-protein match |
| No hits above 1e-3 | Novel, or the query is too short |

Report in biological terms:

```
Searched the designed binder against the PDB:

  Top hit:  7xwq chain B — 62% identity, e-value 3e-88
  Next:     6hll chain A — 41% identity, e-value 2e-61
  14 more hits above 1e-20

The design converged on the neurokinin-1 receptor family, as expected for
a binder raised against NK1R. The 62% hit is the target itself.
```

### When the query is a de novo design

**Zero hits is the expected result, and it is good news.** A designed
sequence has no evolutionary history, so there is nothing in the PDB for it
to match. Reporting "no homologues found" as a failure misreads the search.

What the search is actually checking is whether the design accidentally
reproduced something already known:

- **0 hits** — the sequence is novel. Say so plainly, and note the database
  size so the number means something ("0 hits against 1.56 M PDB
  sequences").
- **A hit at high identity** — the design converged on a known fold. That is
  worth reporting, but it is not a problem; it usually means the target
  family has a well-explored binding mode.
- **A hit at low identity but high coverage** — possible remote homology.
  Worth a look before dismissing.

Confirm a null result before reporting it. A 12-residue query against a
sensitivity of 7.5 can come back empty because the query is short, not
because it is novel. Re-run with a looser threshold and check it is still
empty:

```bash
$MM search qDB $DB resultDB2 tmp2 --gpu 1 -s 4.0 -e 100 --max-seqs 100 --cov-mode 0
$MM convertalis qDB $DB resultDB2 hits.tsv --format-output "query,target,pident,alnlen,evalue,bits"
```

Still empty at e-value 100 is a real null. Report both runs.

### Clustering a set of sequences

Answers "did these designs converge on one fold":

```bash
$MM cluster qDB clu tmp --threads 20
$MM createtsv qDB clu clusters.tsv
```

Each line of `clusters.tsv` is one cluster: a representative followed by its
members. Many small clusters mean the designs diverged; one large cluster
means they converged.

## Step 5 — Publish

If the search produced a structure worth showing — a hit fetched from the
PDB, say — publish it. The search itself produces no structure, so usually
there is nothing to publish beyond the status:

```bash
python3 $PUB --status --state done --step "搜索序列数据库" --index 2 --total 2 \
  --skill sequence-search --message "Top hit: 7xwq B, 62% identity, e-value 3e-88"
```

## GPU notes

The GPU accelerates the prefilter only — the stage that picks candidate
pairs. Alignment and everything after it runs on the CPU regardless.

Measured on local GPU workstation, 2000 queries against 200,000 targets: 23.6 s on 20
CPU threads, 12.8 s on the GPU. **1.8×, not the 10–100× the paper reports**,
because ARM64 GPU shares memory bandwidth between CPU and GPU and the prefilter
is bandwidth-bound. On a discrete GPU the speedup is far larger.

Small searches show no benefit — 200 queries against 5,000 targets runs in
1.5 s either way. Do not bother with `--gpu` below that scale.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Database X is not a valid GPU database` | Target built without GPU mode | `createdb --createdb-mode 2` |
| GPU utilisation 0% | GPU path not engaged | Check the database mode |
| `No datafile could be found for <db>` | The database's main data file is missing — only `.index`/`.lookup`/`.dbtype` survived, usually from an interrupted `createdb` | Rebuild from the source FASTA (below) |
| No hits at all | Query too short, or wrong database | Check the query length; try `-s 7` |
| Hits look like noise | Sensitivity too high | Raise `-e` (stricter e-value) |
| Search very slow | Single-threaded | Pass `--threads 20` |
| Database errors after an upgrade | Databases are version-specific | Rebuild with `createdb` |

### Rebuilding a truncated database

`No datafile could be found` means the `.dbtype` is present but the main
data file is not — the build was killed partway. If the source FASTA is
still there, rebuild rather than re-downloading:

```bash
cd ~/mmseqs-db
$MM createdb pdb_seqs.fasta pdb --createdb-mode 2
```

`--createdb-mode 2` writes the database in the layout the GPU prefilter
needs. Without it the search falls back to CPU with
`Database X is not a valid GPU database`.

## References

- [MMseqs2 GitHub](https://github.com/soedinglab/MMseqs2)
- Steinegger & Söding, *Nat. Biotechnol.* 35, 1026–1028 (2017), doi:10.1038/nbt.3989
