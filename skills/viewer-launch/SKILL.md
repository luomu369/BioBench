---
name: viewer-launch
description: '**LAUNCH SKILL** — Start the BioBench Viewer, the web app that renders protein structures produced by other BioBench skills. USE FOR: starting or restarting the viewer; checking whether the viewer is already running; changing the viewer port; troubleshooting a blank viewer page. DO NOT USE FOR: publishing a structure to the viewer (see structure-viewer); designing proteins (see boltzgen-deploy). The viewer is a static page with no backend — it polls static/current.json every 2 seconds.'
---

# Viewer Launch

The BioBench Viewer is a single static HTML page served by Python's stdlib
`http.server`. It has no backend, no LLM, and no dependencies. It renders
whatever structure a skill publishes to `static/current.cif`.

## Start

```bash
bash /home/asus_gx10/biobench/webapp/run_viewer.sh --port 8282
```

Then open http://localhost:8282

## Check if already running

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8282/
```

- `200` — already running, do not start a second copy
- `000` / connection refused — not running, start it

## Restart

```bash
pkill -f "http.server" && sleep 2
bash /home/asus_gx10/biobench/webapp/run_viewer.sh --port 8282
```

## Change port

```bash
bash /home/asus_gx10/biobench/webapp/run_viewer.sh --port 9000
```

Common conflicts: `8188` (ComfyUI), `8443` (code-server), `11434` (Ollama).

## Run in background

```bash
nohup bash /home/asus_gx10/biobench/webapp/run_viewer.sh --port 8282 \
  > /tmp/biobench-viewer.log 2>&1 &
```

## What the viewer watches

| File | Role |
|------|------|
| `static/current.cif` | The structure to render |
| `static/current.json` | Metadata; its `updated` field triggers a reload |
| `static/status.json` | Progress bar and status message |

See the `structure-viewer` skill for the full contract.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Blank page, `$3Dmol is not defined` | `3Dmol-min.js` missing | Re-download to `static/3Dmol-min.js` |
| Page loads but no structure | No `current.json`, or `updated` unchanged | Publish a structure via the `structure-viewer` contract |
| Structure renders as dots, not cartoon | `format` field wrong in `current.json` | Must be `cif` or `pdb`, matching the file |
| Port already in use | Another service on that port | Pick another port |
| Structure appears then vanishes | Non-atomic write; viewer read a partial file | Always write to `.tmp` then `mv` |

## Files

```
webapp/
├── run_viewer.sh          # launcher
└── static/
    ├── index.html         # the viewer page
    ├── 3Dmol-min.js       # 3Dmol.js (local copy, no CDN)
    ├── current.cif        # published structure
    ├── current.json       # published metadata
    └── status.json        # published progress
```
