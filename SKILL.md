---
name: biobench
description: '**ENTRY SKILL** — BioBench: run biomolecular AI models on local hardware through Agent Skills, and see results in the BioBench Viewer. USE FOR: any request involving protein structure prediction, binder design, molecular property analysis, or structure visualization on this machine; getting started with BioBench; checking whether the environment is ready. DO NOT USE FOR: general coding tasks unrelated to structural biology. This skill is the entry point — it bootstraps the environment, launches the viewer, and routes work to the specialized skills below.'
---

# BioBench

BioBench turns local hardware into a biomolecular AI workbench. An
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
    ├── ligand-binding-design/ design a binder for a small-molecule ligand
    ├── proteinmpnn-deploy/   install ProteinMPNN
    ├── sequence-design/      design sequences for a fixed backbone
    ├── genmol-deploy/        install GenMol and its weights
    ├── molecule-generation/  generate small molecules with GenMol
    ├── resgen-deploy/        install ResGen and its weights
    ├── pocket-molecule-design/ design molecules for a binding pocket
    ├── foldseek-deploy/      install Foldseek
    ├── structure-comparison/ compare structures with Foldseek
    ├── mmseqs-deploy/        install MMseqs2 (GPU-accelerated)
    ├── sequence-search/      search protein sequences with MMseqs2
    ├── vina-deploy/          install AutoDock Vina
    ├── molecular-docking/    dock ligands into a receptor
    ├── conformational-analysis/ scan bond torsions with a force field
    ├── molecule-editing/       change a selected fragment in place
    ├── molecular-dynamics-deploy/ install OpenMM for simulation
    ├── molecular-dynamics/    simulate a protein over time with OpenMM
    ├── admet-prediction/     drug-likeness and structural alerts
    └── pocket-detection/     find binding pockets with fpocket
```

## Where things are

No path in any skill is written out in full. Every command uses these five
variables, and every script resolves them the same way — see
`skills/_shared/biobench_paths.py`, which is the one place the defaults live.

| Variable | What it points at | Default |
|----------|-------------------|---------|
| `$BB` | the repository root | `$HOME/biobench` |
| `$CONDA` | the conda installation | `$HOME/miniconda3` |
| `$MODELS` | downloaded model weights | `$HOME/models` |
| `$TOOLS` | binaries built outside conda | `$HOME/tools` |
| `$FOLDSEEK_DB` | the foldseek search database | `$MODELS/foldseek-db` |

A command that needs them defines them first, so it can be copied and run as
it stands:

```bash
BB="${BB:-$HOME/biobench}"
CONDA="${CONDA:-$HOME/miniconda3}"
MODELS="${MODELS:-$HOME/models}"
TOOLS="${TOOLS:-$HOME/tools}"
FOLDSEEK_DB="${FOLDSEEK_DB:-$MODELS/foldseek-db}"
```

Each is overridable, for a machine that keeps its conda or weights somewhere
unusual. The scripts probe the usual locations too, so a machine that already
has conda does not have to be told where it is.

Environments, weights, and tools all live **outside** the repository — one
copy per machine, shared by every clone. There is deliberately no fallback
inside the tree: a second copy in the repo would be a quieter answer to
"where is the docking env" that could go stale, and a skill fixed against one
while the other drifted would fail in a way that looks like a missing
dependency rather than a wrong path. When a location is missing, the scripts
say so and name the path they wanted.

To see what resolves to what on this machine:

```bash
BB="${BB:-$HOME/biobench}"
python3 "$BB/skills/_shared/biobench_paths.py"
```

## First contact

When a user asks for anything BioBench-related, run the bootstrap check:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
bash $BB/skills/viewer-launch/scripts/bootstrap.sh
```

It reports conda environments, model weights, viewer assets, and whether the
viewer is running. If anything is missing, re-run with `--fix`:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
bash $BB/skills/viewer-launch/scripts/bootstrap.sh --fix
```

Then make sure the viewer is up:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
curl -s -o /dev/null -w '%{http_code}' http://localhost:8282/
# 000 → bash $BB/webapp/run_viewer.sh --port 8282
```

## Routing

Pick the skill that matches the request, read its `SKILL.md`, and follow it.

