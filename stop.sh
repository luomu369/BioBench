#!/usr/bin/env bash
# Stop BioBench — the viewer and the opencode server behind it.
#
# Usage:
#   bash stop.sh              # stop both
#   bash stop.sh --viewer     # only the viewer
#   bash stop.sh --agent      # only opencode
#
# The pids come from the files start.sh wrote. A pid file that is missing or
# stale is not an error: the thing it named is already gone, which is what was
# asked for. What *is* reported is a pid that is alive but will not die, since
# that is the one case where stopping did not happen.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VIEWER_PID_FILE="$HERE/run.pid"
AGENT_PID_FILE="$HERE/agent.pid"

DO_VIEWER=1
DO_AGENT=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --viewer) DO_AGENT=0; shift ;;
        --agent)  DO_VIEWER=0; shift ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# stop <pidfile> <name> — SIGTERM, wait, then SIGKILL if it is still there.
stop() {
    local file="$1" name="$2" pid
    [[ -f "$file" ]] || { say "$name not running (no pid file)"; return 0; }
    pid="$(cat "$file" 2>/dev/null || true)"
    rm -f "$file"
    [[ -n "$pid" ]] || { say "$name not running (empty pid file)"; return 0; }

    if ! kill -0 "$pid" 2>/dev/null; then
        say "$name already stopped (pid $pid gone)"
        return 0
    fi

    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
        # A turn mid-run is the usual reason: opencode finishes the step it is
        # in before it exits, and a skill script it launched keeps going.
        kill -9 "$pid" 2>/dev/null || true
        sleep 0.5
        if kill -0 "$pid" 2>/dev/null; then
            printf '\033[1;31merror:\033[0m %s pid %s would not die\n' \
                "$name" "$pid" >&2
            return 1
        fi
        say "$name killed (pid $pid)"
    else
        say "$name stopped (pid $pid)"
    fi
}

# The agent first, so the panel is not left pointing at a server that goes away
# under it. A viewer whose agent vanished still renders; an agent whose viewer
# vanished has nowhere to show anything.
[[ $DO_AGENT  -eq 1 ]] && stop "$AGENT_PID_FILE"  "opencode"
[[ $DO_VIEWER -eq 1 ]] && stop "$VIEWER_PID_FILE" "viewer"

say "done"
