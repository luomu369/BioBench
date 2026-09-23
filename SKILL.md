---
name: biobench
description: '**ENTRY SKILL** — BioBench: run biomolecular AI models on local NVIDIA hardware through Agent Skills, and see results in the BioBench Viewer. USE FOR: any request involving protein structure prediction, binder design, molecular property analysis, or structure visualization on this machine; getting started with BioBench; checking whether the environment is ready. DO NOT USE FOR: general coding tasks unrelated to structural biology. This skill is the entry point — it bootstraps the environment, launches the viewer, and routes work to the specialized skills below.'
---

# BioBench

BioBench turns local NVIDIA hardware into a biomolecular AI workbench. An
agent reads these skills, runs the models on the local GPU, and publishes
results to a web viewer.

```
biobench/
├── SKILL.md                  ← you are here (entry point)
└── skills/
    ├── viewer-launch/        start the viewer, bootstrap the environment
    ├── structure-viewer/     publish structures and progress to the viewer
    ├── boltzgen-deploy/      install BoltzGen and its weights
    ├── binder-design/        run a BoltzGen binder design
    ├── proteinmpnn-deploy/   install ProteinMPNN
    ├── sequence-design/      design sequences for a fixed backbone
    ├── genmol-deploy/        install GenMol and its weights
    ├── molecule-generation/  generate small molecules with GenMol
    ├── resgen-deploy/        install ResGen and its weights
    ├── pocket-molecule-design/ design molecules for a binding pocket
    ├── foldseek-deploy/      install Foldseek
    └── structure-comparison/ compare structures with Foldseek
```

## First contact

When a user asks for anything BioBench-related, run the bootstrap check:

```bash
bash /home/asus_gx10/biobench/skills/viewer-launch/scripts/bootstrap.sh
```

It reports conda environments, model weights, viewer assets, and whether the
viewer is running. If anything is missing, re-run with `--fix`:

```bash
bash /home/asus_gx10/biobench/skills/viewer-launch/scripts/bootstrap.sh --fix
```

Then make sure the viewer is up:

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8282/
# 000 → bash /home/asus_gx10/biobench/webapp/run_viewer.sh --port 8282
```

## Routing

Pick the skill that matches the request, read its `SKILL.md`, and follow it.

| User wants | Skill | What happens |
|------------|-------|--------------|
| "design a binder for X" | `binder-design` | BoltzGen runs on the local GPU; result published |
| "design around the residues I selected" | `binder-design` | Reads `selection.json`, uses them as hotspots |
| "design a sequence for this backbone" | `sequence-design` | ProteinMPNN runs locally; backbone published |
| "generate some molecules" | `molecule-generation` | GenMol runs locally; SMILES reported |
| "grow this fragment" / "link these fragments" | `molecule-generation` | GenMol fragment modes |
| "design a molecule for this pocket" | `pocket-molecule-design` | ResGen pocket-aware generation |
| "what could bind here" / "propose ligands for these residues" | `pocket-molecule-design` | Reads `selection.json`, generates for that pocket |
| "does my design look like a known fold?" | `structure-comparison` | Foldseek search; matches reported |
| "cluster these structures" | `structure-comparison` | Foldseek clustering |
| "where could a drug bind?" / "find the pockets" | `pocket-detection` | fpocket ranks cavities by drug-likeness |
| "I have an apo structure, where do I dock?" | `pocket-detection` | Pocket centroids become docking boxes |
| "install GenMol" | `genmol-deploy` | Environment + weights setup |
| "install ResGen" | `resgen-deploy` | Repo + Google Drive weights + 3 patches |
| "install Foldseek" | `foldseek-deploy` | Static binary install |
| "install BoltzGen" / "weights missing" | `boltzgen-deploy` | Environment + weights setup |
| "install ProteinMPNN" | `proteinmpnn-deploy` | Environment setup |
| "show me 7xwo" / "display NK1R" | `structure-viewer` | Fetch from RCSB, publish |
| "what's in the viewer now" | `structure-viewer` | Read `static/current.json` |
| "what did I select" | `structure-viewer` | Read `static/selection.json` |
| "restart the viewer" | `viewer-launch` | Restart the static server |
| "is the environment ready" | `viewer-launch` | Run `bootstrap.sh` |

Read the relevant `SKILL.md` before acting — each one contains the operational
detail (flags, failure modes, output conventions) that this overview omits.

## Confirm before running anything expensive

**Never start a long-running computation without confirming the parameters
with the user first.** A BoltzGen design takes 20–30 minutes of GPU time; the
user should agree to that before it starts.

State the plan, then wait:

```
Target:      <name>, chain <id> — <n> residues
Binder type: <type>, <length range>
Hotspots:    <n> residues you selected, or "none"
Designs:     <n>
Est. time:   ~<n> minutes

Proceed?
```

If the user already specified a count ("design 8 binders"), use it and do not
re-ask. If they did not, **ask** — do not pick a number silently.

Read-only operations (listing structures, reading a selection, checking the
environment) need no confirmation.

## Run long jobs in the background

**Never hold the conversation open on a long-running command.** Start it in
the background, tell the user it is running, and carry on — they can keep
talking while the model works.

```
# start it, do not wait
<command> &          # or the agent's own background-execution facility

# then poll for completion
```

Anything that runs longer than a few seconds belongs in the background:
model inference, docking sweeps, database downloads, installs. A command
that blocks the terminal blocks the conversation, and the user cannot ask a
question or change their mind while it runs.

Report progress as it happens — publish to `status.json` so the viewer's
progress bar moves, and tell the user what step the job is on. When it
finishes, report the result and publish the structure.

Short commands (git, grep, reading a file, a quick check) run in the
foreground; they finish before the user notices.

## The publishing contract

Every skill that produces a structure writes two files into
`webapp/static/`:

- `current.cif` — the structure
- `current.json` — metadata whose `updated` field triggers the viewer to reload

Long tasks also write `status.json` so the viewer shows a progress bar.
The full contract, including atomic-write rules, is in
`skills/structure-viewer/SKILL.md`. **Read it before publishing anything.**

## Hardware

Developed and tested on NVIDIA DGX Spark (GB10, aarch64, CUDA 13.0, 121 GB
unified memory). BoltzGen needs ~40 GB of that memory for a typical design.

## Typical session

```
User:  "design a binder for NK1R"

Agent: 1. bash bootstrap.sh                     → environment OK
       2. read skills/binder-design/SKILL.md
       3. fetch 6hll.cif as the target
       4. write design.yaml, then boltzgen check
       5. publish status: running, step 1/5
       6. bash boltzgen run ...                 → GPU work, ~25 min
       7. publish rank0 → current.cif + current.json + status.json

User:  sees the designed binder in the viewer at :8282
```
