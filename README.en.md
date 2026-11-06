<!-- Modified 2026-11-06: align license with LICENSE; add GitHub source. -->
# BioBench

> One DGX Spark, one agent, twenty-three skills, and a web page.
> **Molecular design in plain language.**

BioBench is a biomolecular design workbench. It wraps a set of local-GPU AI
models — structure prediction, binder design, molecule generation, docking,
molecular dynamics — into a group of Agent Skills, so a large-model harness can
drive them in natural language and see the results in a web viewer.

Say "design a 12-amino-acid cyclic peptide in this pocket" and it runs BoltzGen.
Say "replace that hydrogen with a benzene ring" and it edits the structure. Say
"run dynamics to see whether this pose holds" and it runs OpenMM. Every step
publishes to the same page, and you watch the structure take shape.

- **Repo**: https://gitee.com/LRC-bit/biobench
- **Hardware**: NVIDIA DGX Spark (GB10, aarch64, CUDA 13.0, 121 GB unified memory)
- **Agent model**: StepFun `step-5-preview`
- **License**: Apache-2.0 (see [LICENSE](LICENSE))
- **Demo video**: [Bilibili](https://www.bilibili.com/video/BV1jgaE6cEEz/)
- **Competition write-up**: [BioBench · DGX Spark Agent Skills](https://luomu369.github.io/BioBench/)

---

## Contents

- [1. What this is, and why now](#1-what-this-is-and-why-now)
- [2. Highlights](#2-highlights)
- [3. Quick start](#3-quick-start)
- [4. Technical approach](#4-technical-approach)
- [5. Architecture](#5-architecture)
- [6. Agent Skills design](#6-agent-skills-design)
- [7. Deploying on local compute](#7-deploying-on-local-compute)
- [8. Model optimisation](#8-model-optimisation)
- [9. Tech stack](#9-tech-stack)
- [10. Skill list](#10-skill-list)
- [11. State and limits](#11-state-and-limits)

---

## 1. What this is, and why now

### The models arrived; the interface did not

Open-source models in structural biology have arrived in a dense cluster and are
good enough to use:

| Model | Does | Weights |
|-------|------|---------|
| BoltzGen | Binder / ligand design (diffusion + structure prediction) | ~10 GB |
| ProteinMPNN | Sequence design on a fixed backbone | ~0.5 GB |
| GenMol | Small-molecule generation (de novo / completion / linking) | ~2 GB |
| ResGen | Pocket-conditioned molecule generation | ~1 GB |
| AutoDock Vina | Docking and scoring | binary |
| OpenMM | Molecular dynamics | library |
| Foldseek / MMseqs2 | Structure and sequence search | binaries |

Each one works alone. **Together they do not.** Every model has its own conda
environment, weight download, input format and output parsing. A project that
wants to walk "find pocket → generate molecule → dock → validate with dynamics"
shuttles between four environments and five file formats by hand, writing glue
code at every step.

### The hardware threshold dropped to one machine

A DGX Spark (GB10) has 121 GB of unified memory and a GPU that can carry the
load. That is three times what a typical BoltzGen design needs (~40 GB). **Work
that once needed a cluster now fits on a desktop box.**

But "can run" and "can use" are different things: the models are on the machine,
and the human still reaches them through SSH, `conda activate`, and
`python script.py --arg...`.

### Agents changed the unit of interaction

Large-model harnesses made "drive a command line in natural language" real, and
the Agent Skills spec (`SKILL.md` + scripts) gave it a standard shape: **write
domain knowledge as documentation, write operations as scripts, and let the
agent read the docs and run the scripts.**

BioBench applies that shape to biomolecular design. It trains no models and does
no inference optimisation — it solves the **orchestration** problem: letting a
general agent drive this pile of specialist tools safely and reproducibly.

---

## 2. Highlights

**① There is no LLM in the viewer, and no backend logic.** It is a static page
that renders three files the skills publish to disk. The agent works on the
command line; the viewer only displays. That split is why the whole thing runs
at all: skills can be written in any language, the viewer survives a restart
without losing state, any skill can be tested on its own, and the agent can read
those same files to answer "how far along is it".

**② Long tasks are handed to the panel, not held in the turn.** The model ends
its turn after submitting a fifty-minute job, but it says "I'll report when it's
done" — a promise nothing was keeping. The panel now watches the job by **pid
plus process start time** (a pid is eventually reused, so a pid alone would wait
forever on work that died long ago), and on completion wakes the model **with
the log's tail attached**, so the report is written from the ending rather than
from a poll.

**③ Refuse more than guess.** Failures in biomolecular computing are mostly
silent — a shifted serial, a dropped residue, a phantom bond: none of them
error, they just make the number wrong. So BioBench's scripts check and exit:
a PDB column that does not fit raises, a dead pid is reported, a selected atom
overlapping another residue warns. `skills/_shared/silent-failures.md` records
twelve silent failures that actually happened here. It is the project's most
valuable asset.

**④ Domain knowledge separated from the agent loop.** Tool-calling loops have
countless implementations, but "when should flexible docking be used", "which
residues does meeko drop", "which PyTorch wheel matches this machine's CUDA" can
only be written down as documentation. `SKILL.md` is the documentation,
`scripts/` is the operation, and the agent joins them. Switching harnesses
rewrites no domain knowledge.

---

## 3. Quick start

```bash
# One command starts everything: viewer + agent
bash start.sh
#    → http://localhost:8282

# Stop
bash stop.sh
```

`start.sh` installs opencode on demand (downloaded on first run), starts the
agent before the viewer, and detaches both with `setsid` — **a VS Code restart
does not kill them.**

Viewer only (the agent panel is unavailable, but structures still render):

```bash
bash start.sh --no-agent
# or
bash webapp/run_viewer.sh --port 8282
```

The agent panel is on the right once it is up. Describe what you want in plain
language; select residues or atoms on the structure and the selection is sent
along with the question.

First run needs an API key:

```bash
echo 'STEPFUN_API_KEY=sk-xxx' > webapp/agent.key
```

The `STEPFUN_API_KEY` environment variable works too, but it does not outlive
the terminal — and the viewer outlives the terminal that started it, so the file
is the recommended route.

### Layout

```
biobench/
├── SKILL.md                  Entry point: bootstrap, route, orchestrate
├── opencode.jsonc            Agent config: provider, model, permissions, subagent menu
├── start.sh / stop.sh        One-command start / stop (viewer + agent)
├── skills/                   23 skills (8 deploy / 13 run / 1 publish + shared)
│   ├── _shared/              biobench_paths.py, pdb_format.py, selection.py,
│   │                          verify_structure.py, silent-failures.md
│   └── <skill>/
│       ├── SKILL.md          Domain knowledge (when, which parameters, which traps)
│       └── scripts/          Executable operations (preflight.py / business scripts)
├── .opencode/
│   └── agent/                4 subagents: reviewer / analyst / runner / writer
├── webapp/
│   ├── serve.py              Viewer server (static files + agent panel + state)
│   ├── agent.py              opencode client: sessions, streaming, permissions, job watching
│   └── static/
│       ├── index.html        Viewer and agent panel (3Dmol.js, ~8400 lines)
│       ├── current.cif       ← published structure
│       ├── current.json      ← published metadata
│       └── status.json       ← published progress
└── vendor/                   Vendored opencode binary and project config
    └── prompts/biobench.md   Main agent's system prompt (with the subagent contract)
```

---

## 4. Technical approach

### 4.1 Three layers, communicating only through files

```
┌─────────────────────────────────────────────┐
│  Agent layer (opencode + step-5-preview)     │
│  Reads SKILL.md, runs commands, decides      │
└──────────────────┬──────────────────────────┘
                   │ calls
┌──────────────────▼──────────────────────────┐
│  Skill layer (skills/, 23 of them)           │
│  Each = SKILL.md (knowledge) + scripts/ (ops)│
│  Three kinds: deploy / run / publish         │
└──────────────────┬──────────────────────────┘
                   │ reads and writes files
┌──────────────────▼──────────────────────────┐
│  Viewer layer (webapp/)                      │
│  Static page + one thin server               │
│  Renders current.cif / current.json / status.json
└─────────────────────────────────────────────┘
```

No RPC, no message queue, no database. A skill writes its result to three files
under `webapp/static/`, and the viewer polls them every two seconds.

The cost is a two-second polling delay. The payoff: skills can be written in any
language, the viewer survives a restart without losing state, any skill can be
tested on its own, and the agent can read those same files to answer "how far
along is it".

### 4.2 The publish contract: three files are the whole interface

| File | Contents | Written by |
|------|----------|------------|
| `current.cif` | The latest structure | `publish.py --structure` |
| `current.json` | Structure metadata (chains, roles, metrics, params, notes) | same |
| `status.json` | Task list, current step, progress message, pid | `publish.py --status` |

The `params` block in `current.json` exists specifically to stop the model
rummaging through history directories on every re-dock — box centre,
exhaustiveness and ligand SMILES as structured data, read directly next time.
That one field took a re-dock from 42 tool calls to about 20.

`publish.py` writes atomically (`.tmp` then rename), so the viewer never reads
half a file.

### 4.3 The agent panel: an opencode client

```
browser              serve.py              agent.py            opencode
  │  POST /agent        │                      │                   │
  ├────────────────────>│  agent.stream()      │                   │
  │                     ├─────────────────────>│  POST /session/id/prompt_async
  │                     │                      ├──────────────────>│
  │                     │                      │  GET /event (SSE) │
  │                     │                      │<──────────────────┤
  │  SSE: text/reasoning/tool/permission/waiting/done                │
  │<────────────────────┼──────────────────────│                   │
```

Four decisions that matter:

**Streaming, and only opencode's signal ends it.** A turn that runs a skill
takes minutes, and a panel showing nothing looks broken, so text, reasoning and
tool calls are forwarded as they happen. When it is over is decided only by
`session.idle` and `message.updated` + `finish` — every timing-based rule was
tried and each was half right: too short reports "no reply" while the model is
still working, too long makes every short answer feel stuck.

**Permission requests are forwarded to the user.** When the agent wants to run
something outside the allow list, opencode blocks. The request comes to the
panel over SSE and the user presses "allow once" or "reject". A global switch
approves everything, at the cost of the agent running any command unasked.

**Background jobs are watched by the panel.** See ② above. Implementation:
`stream()` checks `status.json` at the end and emits `waiting` if anything is
unfinished; the front end polls `/agent/jobs` every two seconds;
`_pid_alive(pid, starttime)` cross-checks `kill(pid, 0)` against the start time
in `/proc/<pid>/stat`; on wake-up the log is found by parsing `--out` out of
`/proc/<pid>/cmdline` and its tail is attached to the message.

**Sessions are never replaced automatically.** `ensure_session()` once created a
new session whenever the current one was busy, so a user asking "still running?"
mid-job had their message dropped into an empty conversation — context gone. The
choice now belongs entirely to the user: `＋` for new, `☰` for history, the stop
button to interrupt.

### 4.4 The viewer

`webapp/static/index.html`, about 8400 lines, rendered with 3Dmol.js. Three
panels on the left (metrics, sequence, selection), the agent panel on the right,
the 3D stage between them.

- **Tabs**: one per published structure, switchable and closable, state kept per tab
- **Selection modes**: residue / chain / atom / bond / pocket, sent with the question
- **Interchangeable backdrop**: four presets plus a custom image (the canvas goes
  transparent at alpha 0, the picture sits underneath)
- **Empty state**: a molecular-structure logo and one line of introduction

---

## 5. Agent Skills design

### 5.1 Shape

Each skill is a directory:

```
skills/binder-design/
├── SKILL.md           # YAML frontmatter (name + description) + Markdown body
└── scripts/
    ├── preflight.py   # Deploy kind: verify before running
    └── ...
```

The `description` in the frontmatter is the **routing key** — the agent decides
which skill to use from it. So it is written densely: what it does, when to use
it, when **not** to, and what it depends on. `molecular-docking`'s description
explicitly rules out "installing Vina (use vina-deploy)" and "generating the
ligand itself (use molecule-generation)".

### 5.2 Three kinds

**Deploy (8)** — each is "install + verify". `preflight.py` checks the
environment, imports, weights and GPU, with `--fix` where it can repair. The
principle is **verify before running**: a BoltzGen missing its weights fails
after twenty minutes, while preflight says so in ten seconds.

**Run (13)** — the whole chain from pocket finding to dynamics validation:

```
pocket-detection ──> pocket-molecule-design ──> molecular-docking
                         │                            │
                         ▼                            ▼
                    molecule-generation        molecular-dynamics
                         │
                         ▼
                    molecule-editing ──> sequence-design
```

Every run skill follows the same shape: **confirm parameters → report progress
→ execute → interpret → publish.**

**Publish (1)** — `structure-viewer` provides `publish.py`, the exit every other
skill goes through.

### 5.3 The shared layer

`skills/_shared/` holds what every skill uses. It is what stops "each skill
writes its own copy and they drift apart":

| File | Does |
|------|------|
| `biobench_paths.py` | Every path resolved from the machine, nothing hard-coded |
| `pdb_format.py` | PDB fixed-column read/write; a column that does not fit raises |
| `selection.py` | Reads the viewer's selection, resolves serials, builds the RDKit mol |
| `verify_structure.py` | Pre-publish structure verifier (13 checks; called by the reviewer subagent) |
| `silent-failures.md` | Thirteen silent failures that actually happened |
| `pytorch-cuda.md` | Which PyTorch wheel matches which driver |

### 5.4 Learning from the conversation

The skills were not written once. Every confusion the model ran into is recycled
into documentation:

| Confusion | Became |
|-----------|--------|
| Rumming five directories for re-dock parameters | `publish.py --params` + "read parameters from current.json" |
| Reading boltzgen source 18 times for a cyclic peptide | A correct yaml template + "don't read the source, the error names the fix" |
| 100 tool calls for one benzene edit | Overlap detection in the script + "check the shape before writing the script" + the full publish flag table |
| `sleep 280` waiting on an MD run | "End the turn once the job is backgrounded" in the system prompt |
| `pgrep -f` reporting a finished job as running | A new section in `silent-failures.md` |
| Publishing a 375,290-atom solvated frame | Publish the desolvated frame, with the script |
| Switching conversations looked like it paused the running task | `attach` mode in `agent.py`: subscribe to a turn in progress without disturbing it |
| An inverted guard in `agentTurn` made messages vanish silently | `silent-failures.md`: confirm the original semantics before changing an existing condition |
| Subagents existed but the model never dispatched to them | "Delegating is the default" as an explicit prompt rule, plus a calling contract each |

---

## 6. Deploying on local compute

### 6.1 Hardware and system

| Item | Value |
|------|-------|
| Machine | NVIDIA DGX Spark |
| GPU | NVIDIA GB10 |
| Memory | 121 GB unified (shared CPU/GPU) |
| Driver | 580.142 |
| CUDA | 13.0 |
| Architecture | aarch64 (ARM64) |

### 6.2 Steps

```bash
# 1. Clone
git clone https://github.com/luomu369/BioBench.git
cd biobench

# 2. Check the environment (add --fix to repair)
bash skills/viewer-launch/scripts/bootstrap.sh

# 3. Configure the model key
echo 'STEPFUN_API_KEY=sk-xxx' > webapp/agent.key

# 4. One command to start (downloads opencode on first run)
bash start.sh
#    → http://localhost:8282
```

`bootstrap.sh` checks: conda environments, CUDA driver version, whether the
PyTorch wheel matches the driver, whether each tool binary is on PATH, and
whether the model weights are in place. Every failure prints the command that
fixes it.

### 6.3 Environment layout

Eight conda environments, isolated by dependency conflict — the only way to keep
a set of mutually incompatible models on one machine:

| Env | Holds |
|-----|-------|
| `docking` | AutoDock Vina + meeko + RDKit + gemmi |
| `boltzgen` | BoltzGen + PyTorch (cu130) |
| `genmol` | GenMol |
| `resgen` | ResGen |
| `proteinmpnn` | ProteinMPNN |
| `openmm` | OpenMM + AmberTools + PDBFixer |
| `webapp` | The viewer server (standard library only) |
| `comfyui` | Unrelated (already on the machine) |

External tools live in `~/tools/`: `vina`, `foldseek`, `fpocket`, `mmseqs`,
`ResGen`. Model weights live in `~/models/`: `boltzgen-1` (18 GB), `genmol`
(1.4 GB), `proteinmpnn` (45 MB).

### 6.4 How the agent uses the local compute

```
user (browser)
  │ natural language + selected residues
  ▼
webapp/serve.py ──> webapp/agent.py ──> opencode (vendor/bin/opencode)
                                            │
                                            │ reads skills/*/SKILL.md
                                            │ generates bash commands
                                            ▼
                                      conda run -n <env> python scripts/xxx.py
                                            │
                                            │ calls the local GPU / CPU
                                            ▼
                                      BoltzGen / Vina / OpenMM / ...
                                            │
                                            │ writes webapp/static/
                                            ▼
                                      publish.py ──> viewer (2s poll)
```

The key point: **the agent never touches the GPU directly.** It generates shell
commands, executed by each environment's Python interpreter, and PyTorch/OpenMM
reach the GB10 through the CUDA 13.0 driver. So the agent needs no understanding
of CUDA versions, wheel compatibility or environment isolation — those are
handled in `preflight.py` and `pytorch-cuda.md`.

---

## 7. Model optimisation

### 7.1 Model choice

**StepFun `step-5-preview`**, over an OpenAI-compatible endpoint:

```jsonc
"provider": {
  "stepfun": {
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "https://api.stepfun.com/step_plan/v1",
      "apiKey": "{env:STEPFUN_API_KEY}"
    },
    "models": {
      "step-5-preview": { "limit": { "context": 1000000, "output": 65536 } }
    }
  }
}
```

Why: it is a **reasoning model**. It returns `reasoning` and `content`, and the
former is the working. On a biomolecular task, "which file it read, which
distance it computed" matters as much as the answer — an affinity number is only
trustworthy when you can see which `summary.json` it came from.

The viewer renders reasoning as a collapsible "thinking" block and tool calls as
a "tool calls" block, in the order: thinking → tools → answer. Reading it in that
order is following the reasoning through to its conclusion.

### 7.2 Context optimisation

**① The selection goes with the question.** The agent could read
`selection.json` itself, but that costs a tool call and a round trip — and on
"design a binder for this", the selection *is* the question. So
`agentSelectionSummary()` produces one line and attaches it to the message.

**② Run parameters as structured fields.** The `params` block in `current.json`
means a re-dock does not rummage through history directories — the first read of
`current.json` carries box centre, exhaustiveness and ligand SMILES.

**③ The message window has to be wide.** opencode's `?limit=N` returns the
**last** N messages, and a turn that ran a skill produces one assistant message
per step — a docking run with its checks is forty of them. `history()` and
`list_sessions()` both use `limit=200`; otherwise the user's question falls
outside the window and the conversation disappears from the list.

**④ The wake-up carries the log tail.** The model wakes with the last few lines
already in the message, so its first act is not reading a file.

**⑤ Heavy work goes to subagents.** `.opencode/agent/` defines four subagents
the main agent dispatches through the `task` tool: `structure-reviewer` verifies
a structure before it is published, `pocket-analyst` returns only aggregate
pocket descriptors, `docking-runner` returns only affinity and pose count,
`report-writer` only writes the summary. Each holds its own run's tool output
and the main agent receives the conclusion — the forty messages of a docking
sweep never enter the main context.

The dispatch rules live in `vendor/prompts/biobench.md` rather than in config:
`subagent_type` is a free string, opencode injects every agent's name and
description as a menu, and choosing is the model's business. Measured, the model
does not reach for them on its own — it would rather read the file itself. So
the prompt states "delegating is the default" as an explicit rule and gives each
subagent a calling contract (what to pass, what comes back, what it refuses to
give) rather than a "when X, spawn Y" routing table.

### 7.3 Tool-call optimisation

Where this project has spent the most effort. One benzene edit once took 100
tool calls, and the root cause was **information not being at hand**. The fixes:

| Means | Effect |
|-------|--------|
| Copyable templates in SKILL.md (cyclic-peptide yaml, the full publish flag table) | Saves 15–20 source reads |
| Scripts reporting anomalies first (overlap detection, serial validation) | Saves 50 blind investigations |
| "Check the shape before writing the script" in the docs | Saves 30 edit→run→fix loops |
| Ending the turn once a job is backgrounded | Saves 12 `sleep 280` calls |
| `--params` structured parameters | Saves 10 directory archaeology trips |

### 7.4 Permissions and safety

`opencode.jsonc` sets `bash: "ask"` — everything asks first. A panel switch
approves everything, suitable for an unattended long run, and is off by default.

`webapp/agent.key` holds the API key. It never reaches the browser (anything in
a static page is readable) and never enters git.

---

## 8. Tech stack

### 8.1 NVIDIA

| Item | Version / model |
|------|-----------------|
| Hardware | NVIDIA DGX Spark |
| GPU | NVIDIA GB10 (121 GB unified memory) |
| Driver | 580.142 |
| CUDA | 13.0 |
| PyTorch wheel | `cu130` |
| Compute platform | OpenCL / CUDA (OpenMM picks) |

### 8.2 StepFun

| Item | Value |
|------|-------|
| Model | `step-5-preview` |
| Endpoint | `https://api.stepfun.com/step_plan/v1` (OpenAI-compatible) |
| Integration | `@ai-sdk/openai-compatible` |
| Context | 1,000,000 tokens |
| Output | 65,536 tokens |
| Role | The agent's reasoning and tool calling (a reasoning model) |

### 8.3 Biomolecular models and tools

| Kind | Name | Does |
|------|------|------|
| Design | BoltzGen | Binder / ligand design (diffusion + structure prediction) |
| Sequence | ProteinMPNN | Sequence design on a fixed backbone |
| Generation | GenMol | Small-molecule generation (de novo / completion / linking) |
| Generation | ResGen | Pocket-conditioned molecule generation |
| Docking | AutoDock Vina | Docking and scoring |
| Dynamics | OpenMM + AmberTools | Molecular dynamics |
| Search | Foldseek | Structure search and clustering |
| Search | MMseqs2 | Sequence search |
| Pocket | fpocket | Binding-pocket detection |
| Chemistry | RDKit | Molecular graphs, geometry, SMILES |
| Structure | gemmi | mmCIF/PDB parsing |
| Preparation | meeko | Ligand and receptor preparation |

### 8.4 Software

| Kind | Name |
|------|------|
| Agent | opencode 1.18.32 (vendored) |
| 3D rendering | 3Dmol.js |
| Server | Python 3 standard library (`http.server`, no third-party dependencies) |
| Environments | conda / Miniconda3 |
| Spec | Agent Skills (`SKILL.md` + scripts) |

---

## 9. Skill list

| Skill | Role |
|-------|------|
| `biobench` (root) | Entry point — bootstrap, route, orchestrate |
| `viewer-launch` | Start/restart the viewer; environment bootstrap |
| `structure-viewer` | The publish contract for structures and progress |

**Deploy (8)**: `vina-deploy`, `boltzgen-deploy`, `proteinmpnn-deploy`,
`genmol-deploy`, `resgen-deploy`, `foldseek-deploy`, `mmseqs-deploy`,
`molecular-dynamics-deploy`

**Run (13)**: `molecular-docking`, `molecular-dynamics`, `pocket-detection`,
`binder-design`, `ligand-binding-design`, `pocket-molecule-design`,
`molecule-generation`, `molecule-editing`, `sequence-design`, `sequence-search`,
`structure-comparison`, `conformational-analysis`, `admet-prediction`

**Publish (1)**: `structure-viewer`

Skills follow the open Agent Skills spec and install on their own:

```bash
ln -s "$PWD/skills/binder-design" ~/.claude/skills/binder-design
```

---

## 10. State and limits

### 10.1 Numbers

| Item | Count |
|------|-------|
| Skills | 23 |
| Skill scripts (Python + shell) | ~9,000 lines |
| Skill docs (Markdown) | ~7,500 lines |
| Viewer + server | ~8,600 lines |
| Silent failures recorded | 13 |
| Subagents | 4 |
| conda environments | 8 |
| Model weights | ~20 GB |

### 10.2 Known limits

- **Subagent dispatch rests on the prompt, not on a mechanism**:
  `subagent_type` is a free string, choosing is the model's decision, and
  measured it would rather read the file itself. The calling contract in
  `vendor/prompts/biobench.md` is the backstop, but it is not enforced — a
  different model or harness can lower the dispatch rate.
- **`pocket-molecule-design` publishes several designs superimposed**: ten
  molecules in one place, which makes a later edit ambiguous. The script warns
  now; the publish side is not fixed.
- **Single user, single machine**: no multi-user, no remote deployment, no
  cluster scheduling.
- **The privacy boundary is not built yet**: the agent can `cat` any file on the
  machine, `agent.key` included. A subagent's permissions can be narrowed
  individually, but the main agent still has all of them. The split — a local
  model fetching data, a cloud model reasoning over abstractions — is verified
  feasible (opencode supports a per-agent `model` field) but not implemented.
- **Video demo**: hosted on Bilibili (link at the top). Gitee caps a single file
  at 100 MB, and a demo video in git would be downloaded on every clone while
  early versions stayed in the history forever.

### 10.3 In one sentence

**BioBench turns the biomolecular AI tools on one DGX Spark into a workbench you
can talk to in plain language — the agent runs, the viewer shows, and the skills
keep it from being wrong.**
