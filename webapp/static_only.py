#!/usr/bin/env python3
"""A read-only static server for the BioBench viewer, for exposing it safely.

The viewer's own server (webapp/serve.py) also serves /agent, which runs shell
commands on the user's behalf. That endpoint has no authentication of its own —
it is meant to be reached from localhost, where the browser is the only client.
Pointing a tunnel at it would put an unauthenticated shell on the public
internet.

This server serves the same static files and nothing else. No /agent, no
/select.json writes, no task endpoints. A structure viewer that cannot be
talked to is still a structure viewer, and that is what a phone on another
network needs.

Usage:
    python3 static_only.py --port 8283
"""
import argparse
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


class Handler(SimpleHTTPRequestHandler):
    """Serves static/ and refuses everything else.

    SimpleHTTPRequestHandler already normalises `..` out of a path, so the
    directory cannot be escaped. What it does not do is refuse the routes this
    server has no business answering, so they are named and 404'd here.
    """

    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/")
        # Anything that is not a file under static/ is not served. The viewer's
        # own endpoints — /agent, /agent/jobs, /stage-image, /task.json — are
        # absent rather than proxied, so there is no path from here to a shell.
        if route.startswith(("/agent", "/stage-image", "/task", "/sysinfo")):
            self.send_error(404, "not served by the read-only viewer")
            return
        super().do_GET()

    def do_POST(self):
        self.send_error(405, "read-only")

    def do_DELETE(self):
        self.send_error(405, "read-only")

    def end_headers(self):
        # The viewer polls every two seconds; without this a phone on a mobile
        # connection re-downloads the structure each time.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8283)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address. 127.0.0.1 by default: a tunnel should "
                         "reach this, not the network.")
    args = ap.parse_args()

    os.chdir(STATIC)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"read-only viewer on http://{args.host}:{args.port}/ "
          f"serving {STATIC}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
