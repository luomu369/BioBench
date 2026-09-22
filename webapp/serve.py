#!/usr/bin/env python3
"""BioBench Viewer server.

Serves the static viewer and accepts one write endpoint:

  POST /selection.json   — the viewer posts the user's residue selection here;
                           it is saved to selection.json for skills to read.

Everything else is plain static file serving. No framework, no dependencies
beyond the standard library.
"""

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
SELECTION = STATIC / "selection.json"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

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
