#!/usr/bin/env bash
# Install the opencode binary the agent panel drives.
#
# The binary is not committed: it is ~177 MB, it is platform-specific, and a
# repo that carries a compiled ARM64 executable is a repo that cannot be
# cloned on anything else. This fetches the pinned version instead, so every
# machine runs the one this was tested against.
#
# Usage:
#   bash install_opencode.sh            # install if missing or wrong version
#   bash install_opencode.sh --force    # reinstall even if already correct
#
# Idempotent: a machine that already has the right version does nothing.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(python3 -c "import json;print(json.load(open('$HERE/opencode.json'))['version'])")"
TARGET="$HERE/bin/opencode"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

# Already the right version? Then there is nothing to do. Checked by asking
# the binary rather than by comparing file dates — a version bump in
# opencode.json must take effect on a machine that already has an older copy.
if [[ $FORCE -eq 0 && -x "$TARGET" ]]; then
    have="$("$TARGET" --version 2>/dev/null || true)"
    if [[ "$have" == "$VERSION" ]]; then
        echo "opencode $VERSION already installed at $TARGET"
        exit 0
    fi
    echo "opencode $have present, want $VERSION — reinstalling"
fi

mkdir -p "$HERE/bin"

# The upstream installer is used rather than a hand-rolled download: it knows
# the current asset naming scheme, which changes between releases more often
# than this file is updated, and it verifies what it fetched.
#
# It installs to ~/.opencode/bin and edits the shell rc — neither is wanted
# here. --no-modify-path stops the rc edit. The install directory is
# hardcoded upstream, so the binary is copied out afterwards and the vendored
# path is the one the panel uses; a stray ~/.opencode is harmless and means
# the user also gets a working `opencode` on PATH if they want the TUI.
echo "installing opencode $VERSION"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if curl -fsSL https://opencode.ai/install -o "$tmp/install.sh"; then
    bash "$tmp/install.sh" --version "$VERSION" --no-modify-path \
        >"$tmp/log" 2>&1 || true
    if [[ -x "$HOME/.opencode/bin/opencode" ]]; then
        cp "$HOME/.opencode/bin/opencode" "$TARGET"
    fi
fi

if [[ ! -x "$TARGET" ]]; then
    echo "error: could not install opencode $VERSION" >&2
    [[ -f "$tmp/log" ]] && tail -5 "$tmp/log" >&2
    echo "       install it by hand and place it at $TARGET" >&2
    exit 1
fi

chmod +x "$TARGET"
echo "installed: $("$TARGET" --version) at $TARGET"
