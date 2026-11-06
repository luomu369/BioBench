#!/usr/bin/env bash
# Start BioBench — the viewer and the agent behind it.
#
# One command brings up both halves:
#
#   bash start.sh            # viewer on 8282, opencode on 4096
#   bash start.sh --port 9000
#
# The viewer is a static page plus a thin server; the agent is a vendored
# opencode that the viewer's panel drives. Either can run alone — the viewer
# renders whatever a skill published, and opencode serves any harness that
# talks to it — but the panel is the point, so both start together.
#
# Usage:
#   bash start.sh [--port 8282] [--agent-port 4096] [--no-agent]
#
# What it does, in order:
#   1. Checks the opencode binary, and installs it if it is missing
#   2. Starts opencode, unless it is already up
#   3. Starts the viewer, unless it is already up
#   4. Prints both URLs and the pid file locations
#
# Both are started detached (setsid), so they survive the terminal that ran
# this script and a VS Code restart. Stop them with stop.sh, or by killing the
# pids in run.pid and agent.pid.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=8282
AGENT_PORT=4096
WITH_AGENT=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2"; shift 2 ;;
        --agent-port) AGENT_PORT="$2"; shift 2 ;;
        --no-agent)   WITH_AGENT=0; shift ;;
        -h|--help)
            sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

BINARY="$HERE/vendor/bin/opencode"
VIEWER_PID_FILE="$HERE/run.pid"
AGENT_PID_FILE="$HERE/agent.pid"
VIEWER_LOG="$HERE/webapp/viewer.log"
AGENT_LOG="$HERE/webapp/opencode-server.log"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

up() {  # up <port> — is something listening
    curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" 2>/dev/null
}

# ---------------------------------------------------------------- opencode

if [[ $WITH_AGENT -eq 1 ]]; then
    if up "$AGENT_PORT"; then
        say "opencode already running on :$AGENT_PORT"
    else
        if [[ ! -x "$BINARY" ]]; then
            say "opencode not installed — fetching it"
            bash "$HERE/vendor/install_opencode.sh" || die "install failed"
        fi
        if [[ ! -x "$BINARY" ]]; then
            die "still no binary at $BINARY after install"
        fi

        # The key has to reach the child. opencode reads {env:...} from its own
        # environment, so a key loaded here but not passed on resolves to an
        # empty string in the server and fails as a 401 that looks like a bad
        # key rather than a missing one.
        if [[ -f "$HERE/webapp/agent.key" ]]; then
            set -a
            # shellcheck disable=SC1091
            source "$HERE/webapp/agent.key"
            set +a
        else
            warn "no webapp/agent.key — the panel will need a key before it can answer"
        fi

        say "starting opencode on :$AGENT_PORT"
        # Started from the repo root: opencode finds its config by walking up
        # from the working directory, and opencode.jsonc lives here.
        setsid nohup "$BINARY" serve \
            --port "$AGENT_PORT" --hostname 127.0.0.1 \
            >> "$AGENT_LOG" 2>&1 < /dev/null &
        echo $! > "$AGENT_PID_FILE"

        for _ in $(seq 1 40); do
            up "$AGENT_PORT" && break
            sleep 0.25
        done
        up "$AGENT_PORT" || die "opencode did not come up — see $AGENT_LOG"
        say "opencode up (pid $(cat "$AGENT_PID_FILE"))"
    fi
fi

# ------------------------------------------------------------------ viewer

if up "$PORT"; then
    say "viewer already running on :$PORT"
else
    say "starting viewer on :$PORT"
    cd "$HERE/webapp"
    setsid nohup python3 serve.py --port "$PORT" \
        >> "$VIEWER_LOG" 2>&1 < /dev/null &
    echo $! > "$VIEWER_PID_FILE"

    for _ in $(seq 1 40); do
        up "$PORT" && break
        sleep 0.25
    done
    up "$PORT" || die "viewer did not come up — see $VIEWER_LOG"
    say "viewer up (pid $(cat "$VIEWER_PID_FILE"))"
fi

# ------------------------------------------------------------------ report

cat <<EOF

  BioBench is running.

    viewer    http://localhost:$PORT
$( [[ $WITH_AGENT -eq 1 ]] && echo "    agent     http://127.0.0.1:$AGENT_PORT  (drives the panel)" )

  Ask it something in the panel on the right, e.g.
    "design a binder for NK1R"
    "show me 7xwo"

  Logs:   $VIEWER_LOG
$( [[ $WITH_AGENT -eq 1 ]] && echo "          $AGENT_LOG" )
  Stop:   bash stop.sh

EOF