| User wants | Skill | What happens |
|------------|-------|--------------|
| "design a binder for X" | `binder-design` | BoltzGen runs on the local GPU; result published |
| "design around the residues I selected" | `binder-design` | Reads `selection.json`, uses them as hotspots |
| "design a sequence for this backbone" | `sequence-design` | ProteinMPNN runs locally; backbone published |
| "mutate the residues I selected" | `sequence-design` | Saturation scan per position; mutant published |
| "what should I change at these positions?" | `sequence-design` | Same scan, reported as a mutation table |
| "generate some molecules" | `molecule-generation` | GenMol runs locally; SMILES reported |
| "grow this fragment" / "link these fragments" | `molecule-generation` | GenMol fragment modes |
| "design a molecule for this pocket" | `pocket-molecule-design` | ResGen pocket-aware generation |
| "what could bind here" / "propose ligands for these residues" | `pocket-molecule-design` | Reads `selection.json`, generates for that pocket |
| "does my design look like a known fold?" | `structure-comparison` | Foldseek search; matches reported |
| "cluster these structures" | `structure-comparison` | Foldseek clustering |
| "what is this sequence?" / "find homologues" | `sequence-search` | MMseqs2 search; hits reported |
| "does my binder resemble a known family?" | `sequence-search` | MMseqs2 against PDB or Swiss-Prot |
| "cluster these sequences" | `sequence-search` | MMseqs2 clustering |
| "is this molecule drug-like?" | `admet-prediction` | Lipinski, QED, SA, PAINS, Brenk |
| "which of these should I dock?" | `admet-prediction` | Ranks by QED and flags alerts |
| "where could a drug bind?" / "find the pockets" | `pocket-detection` | fpocket ranks cavities by drug-likeness |
| "I have an apo structure, where do I dock?" | `pocket-detection` | Pocket centroids become docking boxes |
| "dock this molecule into the protein" | `molecular-docking` | Vina runs locally; poses and affinities reported |
| "where does this drug bind?" / "score these ligands" | `molecular-docking` | Rigid or flexible docking |
| "screen this library against the target" | `molecular-docking` | Batch docking |
| "what is the lowest-energy state around this bond?" | `conformational-analysis` | MMFF94 torsion scan; profile and minimum published |
| "how much energy to rotate this side chain?" | `conformational-analysis` | Same scan, reported as a barrier |
| "is this residue in a strained conformation?" | `conformational-analysis` | Compares the crystal rotamer against the preferred one |
| "change this into a benzene ring" | `molecule-editing` | Selected atoms rebuilt as a ring; the rest kept |
| "replace this fragment" / "make these atoms a phenyl" | `molecule-editing` | Same, in place, with the complex intact |
| "is this structure sound?" / "the viewer looks wrong" | `structure-reviewer` agent | Verifies the structure on screen against its source; verdict with the numbers |
| "simulate this structure" / "run MD on it" | `molecular-dynamics` | OpenMM in explicit water; trajectory and stability metrics |
| "is this design stable?" / "relax this complex" | `molecular-dynamics` | Minimisation, equilibration, production, RMSD/Rg |
| "how much does the binding site move?" | `molecular-dynamics` | Trajectory analysis over the selected residues |
| "install OpenMM" / "can this machine run MD?" | `molecular-dynamics-deploy` | Env, platforms, force fields, GPU check |
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

## Subagents

`.opencode/agent/` holds subagents the main agent can delegate to through the
`task` tool. Each one keeps its work out of the main agent's context: it runs,
and it returns a conclusion instead of the tool output that produced it.

| Agent | Delegated for | Returns |
|-------|---------------|---------|
| `structure-reviewer` | Verifying a structure before it is published | SOUND / SOUND WITH CAVEATS / NOT SOUND, with numbers |
| `pocket-analyst` | Characterising a pocket's chemistry | Composition, charge, buried-ness — never residue numbers |
| `docking-runner` | Running a Vina job | Affinity, pose count, dropped-residue count, result path |
| `report-writer` | Writing the user-facing summary | Four to eight sentences from facts it is given |

**The routing is the model's, not the config's.** `subagent_type` is a free
string and opencode injects every agent's name and description into the tool
schema as a menu — nothing pins a task to an agent. In practice the main agent
does not reach for them on its own: it reads the file itself instead. The
calling contract therefore lives in `vendor/prompts/biobench.md`, which states
delegating as the default and scopes "read before answering" to answering
rather than running.

