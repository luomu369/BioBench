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

VIEWER = Path(os.environ.get(
    "BIOBENCH_VIEWER",
    "/home/asus_gx10/biobench/webapp/static"
))

# How many past statuses to keep in status.json's history array. Enough to
# scroll back through a session's runs without the file growing without
# bound.
STATUS_HISTORY = 20

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
        for line in text.splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            resname = line[17:20].strip().upper()
            one = AA3TO1.get(resname)
            is_ligand = one is None
            if is_ligand and resname in ("HOH", "WAT", "DOD"):
                continue  # skip water
            note(line[21] or "_", line[22:27].strip(), one, is_ligand)
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
    # The index of the step that published, so the backfill can tell which
    # steps came before it. Falls back to 0 when the caller did not say,
    # which makes this publish the task's earliest structure.
    backfill_group_structures(meta["job"], meta["name"],
                              published_index=args.index or 0)
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
    target["structure"] = name
    # Marks this step as a source rather than a borrower. The backfill reads
    # the flag to tell the two apart, so an inherited structure is never
    # mistaken for one the step produced itself.
    target["published"] = True
    atomic_write(VIEWER / "status.json", json.dumps(data, indent=2))


def backfill_group_structures(group_id: str, name: str,
                              published_index: int = 0) -> None:
    """Give every step of a task a structure to show.

    A task's steps do not each produce a structure. Preparing a receptor,
    scoring a batch, analysing a trajectory — those steps have a finding but
    no file of their own, and the viewer's step rows are only clickable when
    they point at one. The result is a menu where some rows do nothing when
    clicked, which reads as a broken row rather than as a step that had
    nothing to publish.

    So a step that published nothing inherits a structure — but not blindly
    the newest one. A step shows the state of the work *at that point*:
    "preparing the receptor" belongs to the structure it was preparing, and
    "docking complete" belongs to the docked complex. Handing every step the
    final result would send the user to a structure the step had nothing to
    do with.

    The rule is therefore by position: a step takes the structure published
    by the latest step at or before its own index. Steps after the last
    publish take that one, which is the task's result and what they are
    working towards.
    """
    if not group_id:
        return
    data = load_status()
    jobs = data.get("jobs") or []
    in_group = [j for j in jobs
                if (j.get("group") or j.get("id")) == group_id]
    if not in_group:
        return

    # Structures this task has published, with the step index that made them.
    # Only steps that published their own count — a step that inherited one
    # is not a source, or the inheritance would feed itself.
    published = sorted((j.get("index") or 0, j["structure"]) for j in in_group
                       if j.get("structure") and j.get("published"))
    if not published:
        return

    changed = False
    for j in in_group:
        # A step that published its own structure keeps it. A step that only
        # inherited one is recomputed every time, because a later publish may
        # be the better match for it — the inheritance is a default, not a
        # decision the step made.
        if j.get("published"):
            continue
        idx = j.get("index") or 0
        # Latest publish at or before this step. If the step sits before
        # every publish, it takes the first one — the task's input, which is
        # what an early step is working on.
        candidates = [s for i, s in published if i <= idx]
        name_for_step = candidates[-1] if candidates else published[0][1]
        if j.get("structure") != name_for_step:
            j["structure"] = name_for_step
            changed = True

    if changed:
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


# How many past statuses to keep in status.json's history array. Enough to
# scroll back through a session's runs without the file growing without
# bound.
STATUS_HISTORY = 20

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


def load_status():
    """The current status file, or an empty one.

    Tolerates the old single-object format: a file written before jobs
    existed has no "jobs" key, and reading it as one is the difference
    between the viewer showing the last run and showing nothing.
    """
    path = VIEWER / "status.json"
    if not path.exists():
        return {"active": None, "jobs": [], "history": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"active": None, "jobs": [], "history": []}
    if "jobs" not in data:
        # Old format: the whole object was the active status.
        data = {"active": data, "jobs": [], "history": data.get("history") or []}
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
        # The pill is one line tall and the menu rows are narrow. A long
        # message is not truncated — it wraps, and the pill grows from a slim
        # bar into a block that covers the viewer. Warn rather than refuse:
        # the caller may have a reason, and a blocked publish is worse than a
        # tall pill.
        if len(target["message"]) > MSG_SOFT_LIMIT:
            print(f"warning: --message is {len(target['message'])} characters "
                  f"(soft limit {MSG_SOFT_LIMIT}). The status pill wraps and "
                  f"grows tall. Put the numbers in --metrics and keep the "
                  f"message to what happened.", file=sys.stderr)
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

    # History is the flat log of every status ever written. The viewer no
    # longer shows it — the job list covers what is on screen — but it costs
    # a few hundred bytes and is the only record of messages a job
    # superseded, so it stays for anything that wants to read the file.
    history = data.get("history") or []
    if target is not None:
        history.append(dict(target))
    data["history"] = history[-STATUS_HISTORY:]

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
    if args.structure:
        rc = publish_structure(args) or rc
    if args.select:
        rc = publish_selection(parse_highlight(args.select)) or rc
    if args.status:
        if not args.state:
            ap.error("--status requires --state")
        rc = publish_status(args) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
