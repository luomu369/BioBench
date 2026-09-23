---
name: foldseek-deploy
description: '**DEPLOYMENT SKILL** — Install Foldseek (fast protein structure search and alignment) on any machine and verify the installation. USE FOR: setting up Foldseek from scratch; upgrading an existing install; checking that the binary runs; diagnosing architecture or library errors. DO NOT USE FOR: running a structure comparison (use structure-comparison); protein design (use binder-design); publishing results (use structure-viewer). INVOKES: shell tools (curl/tar), file system tools. No GPU, no Python, no conda — just a static binary.'
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

## Verifying an existing installation

```bash
python3 /home/asus_gx10/biobench/skills/foldseek-deploy/scripts/preflight.py
```

Checks that the binary exists, is executable, reports a version, and can run a
trivial search. Exits non-zero on failure.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `foldseek: command not found` | Not on PATH | Add `~/tools/foldseek/bin` to PATH, or use the absolute path |
| `cannot execute binary file` | Wrong architecture archive | Download the arm64 / avx2 build matching your CPU |
| `error while loading shared libraries` | Incomplete extraction | Re-extract the tarball; the binary should be self-contained |
| `Illegal instruction` | CPU lacks AVX2 but you used the avx2 build | Use the arm64 or a generic build |

## References

- [Foldseek GitHub](https://github.com/steineggerlab/foldseek)
- van Kempen et al., *Nature Biotechnology* (2023), doi:10.1038/s41587-023-01773-0
