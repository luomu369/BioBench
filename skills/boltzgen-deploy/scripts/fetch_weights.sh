#!/usr/bin/env bash
# Download BoltzGen weights, preferring a mirror endpoint when the official
# Hugging Face endpoint is slow or unreachable.
#
# Usage:
#   bash fetch_weights.sh [--dest DIR] [--endpoint URL] [--force]
#
# Environment:
#   HF_TOKEN   optional Hugging Face token for higher rate limits

set -euo pipefail

DEST="${HOME}/models/boltzgen-1"
ENDPOINT="https://hf-mirror.com"
FORCE=""
REPO="boltzgen/boltzgen-1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest) DEST="$2"; shift 2 ;;
        --endpoint) ENDPOINT="$2"; shift 2 ;;
        --force) FORCE="--force"; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

echo "== Repo:     $REPO"
echo "== Endpoint: $ENDPOINT"
echo "== Dest:     $DEST"

mkdir -p "$DEST"

# Resolve the python interpreter: prefer an activated conda env, else fall back.
PY="${PYTHON:-python3}"

HF_ENDPOINT="$ENDPOINT" HF_HUB_ENABLE_HF_TRANSFER=0 "$PY" - "$DEST" "$FORCE" <<'PYEOF'
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

dest, force = sys.argv[1], sys.argv[2] == "--force"
path = snapshot_download(
    repo_id="boltzgen/boltzgen-1",
    local_dir=dest,
    max_workers=8,
    force_download=force,
)
print(f"DONE: {path}")
PYEOF

echo "== Verifying checkpoint sizes (LFS pointers are ~76 bytes)"
python3 - "$DEST" <<'PYEOF'
import sys
from pathlib import Path

dest = Path(sys.argv[1])
bad = []
for ckpt in sorted(dest.rglob("*.ckpt")):
    size_gb = ckpt.stat().st_size / 1024**3
    flag = "OK " if size_gb > 0.01 else "BAD"
    print(f"  [{flag}] {ckpt.name:35s} {size_gb:6.2f} GB")
    if size_gb <= 0.01:
        bad.append(ckpt.name)

if bad:
    print(f"\nERROR: {len(bad)} file(s) are LFS pointers, not real weights.")
    print("Re-run with --force, or check network access to the endpoint.")
    sys.exit(1)
print("\nAll checkpoints look valid.")
PYEOF