**Write the contract, not the route.** A table of "when X, spawn Y" is followed
mechanically and misapplied the moment a task matches no row. What generalises:
delegate when the conclusion is needed and not the data, pass paths plus one
sentence on what the run was for, and never silently overrule a NOT SOUND
verdict.

Adding an agent means writing a file under `.opencode/agent/` and restarting
`opencode serve` — a running server does not see new agents. See
`skills/_shared/silent-failures.md` for the two traps: a subagent cannot be run
directly with `--agent`, and a whitelist of script names alone is too narrow
for an agent that has to find something first.

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

**A run that outlives the hour is checked on.** The viewer wakes the model
once an hour while a background task is still going, with the job's log tail
attached, so the model reads the log and reports progress — a run that has
quietly stalled looks exactly like one that is progressing, and something
that can say so has to be looking. The wake is suppressed while a turn is
running, so it lands in an idle moment rather than queueing behind a reply.
A run shorter than an hour gets no such wake: its completion wake is the
report.

Anything that runs longer than a few seconds belongs in the background:
model inference, docking sweeps, database downloads, installs. A command
that blocks the terminal blocks the conversation, and the user cannot ask a
question or change their mind while it runs.

Report progress as it happens — publish to `status.json` so the viewer's
progress bar moves, and tell the user what step the job is on. When it
finishes, report the result and publish the structure.

**Declare the plan before starting a long job, and give each run its own
`--job`.** Anything over a few minutes should publish its whole route up
front so the user sees what is going to happen, and several jobs can proceed
at once — a GPU design alongside a CPU docking run. The convention, with
examples, is in `structure-viewer` under "Every skill reports the same way".

Short commands (git, grep, reading a file, a quick check) run in the
foreground; they finish before the user notices.

## Multi-skill pipelines

A request like "design a cyclic peptide for GLP-1R and validate it" spans
several skills. Run them as **one task with one plan**, not as several
unrelated runs:

```bash
# 1. publish the input first — the viewer has something to show immediately
python3 $PUB --structure 6X18.cif --name glp1r_input --source rcsb \
  --job glp1r --roles "R=receptor,P=peptide"

# 2. declare the whole route, under one --job
python3 $PUB --status --state queued --skill biobench --job glp1r \
  --label "GLP-1R 环肽设计" \
  --plan "获取结构,口袋发现,环肽设计,序列验证,性质评估,发布结果"

# 3. each skill reports its own step, naming itself in --skill
python3 $PUB --status --state running --step "口袋发现" --index 2 --total 6 \
  --skill pocket-detection --job glp1r --message "..."
```

The `--job` stays constant across every skill; the `--skill` on each report
is what attributes the step. The viewer then shows one task with six steps,
each naming the tool that did it, and each finished step clickable back to
what it produced.

**Publish the input before declaring the plan.** A task that only declares a
route leaves the viewer on the *previous* task's structure — for the twenty
minutes a simulation runs the user is looking at something unrelated, with no
way to see what this task is even working on. One `--structure` call binds
the task to the viewer from the first second.

**Split the plan to the granularity the user would want to inspect.** Six
small steps read as a record of what happened; two big ones read as a black
box. A step that produces no structure — a sequence search, a property
analysis — still gets reported, with the finding in `--message`.

**Verify the chain before designing against it.** Multi-chain PDB entries
routinely put the receptor in a later chain. In 6X18 the GLP-1R is chain R,
not chain A, and extracting the wrong one produced a target 50 Å from its
ligand. The check is one command — see `pocket-detection` under "Verify
which chain is the receptor".

## The publishing contract

Every skill that produces a structure writes two files into
`webapp/static/`:

- `current.cif` — the structure
- `current.json` — metadata whose `updated` field triggers the viewer to reload

Long tasks also write `status.json` so the viewer shows a progress bar.
The full contract, including atomic-write rules, is in
`skills/structure-viewer/SKILL.md`. **Read it before publishing anything.**

## Hardware

Developed and tested on local GPU workstation (ARM64 GPU, aarch64, CUDA 13.0, 121 GB
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
