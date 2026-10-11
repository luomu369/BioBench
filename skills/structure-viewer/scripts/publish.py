#!/usr/bin/env python3
"""Publish a structure and/or progress to the BioBench Viewer.

Generic — any skill can call this. It knows nothing about BoltzGen, ESMFold,
or any specific model; it only writes the viewer's contract files.

Usage:
  # Report progress
  python3 publish.py --status --state running --step design \
      --index 1 --total 6 --message "Diffusion sampling"

  # Publish a structure (chains and metrics are read from the file)
  python3 publish.py --structure path/to/design.cif --name my_design \
      --source boltzgen --notes "Top-ranked of 4"

  # Both at once
  python3 publish.py --structure d.cif --status --state done \
      --step filtering --index 6 --total 6 --message "Published"

Exit codes: 0 ok, 1 bad input, 2 structure file unreadable.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The shared PDB column layout, so the chain list is read with the same field
# boundaries every other skill writes with. The viewer's location comes from
# the shared path module rather than a literal, so a clone anywhere works.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import viewer_static
from pdb_format import iter_atoms

VIEWER = Path(os.environ.get("BIOBENCH_VIEWER") or viewer_static())

AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M", "SEC": "U", "PYL": "O", "HYP": "P",
}


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so the viewer never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(text)
    os.replace(tmp, path)


def sniff_format(text: str) -> str:
    """Decide PDB vs mmCIF from the file's first meaningful line.

    The viewer's contract file is always named current.cif, so the source
    suffix says nothing about the content — a .pdb input lands there too.
    Trusting the suffix made parse_chains run the mmCIF branch over PDB
    text and return zero chains, which left the viewer with no chain
    information at all.
    """
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("data_", "_atom_site.", "loop_")):
            return "cif"
        if s.startswith(("HEADER", "CRYST1", "ATOM", "HETATM", "MODEL",
                         "REMARK", "SEQRES", "DBREF")):
            return "pdb"
    return "pdb"


def parse_chains(path: Path) -> list[dict]:
    """Extract per-chain residue counts from a PDB or mmCIF file.

    Both amino-acid residues and HETATM ligands are counted. A chain whose
    residues are mostly non-amino-acid gets ``"ligand": true`` so the viewer
    can style it differently.

    The test is "mostly", not "any": mmCIF files routinely put a small
    molecule in the same auth_asym_id as the protein it binds (7xwr has
    its ligand I2B inside chains A and B). Marking such a chain as a
    ligand would restyle the whole protein.
    """
    residues: dict[str, dict[str, str]] = {}
    ligand_counts: dict[str, int] = {}
    text = path.read_text(errors="replace")
    is_cif = sniff_format(text) == "cif"

    def note(chain: str, resi: str, one: str | None, is_ligand: bool) -> None:
        residues.setdefault(chain, {})[resi] = one or "X"
        if is_ligand:
            ligand_counts[chain] = ligand_counts.get(chain, 0) + 1

    if not is_cif:
        # Fields come from the shared parser. A slice that is off by one reads
        # a different residue name, and the chain list then labels a ligand as
        # an amino acid or the reverse.
        for atom in iter_atoms(text):
            resname = atom.resn.upper()
            one = AA3TO1.get(resname)
            is_ligand = one is None
            if is_ligand and resname in ("HOH", "WAT", "DOD"):
                continue  # skip water
            note(atom.chain or "_", str(atom.resi), one, is_ligand)
    else:
        cols: dict[str, int] = {}
        in_loop = False
        for line in text.splitlines():
            if line.startswith("_atom_site."):
                cols[line.strip().split(".", 1)[1]] = len(cols)
                in_loop = True
                continue
            if in_loop and line.startswith("#"):
                break
            if in_loop and line and not line.startswith("_"):
                parts = line.split()
                if len(parts) < len(cols):
                    continue
                resname = parts[cols.get("label_comp_id", 0)].upper()
                one = AA3TO1.get(resname)
                is_ligand = one is None
                if is_ligand and resname in ("HOH", "WAT", "DOD"):
                    continue
                chain = parts[cols.get("auth_asym_id",
                                       cols.get("label_asym_id", 0))]
                resi = parts[cols.get("auth_seq_id",
                                      cols.get("label_seq_id", 0))]
                note(chain, resi, one, is_ligand)

    out = []
    for c, r in residues.items():
        n_lig = ligand_counts.get(c, 0)
        # A chain is a ligand chain only when ligands are the majority of
        # its residues — a protein with a bound small molecule is still a
        # protein chain.
        if n_lig and n_lig * 2 > len(r):
            out.append({"id": c, "residues": len(r), "ligand": True})
        else:
            out.append({"id": c, "residues": len(r)})
    return out


def parse_highlight(spec: str) -> list[dict]:
    """"A:385,A:386,B:12" -> [{"chain": "A", "resi": 385}, ...].

    Residue numbers are ints when they look like one, so the viewer can
    match them against 3Dmol's numeric resi. Anything unparseable is
    dropped rather than failing the publish — a highlight is a courtesy,
    not part of the contract.
    """
    out = []
    for item in (spec or "").split(","):
        item = item.strip()
        if ":" not in item:
            continue
        chain, _, resi = item.partition(":")
        chain, resi = chain.strip(), resi.strip()
        if not chain or not resi:
            continue
        out.append({
            "chain": chain,
            "resi": int(resi) if resi.lstrip("-").isdigit() else resi,
        })
    return out


def publish_structure(args) -> int:
    src = Path(args.structure)
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 2

    text = src.read_text(errors="replace")
    fmt = sniff_format(text)
    chains = parse_chains(src)
    if not chains:
        print(f"warning: no chains parsed from {src} (detected {fmt}) — "
              "the viewer will show the structure without chain info",
              file=sys.stderr)

    # Optional role assignment: "A=target,B=binder"
    roles = {}
    for pair in (args.roles or "").split(","):
        if "=" in pair:
            cid, role = pair.split("=", 1)
            roles[cid.strip()] = role.strip()
    for ch in chains:
        if ch["id"] in roles:
            ch["role"] = roles[ch["id"]]

    meta = {
        "name": args.name or src.stem,
        "source": args.source or "unknown",
        "format": fmt,
        "chains": chains,
        "metrics": json.loads(args.metrics) if args.metrics else {},
        "notes": args.notes or "",
        "updated": utcnow_iso(),
    }
    # The run's parameters, as data rather than as a sentence in --notes.
    #
    # A repeat run needs the same box, the same exhaustiveness, the same
    # ligand — and the only place those lived was the prose of the previous
    # run's notes. Prose is not something to read a number out of: the agent
    # went looking for the previous run's directory and its log instead, which
    # is a dozen tool calls to recover what one field would have held. With
    # the numbers structured, the next run reads them off current.json in the
    # same step that reads the structure.
    #
    # Invalid JSON is a warning rather than a failure: the structure is still
    # worth publishing, and refusing to publish it over a malformed optional
    # field would lose the result to keep the metadata tidy.
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"warning: --params is not valid JSON ({e}); the run "
                  "parameters were not recorded", file=sys.stderr)
        else:
            if isinstance(params, dict):
                meta["params"] = params
            else:
                print("warning: --params must be a JSON object; the run "
                      "parameters were not recorded", file=sys.stderr)
    # Which task produced this. The viewer groups structures under the task
    # that made them, so a finished task is the way to reach its results —
    # without this a structure is only reachable through the flat tab list,
    # and the user has to remember which run it came from.
    #
    # Defaults to the skill name, matching how --job defaults, so a publish
    # and a status report from the same skill land in the same group without
    # either having to say so.
    meta["job"] = args.job or args.skill or ""
    highlight = parse_highlight(args.highlight)
    if highlight:
        meta["highlight"] = highlight

    atomic_write(VIEWER / "current.cif", text)
    atomic_write(VIEWER / "current.json", json.dumps(meta, indent=2))
    archive_structure(meta, text)
    # Which step produced this. A task publishes several structures over its
    # run — the target, the pocket, the design — and the task list's finished
    # steps each promise to show what they made. Without this the viewer can
    # only fall back to the task's first structure, so every step leads to
    # the same one.
    if args.step:
        record_step_structure(args, meta["name"])
    print(f"published structure: {meta['name']} "
          f"({len(chains)} chains, {fmt})")
    return 0


def record_step_structure(args, name: str) -> None:
    """Note that a step published a structure, so the viewer can find it."""
    data = load_status()
    jobs = data.get("jobs") or []
    group_id = args.job or args.skill or "job"
    target = None
    if args.step:
        target = next((j for j in jobs
                       if (j.get("group") or j.get("id")) == group_id
                       and j.get("step") == args.step), None)
    if target is None and args.index:
        target = next((j for j in jobs
                       if (j.get("group") or j.get("id")) == group_id
                       and j.get("index") == args.index), None)
    if target is None:
        return
    # Only the step that actually published gets a structure. The others
    # keep none: a step that prepared a receptor or scored a batch has a
    # finding but no file of its own, and inventing one for it sent the user
    # to a structure the step had nothing to do with.
    target["structure"] = name
    atomic_write(VIEWER / "status.json", json.dumps(data, indent=2))


# Where published structures are kept so the viewer can restore them.
ARCHIVE = VIEWER / "structures"
ARCHIVE_INDEX = VIEWER / "structures.json"
# How many to keep. A pipeline publishes a handful; a long session of
# separate runs publishes dozens. The cap is on structures, not runs, so it
# has room for several pipelines' worth.
MAX_ARCHIVE = 40


def archive_structure(meta: dict, text: str) -> None:
    """Keep every published structure so the viewer can reopen it.

    current.cif holds only the latest publish, so a reload of the viewer
    shows one structure and forgets the rest — and the task list's finished
    steps, which promise to show what each step produced, then lead nowhere.
    Archiving each publish under its own name and listing them in an index
    lets the viewer rebuild its tabs on startup.

    A republish of the same name overwrites its entry rather than adding a
    second one, matching how the viewer's tabs already behave.
    """
    try:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        name = meta["name"]
        # The name reaches a filename, so anything that is not safe in one is
        # not safe in the other. Replacing rather than rejecting keeps a
        # publish working; the display name in meta is untouched.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "structure"
        (ARCHIVE / f"{safe}.{meta['format']}").write_text(text)

        index = []
        if ARCHIVE_INDEX.exists():
            try:
                index = json.loads(ARCHIVE_INDEX.read_text())
            except (json.JSONDecodeError, OSError):
                index = []
        index = [e for e in index if e.get("name") != name]
        index.append({
            "name": name,
            "file": f"structures/{safe}.{meta['format']}",
            "meta": meta,
        })
        atomic_write(ARCHIVE_INDEX, json.dumps(index[-MAX_ARCHIVE:], indent=2))
    except OSError as e:
        # An archive that cannot be written costs the viewer its history, not
        # its current view — the publish itself has already succeeded.
        print(f"warning: could not archive structure: {e}", file=sys.stderr)


def publish_selection(residues) -> int:
    """Ask the viewer to select residues in the structure already on screen.

    selection.json is one-way — the viewer writes it, skills read it — so a
    skill that computes something the user should see (the residues within
    5 A of a click, say) has had no way to put it on screen. This writes a
    separate file the viewer polls for and applies to the loaded model.

    The residues are selected exactly as if the user had clicked them: same
    colours, same panel rows, same selection.json. The file is consumed by
    the viewer, which deletes it, so a stale request cannot re-apply later.
    """
    payload = {
        "residues": residues,
        "updated": utcnow_iso(),
    }
    atomic_write(VIEWER / "select.json", json.dumps(payload, indent=2))
    print(f"select: {len(residues)} residues requested")
    return 0


# How many jobs to keep in the job list. A declared plan becomes one job
# per step, so a six-step pipeline occupies six entries — the cap counts
# steps, not runs, and has to be generous enough to hold several pipelines'
# worth of them.
MAX_JOBS = 60

# Soft ceiling on a status message's length. The pill at the bottom of the
# screen is one line tall and the menu rows are narrow; past this the text
# wraps and the pill grows into a block that covers the viewer. A warning,
# not a limit — the caller may have a reason, and refusing the publish would
# be worse than a tall pill.
MSG_SOFT_LIMIT = 60


def utcnow_iso():
    """UTC now, with the zone spelled out.

    A bare isoformat() gives local time with no offset, and the browser
    reads it in *its* zone. On a machine set to UTC serving a browser set to
    GMT+0800 that puts every timestamp eight hours off, which turned a
    20-second job into "8h 0m". The trailing Z removes the ambiguity.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proc_starttime(pid):
    """When a process started, in clock ticks since boot, or None.

    Field 22 of /proc/<pid>/stat. Read after the last ')' because the comm
    field before it can itself contain spaces and parentheses, so counting
    fields from the front lands on the wrong column.
    """
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            raw = f.read()
        return int(raw[raw.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


# Log filenames the skills write, looked for inside a run's output directory.
#
# Wider than the viewer's own list, because the viewer's is what it can find
# *after* a process has gone and this one is what the run actually wrote. A
# docking run's log is vina.log; a pocket search's is fpocket.log. Missing a
# name here means the model wakes with no log tail and spends its first tool
# call reading the file, which is the thing the tail exists to avoid.
LOG_NAMES = ("md.log", "run.log", "gen.log", "dock.log", "boltzgen.log",
             "simulate.log", "vina.log", "fpocket.log", "prepare.log",
             "torsion.log", "admet.log", "search.log", "foldseek.log",
             "mmseqs.log", "resgen.log", "mpnn.log")


def proc_log(pid):
    """The log a running process is writing, or None.

    Read from /proc/<pid>/cmdline: the skills take `--out <dir>` and write
    their log inside it, so the directory is right there in argv. This is the
    only route that works — by the time anyone asks, the process is usually
    gone and /proc with it.

    Searched one level down as well as the directory itself, because the
    skills nest their output: a docking run writes docking_out/vina.log under
    the --out directory rather than vina.log directly in it.
    """
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read()
    except (OSError, ValueError, OverflowError):
        return None
    argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
    out = None
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
            break
        # `--out=dir` is the other form a script may accept. The prefix is six
        # characters — "--out=" — and slicing at five keeps the "=" and eats
        # the path's first character, which resolves to a directory that does
        # not exist and returns None as silently as having no --out at all.
        if a.startswith("--out="):
            out = a[len("--out="):]
            break
    if not out:
        return None
    base = Path(out)
    if not base.is_absolute():
        # Resolved against the repo root, which is where the viewer starts its
        # children and where the skills are run from. parents[3] is the root:
        # this file is skills/<skill>/scripts/publish.py, so 0=scripts,
        # 1=<skill>, 2=skills, 3=the repo.
        base = Path(__file__).resolve().parents[3] / base
    # The directory itself, then each immediate subdirectory. Two levels is
    # where the skills stop: docking_out/ and md_out/ are the deepest nesting
    # in use, and a recursive walk of a run directory finds every intermediate
    # file a long run leaves behind.
    for d in [base] + [p for p in sorted(base.glob("*")) if p.is_dir()]:
        for name in LOG_NAMES:
            cand = d / name
            if cand.is_file():
                return str(cand)
    return None


def load_status():
    """The current status file, or an empty one.

    Tolerates the old single-object format: a file written before jobs
    existed has no "jobs" key, and reading it as one is the difference
    between the viewer showing the last run and showing nothing.
    """
    path = VIEWER / "status.json"
    if not path.exists():
        return {"active": None, "jobs": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"active": None, "jobs": []}
    if "jobs" not in data:
        # Old format: the whole object was the active status.
        data = {"active": data, "jobs": []}
    return data


def publish_status(args) -> int:
    data = load_status()
    jobs = data.get("jobs") or []
    now = utcnow_iso()

    # A run is identified by --job, defaulting to the skill name. Everything
    # reported under one id belongs to one task, however many steps it has.
    group_id = args.job or args.skill or "job"

    # A declared plan becomes one job per step.
    #
    # A long run is a sequence of small tasks, and the ones already finished
    # are the record of what it did. Collapsing them into a single row with
    # a roadmap beside it hides everything but the step in progress — the
    # user cannot go back and look at what the run already produced. Each
    # step instead keeps its own start time, message and state, so the menu
    # reads as a list of things that happened rather than one thing that is
    # happening.
    plan = None
    if args.plan:
        steps = [s.strip() for s in re.split(r"[,>→]", args.plan)]
        plan = [s for s in steps if s]
        for i, name in enumerate(plan, 1):
            jid = f"{group_id}/{name}"
            existing = next((j for j in jobs if j.get("id") == jid), None)
            if existing:
                # Re-declaring a plan must not restart a step that already
                # ran, so only the structural fields move.
                existing["group"] = group_id
                existing["step"] = name
                existing["index"] = i
                existing["total"] = len(plan)
            else:
                jobs.append({
                    "id": jid,
                    "group": group_id,
                    "step": name,
                    "index": i,
                    "total": len(plan),
                    "state": "queued",
                    "skill": args.skill or "",
                    "message": "",
                    "started": now,
                    "updated": now,
                })

    # The step this report is about. Matched by name inside the task — that
    # is what a skill reports and what the plan was written with — then by
    # position, then by being the task's only step.
    target = None
    if args.step or args.index:
        in_group = [j for j in jobs
                    if (j.get("group") or j.get("id")) == group_id]
        if args.step:
            target = next((j for j in in_group
                           if j.get("step") == args.step), None)
        if target is None and args.index:
            target = next((j for j in in_group
                           if j.get("index") == args.index), None)
        if target is None and len(in_group) == 1:
            target = in_group[0]
        if target is None:
            # A step the plan did not name, or the first report of a run
            # that declared none. It becomes a step of its own rather than
            # overwriting an unrelated one.
            target = {
                "id": f"{group_id}/{args.step or ('step%d' % args.index)}",
                "group": group_id,
                "step": args.step or "",
                "index": args.index or 0,
                "total": args.total or 0,
                "started": now,
            }
            jobs.append(target)

    if target is not None:
        target["state"] = args.state
        target["updated"] = now
        target["message"] = args.message or ""
        # The process behind this step, when the caller named one.
        #
        # A step's own word for its state is the only thing status.json
        # otherwise carries, and a script that dies without writing `error`
        # leaves `running` there forever — the viewer waits on a run that
        # ended minutes ago. The pid is checkable against the OS, so the
        # viewer can tell "still going" from "said running and vanished".
        #
        # Cleared when the step is done or errored: a finished step has no
        # process to check, and a stale pid would eventually be reused by
        # something unrelated and read as this run still going.
        if args.pid:
            target["pid"] = args.pid
            # When that process started, so a reused pid is not mistaken for
            # this run. Linux recycles pids, and `kill(pid, 0)` on whatever
            # inherited the number succeeds — the viewer would wait forever
            # on a run that ended. The start time cannot collide: two
            # processes holding one pid existed at different times.
            start = proc_starttime(args.pid)
            if start is not None:
                target["pid_start"] = start
            # The log, recorded while the process is still here to ask.
            #
            # After the run ends there is nothing left to infer it from: the
            # pid is gone, /proc with it, and the group name has nothing to
            # do with the directory the skill wrote to. So the path is taken
            # now, from the process's own argv, and kept for the wake-up.
            log = args.log or proc_log(args.pid)
            if log:
                target["log"] = log
        elif args.state in ("done", "error", "idle"):
            # The pid goes — a finished step has no process to check, and a
            # stale one would eventually be reused by something unrelated and
            # read as this run still going. The log stays: it is what the
            # wake-up reads, and a finished run's log is the one thing about
            # it that is still worth having.
            target.pop("pid", None)
            target.pop("pid_start", None)
        elif args.state == "running":
            # A step that says it is running and names no process.
            #
            # Not refused — a script that reports progress without owning a
            # process is still better believed than ignored. But it is said
            # out loud, because the failure it leads to is invisible: the
            # viewer has nothing to check the step against, so a run that
            # dies without writing `error` sits at `running` until someone
            # notices. One real run did exactly that for eighteen minutes.
            #
            # `queued` is not warned about: it has not started yet, so there
            # is no process to name and nothing yet to check. Warning there
            # would fire on every declared plan.
            #
            # Only when the step does not already carry a pid from an earlier
            # call — a multi-step run reports the same pid on each step, and
            # warning about the ones that omit it would be noise.
            if not target.get("pid"):
                print("warning: --state running with no --pid. The viewer "
                      "cannot tell this run from a stalled one, so a step "
                      "that dies without reporting it will sit at 'running' "
                      "until someone notices. Pass --pid from "
                      "`setsid ... & echo $!`.",
                      file=sys.stderr)
        if args.log:
            target["log"] = args.log
        # The pill is one line and ellipsizes what does not fit, so a long
        # message is not lost — but it is not readable either, and the status
        # bar is the one thing that must stay glanceable while a run is going.
        # Warn rather than refuse: the caller may have a reason, and a blocked
        # publish is worse than an abbreviated message.
        if len(target["message"]) > MSG_SOFT_LIMIT:
            print(f"warning: --message is {len(target['message'])} characters "
                  f"(soft limit {MSG_SOFT_LIMIT}). The status pill shows one "
                  f"line and will ellipsize it. Put the numbers in --metrics "
                  f"and keep the message to what happened.", file=sys.stderr)
        if args.skill:
            target["skill"] = args.skill
        if args.step:
            target["step"] = args.step
        if args.index:
            target["index"] = args.index
        if args.total:
            target["total"] = args.total
        # A step's own numbers. The metrics panel reads them off the
        # structure on screen, so a step that published none — a sequence
        # search, a property analysis — has nowhere to show what it found.
        # Attaching them to the step puts them on its row instead, where the
        # finding belongs.
        #
        # --metrics is the one flag a skill has to remember. When the call
        # carries no --structure there is nothing for the panel to read, so
        # the same numbers go to the step instead of being dropped. Passing
        # --step-metrics explicitly still wins, for the rare call that
        # publishes a structure and also wants numbers on the step.
        step_metrics = args.step_metrics or (
            args.metrics if not args.structure else None)
        if step_metrics:
            try:
                target["metrics"] = json.loads(step_metrics)
            except json.JSONDecodeError as e:
                print(f"warning: --step-metrics is not valid JSON: {e}",
                      file=sys.stderr)
        target.setdefault("started", now)
        if args.state == "error" and args.message:
            target["error"] = args.message

    # Reaching a step means the ones before it are finished. A skill that
    # reports only its last step would otherwise leave every earlier one
    # waiting forever, and the menu would show a task that never started
    # next to one that did.
    reached = (target or {}).get("index") or 0
    if reached:
        for j in jobs:
            if j is target:
                continue
            if (j.get("group") or j.get("id")) != group_id:
                continue
            if (j.get("index") or 0) < reached and \
                    j.get("state") in ("queued", "idle", "running"):
                j["state"] = "done"
                j["updated"] = now

    # A task's display name. The id is what a skill passes to --job and what
    # a structure records as its task, so it has to stay short and stable;
    # the label is what the user reads.
    if args.label:
        groups = data.get("groups") or {}
        entry = dict(groups.get(group_id) or {})
        entry["label"] = args.label
        if plan:
            entry["total"] = len(plan)
        groups[group_id] = entry
        data["groups"] = groups

    data["jobs"] = jobs[-MAX_JOBS:]

    # The pill shows one job. Whatever is running wins — that is what the
    # user is waiting for. Otherwise the step this report just finished, so
    # the pill rests on a result; then a freshly declared plan, which the
    # user asked to see; then whatever was already there.
    running = [j for j in jobs if j.get("state") == "running"]
    finished = [j for j in jobs if j.get("state") in ("done", "error")]
    prev = (data.get("active") or {}).get("id")
    prev_live = next((j for j in jobs if j.get("id") == prev), None)

    if running:
        mine = [j for j in running
                if (j.get("group") or j.get("id")) == group_id]
        data["active"] = (mine or running)[-1]
    elif target is not None and args.state in ("done", "error"):
        data["active"] = target
    elif plan:
        data["active"] = next(
            (j for j in jobs if (j.get("group") or j.get("id")) == group_id),
            target or prev_live)
    else:
        data["active"] = prev_live or (finished[-1] if finished else None) \
            or target

    atomic_write(VIEWER / "status.json", json.dumps(data, indent=2))
    print(f"status: {args.state}"
          + (f" {args.index}/{args.total} {args.step}" if args.total else "")
          + (f" — {args.message}" if args.message else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--structure", help="path to a .cif/.pdb to publish")
    ap.add_argument("--name", help="display name (default: filename stem)")
    ap.add_argument("--source", help="producing skill or model")
    ap.add_argument("--notes", help="free-text context")
    ap.add_argument("--roles", help='chain roles, e.g. "A=target,B=binder"')
    ap.add_argument("--metrics", help="JSON object of metrics")
    ap.add_argument("--params",
                    help='JSON object of the run parameters, e.g. '
                         '{"box_center": [25.8, 90.0, 46.0], '
                         '"box_size": 20, "exhaustiveness": 8, '
                         '"num_modes": 9, "flexibility": "rigid", '
                         '"ligand_smiles": "c1ccc2c(c1)OCO2", '
                         '"ligand_chain": "re"}. Written to current.json '
                         'as a `params` block, so a repeat run reads the '
                         'numbers instead of reconstructing them from '
                         'prose or from the previous run\'s directory.')
    ap.add_argument("--step-metrics",
                    help='metrics for the step itself rather than the '
                         'structure, e.g. \'{"hits": 0}\'. Only needed when '
                         'a call publishes a structure and also wants '
                         'numbers on the step — a call with no --structure '
                         'sends --metrics there on its own.')
    ap.add_argument("--highlight",
                    help='residues to highlight on arrival, e.g. '
                         '"A:385,A:386,B:12". The viewer selects them as '
                         'if the user had clicked, so what a skill just '
                         'changed is visible without hunting for it.')
    ap.add_argument("--select",
                    help='residues to select in the structure already on '
                         'screen, e.g. "A:385,A:386". Same effect as '
                         '--highlight but applies to the loaded model '
                         'instead of waiting for the next publish.')

    ap.add_argument("--status", action="store_true", help="also write status")
    ap.add_argument("--state",
                    choices=["idle", "queued", "running", "done", "error"],
                    help="idle and queued both mean waiting; queued says so "
                         "more plainly when a plan has been declared")
    ap.add_argument("--step", help="current step name")
    ap.add_argument("--index", type=int, help="current step number")
    ap.add_argument("--total", type=int, help="total step count")
    ap.add_argument("--message", help="human-readable progress message")
    ap.add_argument("--pid", type=int,
                    help="process id of the background run this step drives, "
                         "from `setsid ... & echo $!`. Recorded in "
                         "status.json so the viewer can tell a finished run "
                         "from a stalled one by asking the OS rather than "
                         "believing the last thing the script said.")
    ap.add_argument("--log",
                    help="path to this run's log. Recorded so the viewer can "
                         "wake the model with the log's tail when the run "
                         "ends. Inferred from --pid when not given: the "
                         "process's own argv carries the --out directory the "
                         "skill writes its log into, and that is the only "
                         "route that still works once the process is gone.")
    ap.add_argument("--skill", help="skill name for attribution")
    ap.add_argument("--job",
                    help="identify this run so several can proceed at once. "
                         "Defaults to the skill name, so re-running a skill "
                         "updates its entry instead of adding a duplicate. "
                         "Pass a distinct value to run two of the same skill "
                         "side by side.")
    ap.add_argument("--plan",
                    help='declare the whole run up front, e.g. '
                         '"生成主链,折叠验证,亲和力评估,筛选排序". Each step '
                         'becomes its own entry in the viewer\'s task list, '
                         'so a finished step stays visible and clickable '
                         'instead of collapsing into a roadmap beside the '
                         'step in progress.')
    ap.add_argument("--label",
                    help='display name for the task identified by --job, e.g. '
                         '"GLP-1R 环肽设计". The id has to stay short and '
                         'stable — structures record it — so anything the '
                         'user should read goes here.')

    args = ap.parse_args()

    if not args.structure and not args.status and not args.select:
        ap.error("nothing to do: pass --structure, --select and/or --status")

    rc = 0
    # Status first, then the structure. `publish_structure` records which step
    # produced the structure it just published, and it finds that step by
    # looking it up in status.json — so the step has to be there already. Run
    # the other way round and the lookup finds nothing, the step's `structure`
    # field stays null, and every downstream thing that depends on it silently
    # does nothing: the viewer cannot tell which step made which structure, and
    # deleting a task cannot tell which structures were its to clear.
    if args.status:
        if not args.state:
            ap.error("--status requires --state")
        rc = publish_status(args) or rc
    if args.structure:
        rc = publish_structure(args) or rc
    if args.select:
        rc = publish_selection(parse_highlight(args.select)) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
