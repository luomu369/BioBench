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
