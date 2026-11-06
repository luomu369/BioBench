#!/usr/bin/env bash
# Start the BioBench Viewer — a static page that renders whatever structure
# a BioBench skill publishes to static/current.cif, and that records the
# residues the user selects so skills can design around them.
#
# Usage:
#   bash run_viewer.sh [--port 8282]
#
# Skills write to static/current.cif + static/current.json; the page polls
# them every 2 seconds. The page POSTs the user's selection back to
# static/selection.json. See skills/structure-viewer/SKILL.md for the contract.

set -euo pipefail

PORT=8282
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

exec python3 serve.py --port "$PORT"
