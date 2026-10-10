# Silent failures this codebase has already hit

Each of these produced output that looked plausible and was wrong. Nothing
errored. The file wrote, the viewer drew it, the number printed. They are
collected here because they are not specific to one skill — the same root
cause shows up wherever a structure is read, written, or handed to a tool.

A skill's own SKILL.md carries the traps specific to its chemistry. This file
carries the ones about the formats and the tools, which every skill shares.

## Fixed-column formats have no error checking

PDB and PDBQT are fixed-column, and every column boundary is implicit in an
f-string or a slice. Nothing validates them. A format string with one space
too many shifts the residue name into the atom-name field; a residue number
above 9999 overflows its four columns and pushes the chain ID into the residue
name; a serial that restarts at 1 in a file appended to another collides with
the first file's atoms. None of these raise — the file writes and a parser
reads a different molecule from the one that was meant.

Four of these happened in one session, in three scripts, because each had
written its own copy of the column layout.

**Use `_shared/pdb_format.py`.** It owns the layout for PDB: `atom_line`
refuses a value that would not fit rather than shifting the columns after it,
`iter_atoms` returns fields by name so no caller slices, and
`check_serials_unique` catches a duplicate serial or a CONECT naming an atom
that is not there. Run `python3 skills/_shared/pdb_format.py` after editing it.

**PDBQT is not covered yet.** Its element column moves: AutoDock's atom type
is one or two characters (`C`, `Cl`, `NA`, `OA`), and a two-character type
pushes the element one column right, so a 79-column line has `Cl` at
`[77:79]` and not `[76:78]`. Reading the fixed column returns `" C"` for a
chlorine and the atom comes out a carbon — invisible in the file, obvious on
screen where it renders the wrong colour. Read the element from the end of the
line, not from a column.

## `proximityBonding` bonds on distance alone

`Chem.MolFromPDBFile(..., proximityBonding=True)` is the only way to read a PDB
that has no CONECT records, and it bonds anything close enough. That includes
things that are not bonds:

- **Hydrogens on the selected group's own atoms** look like surviving
  neighbours. Bonding a new group to one leaves the real attachment dangling.
- **A protein side chain 1.3 Å from a ligand's amine** reads as a bond to it,
  and the new group is then bonded to the protein as well as to the ligand —
  drawing a line between two molecules that was never there.

Filter attachments to heavy atoms *and* to the same residue as the selection.

## meeko drops residues it cannot template, silently

`mk_prepare_receptor.py -a` (allow_bad_res) tells meeko to drop any residue it
cannot build a chemical template for, and it drops them without a word. A
crystal structure with disordered side chains reaches Vina with holes in the
binding pocket — and a ligand that fits a hole scores better than one that
does not.

One run scored **-10.7 kcal/mol** against a receptor missing a pocket
phenylalanine, and **-6.0** once that residue was restored. The score and the
picture disagreed because the complex was assembled from the input PDB while
Vina had docked against the PDBQT.

Three things follow:

1. **Parse the dropped list out of meeko's report** and carry it in the
   summary. `dock.py` writes `receptor_residues_dropped`.
2. **Build the complex from the PDBQT**, converted back — the receptor Vina
   actually used, hydrogens and all — not from the input PDB.
3. **Check the receptor for completeness before docking.** A residue missing
   its backbone N/C/O cannot be templated at all; one missing side-chain
   termini may be padded. `dock_flex.py` writes only side chains, so its
   output needs the backbone restored from the source structure before it can
   be docked against.

## A protein written as HETATM is not a protein

meeko's receptor preparer templates `ATOM` residues as protein and treats
`HETATM` ones as ligands. A protein written entirely as HETATM reaches the
docking step with whole side chains missing — it cannot template a PHE as a
ligand and drops it.

Write standard amino acids as `ATOM` and everything else as `HETATM`.
`_shared/pdb_format.py` takes a `record` argument; `replace_with_group.py`
picks it from `STANDARD_AA`.

## A cropped design context is not a publishable structure

`include_proximity` in a BoltzGen spec keeps only the residues within
`radius` of the hotspots and deletes the rest of every chain. It is a
design-time context crop and nothing more — but the design output is what
gets published, so the viewer ends up drawing the crop.

One 1RUZ peptide design ran with a 15 Å crop. The output was valid, the
metrics were real (ipTM 0.34, 4 hbonds, 3 saltbridges), the ring was
verified closed — and the receptor arrived as five chains in pieces: chain
B as 76-77/79-85/87-88/90-91, chain E as 103-111/265-268/301-303/309-310.
The user's first question was why the structure was broken. Nothing in the
pipeline had erred; the spec had asked for a context and the publish step
had shown it as a receptor.

