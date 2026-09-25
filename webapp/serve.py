#!/usr/bin/env python3
"""BioBench Viewer server.

Serves the static viewer and accepts one write endpoint:

  POST /selection.json   — the viewer posts the user's residue selection here;
                           it is saved to selection.json for skills to read.

It also serves one read endpoint:

  GET /sysinfo.json      — CPU, GPU and memory utilisation, sampled on demand.

Everything else is plain static file serving. No framework, no dependencies
beyond the standard library.
"""

import json
import os
import re
import shutil
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
SELECTION = STATIC / "selection.json"


def _cpu_percent() -> float:
    """CPU utilisation from /proc/stat, measured across a short interval.

    A single read of /proc/stat gives cumulative counters since boot, which
    says nothing about what the machine is doing now. Two reads a fraction of
    a second apart give the delta, which does.
    """
    def read():
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = [int(v) for v in parts]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle

    try:
        t1, i1 = read()
        import time
        time.sleep(0.25)
        t2, i2 = read()
        dt = t2 - t1
        if dt <= 0:
            return 0.0
        return round(100.0 * (dt - (i2 - i1)) / dt, 1)
    except OSError:
        return 0.0


def _memory() -> dict:
    """Used and total RAM in GB, from /proc/meminfo."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0) / 1024 / 1024
        # MemAvailable is what the kernel can hand out without swapping;
        # MemFree alone would report a machine under load as nearly full.
        avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1024 / 1024
        return {
            "total_gb": round(total, 1),
            "used_gb": round(total - avail, 1),
            "percent": round(100.0 * (total - avail) / total, 1) if total else 0.0,
        }
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "percent": 0}


def _gpu() -> dict:
    """GPU utilisation and memory from nvidia-smi, if present.

    Returns an empty dict when there is no nvidia-smi or no NVIDIA GPU, so
    the viewer can hide the readout rather than show a permanent zero.

    Memory fields are optional: unified-memory parts such as the DGX Spark
    report `[N/A]` for memory.used/total because the GPU shares the system
    pool. Utilisation is still reported in that case — it is the number that
    says whether a run is actually on the GPU.
    """
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return {}
        line = out.stdout.strip().splitlines()[0]
        util, used, total = (v.strip() for v in line.split(","))
        info = {"util_percent": round(float(util), 1)}
        # "[N/A]" on unified-memory hardware; keep utilisation regardless.
        try:
            used_gb, total_gb = float(used) / 1024, float(total) / 1024
            info["mem_used_gb"] = round(used_gb, 1)
            info["mem_total_gb"] = round(total_gb, 1)
            info["mem_percent"] = round(100.0 * used_gb / total_gb, 1) if total_gb else 0.0
        except ValueError:
            pass
        return info
    except (subprocess.SubprocessError, ValueError, IndexError):
        return {}


def sysinfo() -> dict:
    return {
        "cpu_percent": _cpu_percent(),
        "memory": _memory(),
        "gpu": _gpu(),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") == "/sysinfo.json":
            body = json.dumps(sysinfo()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_DELETE(self):
        # Only select.json is deletable, and only by the viewer consuming a
        # request it has just applied. Without the delete the same request
        # would re-apply on every poll, re-selecting residues the user had
        # since dismissed by hand.
        route = self.path.split("?")[0].rstrip("/")
        if route == "/select.json":
            target = STATIC / "select.json"
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if route == "/task.json":
            self.delete_task()
            return
        self.send_error(404, "only DELETE /select.json and /task.json are supported")

    def delete_task(self):
        """Drop one task's steps, and the structures nothing else points at.

        The viewer's task list is the record of every run this session, and a
        finished one stays there forever. There was no way to clear a task the
        user is done with, so the menu grew into a history of everything
        rather than a list of what is currently open.

        Deleting the steps alone would leave their structures behind as tabs
        with no way to reach them, so the structures go too — but only the
        ones no surviving step references. A structure another task also
        published, or one the user opened by hand, stays.
        """
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        group = (req.get("group") or "").strip()
        if not group:
            self.send_error(400, "group is required")
            return

        status_path = STATIC / "status.json"
        try:
            data = json.loads(status_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.send_error(404, "no status file")
            return

        jobs = data.get("jobs") or []
        doomed = [j for j in jobs if (j.get("group") or j.get("id")) == group]
        if not doomed:
            self.send_error(404, f"no such task: {group}")
            return

        # Structures only this task referenced. A structure another surviving
        # step still points at is kept. The active pill's structure counts as
        # used too — unless the pill is describing a step of the task being
        # deleted, in which case it is going away with it.
        survivors = [j for j in jobs if (j.get("group") or j.get("id")) != group]
        still_used = {j.get("structure") for j in survivors if j.get("structure")}
        active = data.get("active") or {}
        active_id = active.get("id") or ""
        active_group = active.get("group") or active_id.split("/")[0]
        if active_group != group and active.get("structure"):
            still_used.add(active["structure"])

        orphaned = sorted({j.get("structure") for j in doomed
                           if j.get("structure")} - still_used)

        data["jobs"] = survivors
        (data.get("groups") or {}).pop(group, None)
        # The pill must not keep describing a task that no longer exists.
        # Fall back to the newest surviving step rather than to nothing: a
        # null active hides the pill entirely, and the user just deleted one
        # task out of several — the rest are still worth showing.
        if active_group == group:
            finished = [j for j in survivors
                        if j.get("state") in ("done", "error")]
            running = [j for j in survivors if j.get("state") == "running"]
            pick = (running or finished or survivors)
            data["active"] = pick[-1] if pick else None

        tmp = status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, status_path)

        # Drop the archived copies too, so a reload does not bring the task
        # back as a set of tabs with nothing pointing at them.
        index_path = STATIC / "structures.json"
        removed_files = []
        try:
            index = json.loads(index_path.read_text())
            entries = index if isinstance(index, list) else index.get("structures", [])
            kept = [e for e in entries if e.get("name") not in orphaned]
            if len(kept) != len(entries):
                if isinstance(index, list):
                    index_path.write_text(json.dumps(kept, indent=2))
                else:
                    index["structures"] = kept
                    index_path.write_text(json.dumps(index, indent=2))
                for name in orphaned:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "structure"
                    for ext in ("cif", "pdb"):
                        f = STATIC / "structures" / f"{safe}.{ext}"
                        if f.is_file():
                            f.unlink()
                            removed_files.append(f.name)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        body = json.dumps({
            "ok": True,
            "removed_steps": len(doomed),
            "removed_structures": orphaned,
            "removed_files": removed_files,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # CORS preflight. A POST carrying Content-Type: application/json is
        # not a "simple" request, so the browser asks first with OPTIONS —
        # and the base class answers 501, which fails the preflight and stops
        # the POST from ever being sent. The viewer's fire-and-forget write
        # swallows that, so selection.json silently stops updating while the
        # selection itself keeps working on screen.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") != "/selection.json":
            self.send_error(404, "only POST /selection.json is supported")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self.send_error(413, "selection too large")
            return

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return

        # Atomic write so a skill never reads a partial file.
        tmp = SELECTION.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, SELECTION)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def end_headers(self):
        # Allow the page to POST from any origin during local development.
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8282)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"== BioBench Viewer")
    print(f"   URL:        http://localhost:{args.port}")
    print(f"   Serving:    {STATIC}")
    print(f"   Selection:  POST /selection.json -> {SELECTION.name}")
    print(f"   Sysinfo:    GET /sysinfo.json (cpu / gpu / memory)")
    print(f"   Watching:   current.json / status.json (2s poll)")
    print()
    print("   Run a skill from your agent, e.g.:")
    print('     "design a binder around my selected residues"')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
