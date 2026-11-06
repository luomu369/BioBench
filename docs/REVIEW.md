# Source import and first review — 2026-11-06

Source: https://gitee.com/LRC-bit/biobench
Imported revision: c602417 (full revision recorded in the local Git history).
GitHub destination: https://github.com/luomu369/BioBench

The existing GitHub introduction page is retained. The Gitee LICENSE is
Apache-2.0; README references to MIT were corrected without replacing LICENSE.
External tools and model weights retain their own licenses and download terms.
Both the local checkout and GitHub migration retain the original Gitee
commit history and the existing GitHub introduction-page history.

## Changes

- Bound and validate JSON object bodies across seven HTTP handlers; reject
  malformed/negative/duplicate lengths and unsupported transfer encoding.
- Use unique temporary files for simultaneous selection updates.
- Reject a stale opencode binary when an installation fails or returns the
  wrong version.
- Preserve the introduction page; publish only index.html to GitHub Pages.
- Add standard-library HTTP regression tests and Linux/Windows CI.

## Local validation

- Python 3.12: 10 HTTP regression tests passed.
- All Python files under webapp and skills compile successfully.
- GPU inference, docking and dynamics were not executed: this Windows machine
  is not the documented Linux/local GPU workstation environment.
- Shell runtime and installer failure behavior require Linux verification;
  CI checks shell syntax, not real installation or model downloads.

## Development checks

Use Python 3.10 or newer for the core checks:

```sh
python -m compileall -q webapp skills
python -m unittest discover -s tests -v
```

Viewer-only local smoke test (no model environment is required):

```sh
python webapp/serve.py --host 127.0.0.1 --port 8282
```

The full start/stop scripts and GPU skills target Linux. Native Windows
support for those workflows has not been established.

## Remaining work, in priority order

1. Server access boundary: the default now binds to loopback. Network access
   requires an explicit --host or BIOBENCH_HOST setting. Viewer authentication
   and trusted-origin enforcement remain unfinished (wildcard CORS is still
   present). Prefer SSH forwarding. The Agent can execute local commands.
2. Concurrent task/status/archive updates: selection writes are fixed; shared
   status writers still need an inter-process locking/transaction strategy.
3. Installer: verify official release checksums and test network failure,
   stale binary, unsupported platform and successful install cases on Linux.
4. GPU validation: pin and verify tool environments and run a small documented
   structure-to-result workflow on the target hardware.
5. Scientific output: review overlapping multi-design publication and add
   independent structural validation fixtures before changing algorithms.

This is a first engineering pass, not a full security or scientific audit.

## Follow-up: startup and invalid input handling

- Default to loopback; allow explicit network binding using --host or
  BIOBENCH_HOST. Reject ports outside 1–65535 before starting the server.
- Reject invalid task, session and image field types before changing state.
  Empty image strings and null session IDs retain their existing behavior.
- Handle extremely long Content-Length values without integer-conversion
  exceptions. Preserve valid zero-padded lengths.
- Close the listening socket on normal server exit or keyboard interruption.