The crop is worth having — on a trimer it is the difference between a
two-minute run and a forty-five-minute one. So the rule is not "never
crop", it is "never publish the crop":

1. **Design against the crop when speed matters, then rebuild the complex
   onto the full target.** The crop is a subset of the same file, so the
   retained residues carry the same coordinates: superimpose the design's
   target chains onto the full target, carry the designed chain across, and
   publish the full target plus the binder. The procedure is in
   `binder-design` Step 6.
2. **Check the designed chain against the residues the crop removed.**
   Those residues were absent when the pose was scored, so a clash with
   them means the merged picture is not the pose that was designed — a
   bigger `radius` or the whole target is the fix, and the design must not
   be published as sound. Waters are skipped: they are mobile and were
   usually cropped away en masse.
3. **Check the output against the source before publishing.** Every target
   chain of the *full* structure must arrive whole, at its residue count,
   with no interior gaps the source did not have. A chain the crop removed
   entirely is still published — from the full target, not from the design.

Two things the rebuild has to get right, both of which were wrong on the
first attempt: the design's chains are renumbered from 1 and the crop
leaves interior gaps, so the correspondence is by **order** (align the
residue-name sequences), never by residue number; and a trimer holds three
copies of the same sequence, so the mapping must be **one-to-one** or every
copy claims the same target chain.

## Vina's score is not a clash check

Vina's scoring function softens the van der Waals term, so a pose with real
overlap can still score well. "The affinity improved" and "the structure is
sound" are separate claims.

Check the clash directly — same-element heavy atoms closer than ~2.2 Å — and
say so when reporting a score. A score computed against a receptor with holes
in it is not comparable to one computed against a whole receptor, and neither
is comparable across a rigid and a flexible run.

## The status bar is a file, not a live view

`status.json` is written to disk and polled every 2 seconds. It outlives the
viewer, the shell and the reboot, so the pill at the bottom of the screen
shows the last thing *anyone* published — a smoke test, a manual
`publish.py` call, a run that finished hours ago — and it looks exactly like
a current one.

`publish.py` generates no status text of its own. `--message` is free text
stored verbatim, so a pill reading "全部通过" is a claim made by whoever
passed that flag, not a verdict any tool reached. One session left a
pipeline smoke test's message on the pill, and it was later read as the
state of the machine.

Check `active.updated` before reading the pill as progress, and say so when
it belongs to an earlier session. To clear it, publish something — there is
no reset flag, and the next `--status` call overwrites `active`. When you
publish to verify the pipeline works, word the message as what it was
("发布链路验证"), not as a conclusion.

## The viewer's atom serial is not the PDB serial

3Dmol's `atom.serial` is its own per-model counter, starting at 1 for every
model it builds. On a structure whose ligand is numbered 4075-4103 it reports
8, and a skill told "serial 8" edits the receptor's eighth atom instead of the
oxygen that was clicked. It agrees with the PDB serial whenever a structure's
atoms are numbered from 1, which is why the mistake survived as long as it
did.

Take the serial from `atom.pdbline`, which is the original PDB record.

## `pgrep -f` matches the command that is looking for it

`pgrep -f simulate_complex.py` matches any process whose *whole command line*
contains that string — including the shell running the pgrep itself, and
including the `bash -c` wrapper the agent's own tool call arrives in. A run
that finished an hour ago is reported as still running, and the panel waits on
work that ended long ago.

This is not hypothetical. A 1 ns simulation finished at 15:02 and wrote its
`final.pdb`; at 16:00 `pgrep -f simulate_complex.py` still returned a pid, and
the conclusion drawn was "the process is still going". It was the pgrep.

Check the pid the run reported, not a name:

```bash
kill -0 "$PID" 2>/dev/null && echo alive || echo gone
```

`kill -0` asks the kernel about one process and answers for that process only.
When the pid was recorded with `--pid`, `pending_jobs()` already does this —
and compares the process start time as well, because a pid is eventually
reused by something unrelated.

If a name search is unavoidable, exclude the searcher:

```bash
pgrep -f "simulate_complex" | grep -v "^$$\$"
```

which is still weaker than the pid, and says nothing about *which* run it
found.

## A file that was just listed can fail to open

`ls` shows `1ru7_F542_cycpep12.cif` at 810,582 bytes; the next command opens it
and gets `FileNotFoundError`. Nothing deleted it. The viewer's poll rewrites
`structures.json` and the archive index on a two-second cycle, and a publish
replaces files in place — so a path that existed when it was listed can be
gone, or momentarily mid-rename, when it is read.

`publish.py` writes atomically (`.tmp` then rename), which is why this is rare
and brief rather than corrupting. But "rare and brief" still costs a turn: the
model stops to work out what happened, re-lists the directory, and tries again
with an absolute path.

