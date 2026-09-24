---
name: mmseqs-deploy
description: '**DEPLOYMENT SKILL** — Install MMseqs2 (GPU-accelerated protein sequence search) on any machine, verify the GPU path works, and build searchable databases. USE FOR: setting up MMseqs2 from scratch; installing the GPU build on ARM64; fixing "not a valid GPU database"; checking whether the GPU is actually being used; building a sequence database for searching. DO NOT USE FOR: running a search (use sequence-search); structure-based comparison (use structure-comparison, which uses Foldseek); installing Foldseek (use foldseek-deploy). INVOKES: shell tools (curl/tar). Needs a CUDA GPU for the accelerated path; the CPU path works everywhere.'
---

# MMseqs2 Deployment

MMseqs2 searches protein sequences orders of magnitude faster than BLAST.
The GPU build accelerates the prefilter — the stage that decides which
candidate pairs are worth aligning, which is where most of the work is.

This skill covers **installation and verification only**. To run a search,
see `sequence-search`.

## When to Use

- "Install MMseqs2"
- "mmseqs: command not found"
- "Is the GPU actually being used?"
- "Database is not a valid GPU database"
- "Build a searchable database from these sequences"

## Requirements

| Resource | Need |
|----------|------|
| GPU | Optional. CUDA-capable, for the accelerated prefilter |
| Disk | ~200 MB for the binary; databases scale with input |
| RAM | 2 GB+ |
| OS | Linux, macOS |

## Installation

### Pick the archive

| Archive | Architecture | GPU |
|---------|--------------|-----|
| `mmseqs-linux-gpu-arm64.tar.gz` | ARM64 (DGX Spark, Apple Silicon, Graviton) | yes |
| `mmseqs-linux-gpu.tar.gz` | x86-64 | yes |
| `mmseqs-linux-avx2.tar.gz` | x86-64 with AVX2 | no |
| `mmseqs-osx-universal.tar.gz` | macOS | no |

**The GPU archives are ~150 MB and the AVX2 one is 17 MB.** The GPU build
statically links CUDA, which is most of the difference. Take the GPU archive
unless there is no CUDA GPU.

```bash
mkdir -p ~/tools && cd ~/tools
VERSION=18-8cc5c
curl -sL -o mmseqs.tar.gz \
  "https://github.com/soedinglab/MMseqs2/releases/download/${VERSION}/mmseqs-linux-gpu-arm64.tar.gz"
tar xzf mmseqs.tar.gz && rm mmseqs.tar.gz
```

Check the [releases page](https://github.com/soedinglab/MMseqs2/releases) for
the current tag.

### Add to PATH

```bash
echo 'export PATH="$HOME/tools/mmseqs/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Verify

```bash
mmseqs version
```

Prints a commit hash. `mmseqs --help` should list `gpuserver` among the
workflows — that subcommand only exists in the GPU build.

## Verifying the GPU path

`--gpu 1` being accepted does not mean the GPU is used. Two things to check.

### 1. The database must be GPU-compatible

```
Error: Database tDB is not a valid GPU database
```

The GPU prefilter reads a different layout. Build the target database with
`--createdb-mode 2`:

```bash
mmseqs createdb targets.fasta tDB --createdb-mode 2
```

The query database does not need it — only the target.

### 2. Confirm the GPU is actually working

Watch utilisation while a search runs:

```bash
nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader -l 1 &
mmseqs search qDB tDB res tmp --gpu 1 --threads 20
kill %1
```

A working GPU path shows sustained high utilisation. **0% means the GPU is
not being used** — the run is falling back to the CPU, and the wall time
will be worse than CPU-only because of the transfer overhead.

### Expected speedup

Measured on DGX Spark (GB10, unified memory), 2000 queries against 200,000
targets:

| Path | Time |
|------|------|
| CPU, 20 threads | 23.6 s |
| GPU | 12.8 s |

**1.8×, not the 10–100× the paper reports.** GB10 shares memory bandwidth
between CPU and GPU, so the prefilter is bandwidth-bound rather than
compute-bound. The speedup is real but modest; on a discrete GPU it is far
larger.

Small searches show no benefit at all — 200 queries against 5,000 targets
runs in 1.5 s either way, because the fixed costs dominate. Use `--gpu 1`
for large databases and do not bother for small ones.

### First run is slow

The first GPU run on a machine can take minutes longer than subsequent
ones — CUDA JIT-compiles and caches kernels. A 1.5 s search taking 124 s
once, then 1.5 s every time after, is this. Do not judge performance from
the first run.

## Building databases

```bash
# From FASTA
mmseqs createdb targets.fasta tDB

# GPU-compatible, for --gpu 1
mmseqs createdb targets.fasta tDB --createdb-mode 2
```

| Mode | Use |
|------|-----|
| 0 (default) | CPU searches |
| 2 | GPU searches |

A database is a directory of binary files plus an index. It is not portable
between MMseqs2 versions — rebuild rather than upgrade in place.

## Downloading ready-made databases

Optional. Without them, searches are limited to local files.

```bash
mmseqs databases UniProtKB Swiss-Prot swissprot db_tmp
```

| Database | Download | On disk | Notes |
|----------|----------|---------|-------|
| `UniProtKB/Swiss-Prot` | ~90 MB | ~250 MB | Curated; the practical default |
| `UniProtKB/UniRef90` | ~8 GB | ~20 GB | Clustered at 90% identity |
| `PDB` | ~30 GB | ~80 GB | Sequences of experimental structures |
| `UniProtKB/UniRef50` | ~25 GB | ~60 GB | Clustered at 50% |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Database X is not a valid GPU database` | Target built without GPU mode | `createdb --createdb-mode 2` |
| GPU utilisation 0% during a search | GPU path not engaged | Check the database mode; confirm `--gpu 1` reached the prefilter |
| GPU run slower than CPU | Small database, or first-run JIT | Use CPU for small searches; re-run to skip the JIT |
| `cannot execute binary file` | Wrong architecture | Take the arm64 archive on DGX Spark |
| `mmseqs: command not found` | Not on PATH | `export PATH="$HOME/tools/mmseqs/bin:$PATH"` |
| Database errors after an upgrade | Databases are version-specific | Rebuild with `createdb` |
| Download stalls | Proxy blocking GitHub | `export https_proxy=http://127.0.0.1:6450` |

## References

- [MMseqs2 GitHub](https://github.com/soedinglab/MMseqs2)
- Steinegger & Söding, *Nat. Biotechnol.* 35, 1026–1028 (2017), doi:10.1038/nbt.3989
- Kallenborn et al., *bioRxiv* (2024), doi:10.1101/2024.11.13.623350 — GPU-accelerated prefilter
