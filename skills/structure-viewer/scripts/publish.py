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
    print(f"published structure: {meta['name']} "
          f"({len(chains)} chains, {fmt})")
    return 0


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

# How many jobs to keep in the job list. Finished jobs are the ones worth
# keeping — a running job is already visible on the pill — so the cap is
# generous.
MAX_JOBS = 30


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
    status = {
        "state": args.state,
        "skill": args.skill or "",
        "step": args.step or "",
        "step_index": args.index or 0,
        "step_total": args.total or 0,
        "message": args.message or "",
        "updated": utcnow_iso(),
    }
    if args.state == "error" and args.message:
        status["error"] = args.message

    data = load_status()
    jobs = data.get("jobs") or []

    # A job is identified by --job, defaulting to the skill name. Two runs of
    # the same skill without --job are the same job, so re-running one
    # updates its entry rather than piling up duplicates — the same rule the
    # viewer's tabs follow.
    job_id = args.job or args.skill or "job"
    status["id"] = job_id

    existing = next((j for j in jobs if j.get("id") == job_id), None)
    if existing:
        # Keep the original start time so elapsed time survives a step
        # update; only the state and message move.
        status["started"] = existing.get("started") or status["updated"]
        # A plan is declared once and then carried forward. Re-declaring it
        # on every step update would be redundant, and dropping it would
        # lose the route map the moment the first step reported in.
        if existing.get("plan") and not args.plan:
            status["plan"] = existing["plan"]
        jobs[jobs.index(existing)] = status
    else:
        status["started"] = status["updated"]
        jobs.append(status)

    # The plan, if one was given. Split on commas, and also on ">" and "→"
    # so a route written either way parses the same.
    if args.plan:
        steps = [s.strip() for s in re.split(r"[,>→]", args.plan)]
        status["plan"] = [s for s in steps if s]

    # The pill shows one job. Priority: whatever is running beats whatever is
    # waiting, and a job that just finished beats one still queued — the user
    # wants to see the result of the work that just completed, not the next
    # thing in line.
    #
    # An explicit --job still wins outright: naming a job is how a skill says
    # "this is the one to look at", and second-guessing it would make the
    # flag useless.
    if args.job:
        data["active"] = status
    elif status["state"] == "running":
        data["active"] = status
    elif status["state"] in ("done", "error"):
        # Only take the front if nothing is running — a finished background
        # job must not displace the one still working.
        if not any(j.get("state") == "running" for j in jobs):
            data["active"] = status
    elif data.get("active") and data["active"].get("id") == job_id:
        # This job was on screen and has gone quiet; fall back to whatever is
        # still running, or to it if nothing is.
        still_running = next((j for j in reversed(jobs)
                              if j.get("state") == "running"), None)
        data["active"] = still_running or status
    elif status["state"] == "idle":
        # A newly queued job does not take the front on its own — it has not
        # done anything yet. It appears in the menu's 排队中 group.
        if not data.get("active"):
            data["active"] = status

    # Finished and failed jobs drop out of the active list once nothing is
    # running, so the pill does not sit on a stale "done" while a new job
    # waits behind it.
    if not any(j.get("state") == "running" for j in jobs):
        if data.get("active") and data["active"].get("state") != "running":
            newest = next((j for j in reversed(jobs)
                           if j.get("state") in ("done", "error")), None)
            if newest:
                data["active"] = newest

    data["jobs"] = jobs[-MAX_JOBS:]

    # History is the flat log of every status ever written. The viewer no
    # longer shows it — the job list covers what is on screen — but it costs
    # a few hundred bytes and is the only record of messages a job
    # superseded, so it stays for anything that wants to read the file.
    history = data.get("history") or []
    history.append(dict(status))
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
                         '"生成主链,折叠验证,亲和力评估,筛选排序". The '
                         'viewer shows the full route before anything has '
                         'run, and lights each step up as it is reached — '
                         'so a 25-minute job says what it is going to do '
                         'rather than revealing one step at a time.')

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