Read the file again. If it fails twice, something really did remove it — and
that is worth reporting. Once is the filesystem, twice is a fact.


## Switching away from a conversation is not a request to stop it

The chat panel's ＋ button starts a new conversation. It does not stop the one
being left. A design that takes twenty minutes should still be running when
the user comes back — aborting on switch would make ＋ a stop button in
disguise, and the work would be gone with nothing on screen to say so.

The first fix for the reported bug ("switching away pauses the conversation")
got this exactly backwards: `agentAfterSwitch()` was made to POST
`/agent/abort` whenever a turn was in flight. That stopped the turn the user
had asked to keep running. The symptom was real, but its cause was not a
missing abort — it was that the panel had no way to *re-attach* to a turn it
had stopped watching.

What the panel needs when it opens a conversation whose turn is still running:

1. **`/agent/session` returns `busy` and the `session` id.** The id is what
   the panel subscribes to; without it the panel knows a turn is running but
   not which one.
2. **`agent.stream(..., attach=sid)` watches without sending.** It replays
   only the turn in progress — walked back to the last user message, so the
   earlier turns the panel already rendered from `/agent/history` are not
   listed twice — then follows the event stream until `session.idle`.
3. **The transcript's last assistant block is filled in, not replaced.**
   Attaching reuses it; starting a second block below shows the same reply
   twice. A reused block must not be `remove()`d on the error paths either —
   it is history, not a placeholder this call owns.

Two traps in the replay, both found by testing:

- **The user's message is echoed through the same event stream** with the same
  shape as the assistant's. The "first messageID seen is the user's" rule
  works when starting a turn, but when attaching the echo went out long before
  the connection existed — so the id is read out of the session instead, and
  the first messageID seen is the assistant's reply being watched for. Getting
  this wrong puts the question where the reply belongs.
- **Only text and reasoning are replayed.** Tool calls are left to the event
  stream, which is already running and publishes each as it happens;
  replaying them lists the ones already made twice.

The stop button still aborts both ends — the fetch, so the panel stops
waiting, and `/agent/abort`, so opencode stops the model. That is a request to
stop. Switching conversations is not.

## A subagent cannot be run directly, and its whitelist is not the problem

`opencode run --agent pocket-analyst "..."` does not run the subagent. It
prints `agent "pocket-analyst" is a subagent, not a primary agent. Falling
back to default agent` and then runs `biobench` — whose `bash: ask` permission
auto-rejects in a non-interactive shell. Every command fails with
`The user rejected permission to use this specific tool call`, which reads
exactly like a broken whitelist in the subagent's own frontmatter.

It is not. The whitelist was never consulted, because the subagent never ran.

**Subagents are reached only through the `task` tool**, from a primary agent.
To test one, drive the primary agent and tell it to delegate:

```
opencode run --agent biobench "用 task 工具调用 pocket-analyst。任务：..."
```

Two more things that cost time here:

- **A newly added agent is not visible to a running server.** The primary
  agent globs for it, finds nothing, and reports the tool as unavailable.
  Restart `opencode serve` after adding an agent file.
- **A whitelist of script names alone is too narrow.** `structure-reviewer`
  only ever runs one script, so `"*verify_structure.py*": allow` is enough.
  An agent that has to *find* something first — which ligand is in this file,
  where does the pocket start — needs `grep`, `cat`, and `python3 -c` as well.
  Denying those makes it fail on the first step of its own task.

## A subagent is chosen by the model, and the model will not choose it

`subagent_type` is a free string, and opencode injects every agent's name and
`description` into the `task` tool's schema as a menu. So the routing is the
model's to make — nothing in the config pins a task to an agent.

Which is the problem. Asked to characterise a pocket, the main agent ran
`bash ls` and then `read` on the PDB file itself. Asked to re-dock a ligand,
same. It never called `task` once, in either case.

Two reasons, both confirmed:

- **The prompt's rules were unequal.** "Read before answering. `current.json`
  first" is unconditional; the subagent section asked it to weigh whether the
  answer was small and the work large. An unconditional rule beats a judgement
  call every time. The fix is to make delegating the stated default and to
  scope the read-first rule to *answering*, not to running.
- **It could not see what a subagent does.** It ran `Glob "**/pocket-analyst*"`
  and got 0 matches — the agent definitions live in `.opencode/agent/`, outside
  its search path. Faced with an agent it cannot inspect, it does the work
  itself. The fix is to put the calling contract in the prompt: what to pass,
  what comes back, and what the agent will refuse to give.

**Write the contract, not the route.** A table of "when X, spawn Y" is a
routing table the model follows mechanically and misapplies the moment a task
does not match a row. What generalises is: delegate when you need the
conclusion and not the data, pass paths plus one sentence on what the run was
for, and never silently overrule a NOT SOUND verdict.
