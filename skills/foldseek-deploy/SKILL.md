---
name: foldseek-deploy
description: '**DEPLOYMENT SKILL** — Install Foldseek (fast protein structure search and alignment) on any machine, optionally download the PDB database, and verify the installation. USE FOR: setting up Foldseek from scratch; upgrading an existing install; downloading or repairing the PDB structure database; checking that the binary runs; diagnosing architecture or library errors. DO NOT USE FOR: running a structure comparison (use structure-comparison); protein design (use binder-design); publishing results (use structure-viewer). INVOKES: shell tools (curl/tar), file system tools. No GPU, no Python, no conda — just a static binary.'
---

# Foldseek Deployment

Foldseek compares protein structures orders of magnitude faster than
sequence-based methods. This skill covers **installation only**.

To actually compare structures, see the `structure-comparison` skill.

## When to Use

- User wants to install Foldseek
- `foldseek: command not found` or a library error
- User asks whether Foldseek is ready to use

## Requirements

| Resource | Need |
|----------|------|
| GPU | none |
| Disk | ~30 MB |
| RAM | 2 GB+ (scales with database size) |
| OS | Linux, macOS, or Windows |

Foldseek is a **static binary** — no Python, no conda, no CUDA.

## Installation

### Download the release binary

Pick the archive matching your architecture:

| Archive | Architecture |
|---------|--------------|
| `foldseek-linux-arm64.tar.gz` | ARM64 (DGX Spark, Apple Silicon, Graviton) |
| `foldseek-linux-avx2.tar.gz` | x86-64 with AVX2 |
| `foldseek-linux-gpu.tar.gz` | x86-64 with GPU support |
| `foldseek-osx-universal.tar.gz` | macOS (Intel + Apple Silicon) |

```bash
mkdir -p ~/tools && cd ~/tools
VERSION=10-941cd33
curl -sL -o foldseek.tar.gz \
  "https://github.com/steineggerlab/foldseek/releases/download/${VERSION}/foldseek-linux-arm64.tar.gz"
tar xzf foldseek.tar.gz && rm foldseek.tar.gz
```

Check the [releases page](https://github.com/steineggerlab/foldseek/releases)
for the current version tag.

### Add to PATH

```bash
echo 'export PATH="$HOME/tools/foldseek/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Verify

```bash
foldseek version
```

## Downloading the PDB database

Optional but recommended — without it, comparisons are limited to local
files and cannot answer "what known protein does this look like?".

```bash
FS=~/tools/foldseek/bin/foldseek
mkdir -p ~/foldseek-db
$FS databases PDB ~/foldseek-db ~/foldseek-db/dltmp
```

| Database | Download | On disk | Notes |
|----------|----------|---------|-------|
| `PDB` | 2.3 GB | ~6 GB | Experimental structures; the practical default |
| `Alphafold/Swiss-Prot` | 1.5 GB | ~4 GB | Predicted structures, Swiss-Prot subset |
| `CATH50` | 1.0 GB | ~3 GB | CATH domain representatives |
| `Alphafold/UniProt50` | 122 GB | ~500 GB | Full predicted set; needs 151 GB RAM |

`PDB` is clustered at **100% sequence identity** — only chains with
identical sequences are merged, so no distinct sequence is lost (1.56 M
chains → 324 k representatives). It is *not* a non-redundant set.

### The download unpacks incompletely — always repair

`foldseek databases` has been observed to drop files silently. Two separate
failure modes, both of which break searches later:

1. the large data files `pdb_ca` and `pdb_seq_ca.1`
2. the `*.lookup` symlinks for `pdb_ca`, `pdb_h`, `pdb_ss`, `pdb_clu`

Symptom of (1) is a mid-search failure; symptom of (2) is
`Cannot open lookup file .../pdb_ca.lookup!`.

```bash
cd ~/foldseek-db
tar xzf dltmp/latest/pdb.tar.gz pdb_ca pdb_seq_ca.1   # if missing
ln -sf pdb_ca pdb_seq_ca.0
for f in pdb_ca pdb_h pdb_ss pdb_clu; do ln -sf pdb.lookup $f.lookup; done
rm -rf dltmp        # reclaims ~2.3 GB of cached tarball
```

Then verify:

```bash
python3 /home/asus_gx10/biobench/skills/foldseek-deploy/scripts/preflight.py
```

### Using the database

The database is a **flat set of files sharing a `pdb` prefix** — `pdb`
itself is the main data file, not a directory. Pass the prefix, from inside
the database directory:

```bash
cd ~/foldseek-db
$FS easy-search query.cif pdb result.m8 tmp \
  --format-output "query,target,pident,alnlen,evalue,bits"
```

Passing the directory path instead makes foldseek treat it as a folder of
structures and fail with `No structures found in given input`.

## Verifying an existing installation

```bash
python3 /home/asus_gx10/biobench/skills/foldseek-deploy/scripts/preflight.py
```

Checks that the binary exists, is executable, reports a version, can run a
trivial search, and that the PDB database (if present) is complete. Exits
non-zero on failure.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `foldseek: command not found` | Not on PATH | Add `~/tools/foldseek/bin` to PATH, or use the absolute path |
| `cannot execute binary file` | Wrong architecture archive | Download the arm64 / avx2 build matching your CPU |
| `error while loading shared libraries` | Incomplete extraction | Re-extract the tarball; the binary should be self-contained |
| `Illegal instruction` | CPU lacks AVX2 but you used the avx2 build | Use the arm64 or a generic build |
| `Cannot open lookup file .../pdb_ca.lookup!` | Database unpacked incompletely | `for f in pdb_ca pdb_h pdb_ss pdb_clu; do ln -sf pdb.lookup $f.lookup; done` |
| Search fails on `pdb_ca` | `pdb_ca` / `pdb_seq_ca.1` missing | Re-extract from `dltmp/latest/pdb.tar.gz`, then `ln -sf pdb_ca pdb_seq_ca.0` |
| `No structures found in given input` | Passed the directory instead of the `pdb` prefix | `cd ~/foldseek-db` and pass `pdb` |

## References

- [Foldseek GitHub](https://github.com/steineggerlab/foldseek)
- van Kempen et al., *Nature Biotechnology* (2023), doi:10.1038/s41587-023-01773-0
