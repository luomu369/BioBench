#!/usr/bin/env python3
"""BioBench Viewer server.

Serves the static viewer and accepts two write endpoints:

  POST /selection.json   — the viewer posts the user's residue selection here;
                           it is saved to selection.json for skills to read.
  POST /agent            — one turn of the chat panel's conversation, driven
                           through the vendored opencode server.

It also serves two read endpoints:

  GET /sysinfo.json      — CPU, GPU and memory utilisation, sampled on demand.
  GET /agent/status      — whether the agent is installed and running.

Everything else is plain static file serving. No framework, no dependencies
beyond the standard library.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STATIC = Path(__file__).resolve().parent / "static"
SELECTION = STATIC / "selection.json"
_SELECTION_LOCK = threading.Lock()


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

    # Modified 2026-11-06: validate bounded JSON bodies before dispatch.
    def _read_json(self, limit=1_000_000, empty=False):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            self.send_error(400, "unsupported request framing")
            return None
        value = lengths[0] if lengths else "0"
        if not value.isascii() or not value.isdigit():
            self.send_error(400, "invalid Content-Length")
            return None
        # Avoid Python's integer-string limit for arbitrarily long headers.
        digits = value.lstrip("0") or "0"
        if len(digits) > len(str(limit)):
            self.send_error(413, "request too large")
            return None
        length = int(digits)
        if length > limit:
            self.send_error(413, "request too large")
            return None
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw or (b"{}" if empty else b""))
        except (ValueError, UnicodeError):
            self.send_error(400, "invalid JSON")
            return None
        if not isinstance(data, dict):
            self.send_error(400, "JSON body must be an object")
            return None
        return data

    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/")
        if route == "/sysinfo.json":
            body = json.dumps(sysinfo()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # The viewer polls select.json every two seconds to see whether a
        # skill has asked it to select something. Almost always there is
        # nothing, and a 404 for that fills the console with errors that
        # look like a fault when nothing is wrong. An empty 204 says the
        # same thing without the noise.
        if route == "/select.json" and not (STATIC / "select.json").exists():
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        # The panel asks this on load so it can say "not installed" with the
        # command to fix it, rather than letting the user type a message into
        # a box that cannot answer.
        if route == "/agent/status":
            try:
                import agent
                installed = agent.configured()
                running = agent.server_up()
                auto = agent.auto_approve()
                # Whether the conversation the panel will open has a turn
                # running. A reload into a busy session has to show a stop
                # button, or the user is offered a send that queues behind a
                # turn already in progress.
                busy = agent.session_busy(agent.current_session_id())
            except ImportError:
                installed = running = auto = busy = False
            self._json(200, {"installed": installed, "running": running,
                             "auto_approve": auto, "busy": busy})
            return
        if route == "/agent/history":
            self.do_GET_history()
            return
        if route == "/agent/jobs":
            self.do_GET_jobs()
            return
        if route == "/agent/sessions":
            self.do_GET_sessions()
            return
        # The current conversation, and whether it has a turn running.
        #
        # The panel's resume check asks this every ten seconds: a turn started
        # by another tab, or by the server's own hourly backstop, is not one
        # this page knows about, and without the session id there is nothing
        # to attach to. It used to 404 — the route existed only under DELETE
        # and POST — so the check returned at once every time and a wake turn
        # landed in a transcript nobody was looking at.
        if route == "/agent/session":
            try:
                import agent
                sid = agent.current_session_id()
                busy = agent.session_busy(sid)
            except ImportError:
                sid, busy = None, False
            self._json(200, {"session": sid, "busy": busy})
            return
        super().do_GET()

    def do_GET_jobs(self):
        """The tasks still running, for the panel to watch.

        A turn that starts a background task ends before the task does — the
        skill scripts publish their progress to status.json and run for
        minutes. The panel polls this to know when to bring the model back,
        and it reads the same file the viewer already polls, so what the panel
        watches and what the model is waiting on cannot disagree.
        """
        try:
            import agent
            jobs = agent.pending_jobs()
            # The panel's watch is a two-second poll of this route, and the
            # backstop thread reads the timestamp of the last one: a panel
            # that polled recently is watching and will do its own waking, so
            # the server stays out of it. `?poll=0` is the resume check and
            # the idle sweep, which look without claiming to be the watch.
            if "poll=0" not in self.path:
                _last_jobs_poll[0] = time.time()
            # When nothing is running, the tail of what just finished.
            #
            # The panel asks this every two seconds while it watches, and the
            # answer that ends the watch is the one that matters: the model is
            # about to be woken, and the first thing it would do is read the
            # log to find out what happened. Sending the tail with the empty
            # list means it wakes up already knowing.
            #
            # Read here rather than in the panel because the panel does not
            # know where the logs live — that is the skills' business, and
            # agent.py is the side that already reads their status file.
            finished = []
            if not jobs:
                finished = agent.finished_jobs_tail()
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return
        self._json(200, {"jobs": jobs, "finished": finished})

    def do_GET_sessions(self):
        """Every conversation, newest first, with a line of its first message.

        The panel needs a list the user can pick from, and a session id is not
        something to recognise. The first user message is what the conversation
        was about, so that is the label.
        """
        try:
            import agent
            sessions = agent.list_sessions()
        except Exception as e:
            self._json(200, {"error": str(e), "sessions": []})
            return
        self._json(200, {"sessions": sessions})

    def do_GET_history(self):
        """The current session's turns, so a reload can rebuild the panel.

        The conversation lives in opencode's session store, which survives a
        reload of the page — but the panel's own copy of it was in memory and
        did not. Without this the user refreshes and finds an empty chat next
        to an agent that still remembers everything, which reads as the agent
        having lost the thread rather than the page.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return
        try:
            turns = agent.history()
        except Exception as e:
            self._json(200, {"error": str(e), "turns": []})
            return
        self._json(200, {"turns": turns})

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
        if route == "/agent/session":
            self.do_DELETE_session()
            return
        self.send_error(404, "only DELETE /select.json, /task.json and "
                             "/agent/session are supported")

    def do_DELETE_session(self):
        """Delete one conversation from opencode's store.

        The id comes as a query parameter rather than in a body, because a
        DELETE with a body is awkward to make from `fetch` and easy to get
        wrong. Deleting the session the panel is currently in leaves it
        pointing at nothing, so a fresh one is started in its place.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        sid = (q.get("id") or [""])[0].strip()
        if not sid:
            self._json(400, {"error": "no session id given"})
            return

        try:
            was_current = agent.current_session_id() == sid
            agent.delete_session(sid)
            if was_current:
                agent.new_session()
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True})

    def delete_task(self):
        """Drop one task's steps, and the archive entries nothing else points at.

        The viewer's task list is the record of every run this session, and a
        finished one stays there forever. There was no way to clear a task the
        user is done with, so the menu grew into a history of everything
        rather than a list of what is currently open.

        Deleting the steps alone would leave their structures behind as tabs
        with no way to reach them, so the archive entries go too — but only
        the ones no surviving step references. A structure another task also
        published, or one the user opened by hand, stays.

        The files under structures/ are not deleted. Clearing a task says
        what the user wants out of the way, not what is worth destroying; a
        structure that took twenty minutes to produce is not something to
        unlink because its task left the list. The entry goes, the tab goes
        with it, and the file stays for anyone who wants it back.
        """
        req = self._read_json(limit=1000000, empty=True)
        if req is None:
            return
        for field in ("group", "structure"):
            if req.get(field) is not None and not isinstance(req[field], str):
                self._json(400, {"error": f"{field} must be a string"})
                return
        group = (req.get("group") or "").strip()
        # A single structure, rather than a whole task. The structure
        # switcher's close button closes one tab, and closing it only in the
        # browser left the archive entry in place — a reload brought the tab
        # straight back, so the close button appeared not to work.
        structure = (req.get("structure") or "").strip()
        if not group and not structure:
            self.send_error(400, "group or structure is required")
            return

        status_path = STATIC / "status.json"
        try:
            data = json.loads(status_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.send_error(404, "no status file")
            return

        jobs = data.get("jobs") or []
        if structure:
            # One structure. The steps that published it stay — they are the
            # record of what the run did — but they no longer point at a
            # structure that exists, so their `structure` field is cleared.
            # A step left pointing at a deleted structure would render as a
            # clickable row that opens nothing.
            doomed = []
            survivors = jobs
            for j in jobs:
                if j.get("structure") == structure:
                    j["structure"] = None
            orphaned = [structure]
            active = data.get("active") or {}
            if active.get("structure") == structure:
                active["structure"] = None
                data["active"] = active
        else:
            doomed = [j for j in jobs
                      if (j.get("group") or j.get("id")) == group]
            if not doomed:
                self.send_error(404, f"no such task: {group}")
                return
            survivors = [j for j in jobs
                         if (j.get("group") or j.get("id")) != group]
            # Structures only this task referenced. A structure another
            # surviving step still points at is kept. The active pill's
            # structure counts as used too — unless the pill is describing a
            # step of the task being deleted, in which case it is going away
            # with it.
            still_used = {j.get("structure") for j in survivors
                          if j.get("structure")}
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
            # null active hides the pill entirely, and the user just deleted
            # one task out of several — the rest are still worth showing.
            #
            # The check covers an active that is already null too. A null
            # active has no group, so comparing groups alone would skip the
            # fallback and leave the pill hidden even though tasks survive —
            # which is how the status bar disappeared outright.
            if active_group == group or not active:
                finished = [j for j in survivors
                            if j.get("state") in ("done", "error")]
                running = [j for j in survivors
                           if j.get("state") == "running"]
                pick = (running or finished or survivors)
                data["active"] = pick[-1] if pick else None

        tmp = status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, status_path)

        # Drop the archive entries, so a reload does not bring the task back
        # as a set of tabs with nothing pointing at them.
        #
        # The files themselves stay on disk. Deleting a task is a statement
        # about what the user wants to see in the viewer, not about what is
        # worth keeping — a structure that took twenty minutes to dock is not
        # something to destroy because its task was cleared from the list.
        # The entry goes, the tab goes with it, and the file remains under
        # structures/ for anyone who wants it back.
        #
        # Anything no surviving step references goes too, not just what this
        # task published. The archive only ever grew: a publish added an entry
        # and nothing removed one, so a session that ran a dozen pipelines
        # left a dozen structures in the switcher with one task in the status
        # list. A structure a step still points at stays, and so does one with
        # no job at all — that is a structure the user opened by hand, an RCSB
        # fetch or a file they dropped in, and it was never any task's to
        # delete.
        index_path = STATIC / "structures.json"
        try:
            index = json.loads(index_path.read_text())
            entries = index if isinstance(index, list) else index.get("structures", [])
            # Read after the jobs were rewritten above, so this is the pill
            # that will actually be showing once this request is done.
            referenced = {j.get("structure") for j in survivors
                          if j.get("structure")}
            shown = (data.get("active") or {}).get("structure")
            if shown:
                referenced.add(shown)

            def keep(e):
                if e.get("name") in orphaned:
                    return False
                # No job: opened by hand, not produced by a task.
                if not (e.get("meta") or {}).get("job"):
                    return True
                return e.get("name") in referenced

            kept = [e for e in entries if keep(e)]
            if len(kept) != len(entries):
                if isinstance(index, list):
                    index_path.write_text(json.dumps(kept, indent=2))
                else:
                    index["structures"] = kept
                    index_path.write_text(json.dumps(index, indent=2))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        body = json.dumps({
            "ok": True,
            "removed_steps": len(doomed),
            "removed_structures": orphaned,
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
        route = self.path.split("?")[0].rstrip("/")
        if route == "/agent":
            self.do_POST_agent()
            return
        if route == "/agent/permission":
            self.do_POST_permission()
            return
        if route == "/agent/auto-approve":
            self.do_POST_auto_approve()
            return
        if route == "/stage-image":
            self.do_POST_stage_image()
            return
        if route == "/agent/clear":
            self.do_POST_clear()
            return
        if route == "/agent/abort":
            self.do_POST_abort()
            return
        if route == "/agent/session":
            self.do_POST_session()
            return
        if route != "/selection.json":
            self.send_error(404, "only POST /selection.json, /agent, "
                                 "/agent/permission, /agent/auto-approve, "
                                 "/stage-image, /agent/clear, /agent/abort "
                                 "and /agent/session are supported")
            return

        data = self._read_json(limit=1000000, empty=False)
        if data is None:
            return

        # Atomic write so a skill never reads a partial file.
        # A unique temporary file prevents concurrent selections sharing a name.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=SELECTION.parent, suffix=".tmp",
                                         delete=False) as f:
            tmp = Path(f.name)
            json.dump(data, f, indent=2)
        try:
            # Windows may briefly deny replacement while another writer or
            # a reader holds the destination. Serialize writers and bound retries.
            with _SELECTION_LOCK:
                for attempt in range(6):
                    try:
                        os.replace(tmp, SELECTION)
                        break
                    except PermissionError:
                        if os.name != "nt" or attempt == 5:
                            raise
                        time.sleep(0.01 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_POST_agent(self):
        """One turn of the chat panel's conversation.

        The page sends the messages so far; this drives the vendored opencode
        server and returns the reply plus which tools it called. The model's
        API key stays in opencode's own credential store — the page is static,
        so anything handed to it would be readable by anyone with the URL.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        data = self._read_json(limit=1000000, empty=False)
        if data is None:
            return

        # Watching a turn that is already running, rather than starting one.
        #
        # The panel sends this when it switches back to a conversation whose
        # turn is still going: the work continued in opencode while the user
        # was elsewhere, and coming back has to show it live. Nothing is sent
        # to the model, so the turn is not disturbed and no second turn is
        # queued behind it.
        #
        # Read before the messages check, because an attach carries no
        # message — there is nothing to send, which is the whole point.
        attach = data.get("attach")
        if attach is not None and not isinstance(attach, str):
            attach = None

        messages = data.get("messages")
        if not attach and (not isinstance(messages, list) or not messages):
            self._json(400, {"error": "no messages"})
            return
        if attach:
            messages = []

        # Which agent to use. The panel offers a read-only mode for questions
        # about the structure and a full one that can run skills; the choice
        # is the user's, so it comes in the request rather than being decided
        # here.
        which = data.get("agent")
        if which not in (None, "biobench", "biobench-readonly"):
            which = None

        # Streamed unless the caller asks otherwise. A turn that runs a skill
        # takes minutes, and a panel that shows nothing until it finishes
        # looks broken — the model emits text the whole time, so it is
        # forwarded as it arrives. `?stream=0` keeps the old one-shot shape
        # for scripts that want a single JSON document.
        if data.get("stream") is False or "stream=0" in self.path:
            try:
                reply, reasoning, used = agent.chat(messages, agent=which)
            except Exception as e:
                self._json(200, {"error": str(e)})
                return
            self._json(200, {"reply": reply, "reasoning": reasoning,
                             "tools": used})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for ev in agent.stream(messages, agent=which, attach=attach):
                line = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                self.wfile.write(line.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the user navigated away; nothing to report to
        except Exception as e:
            try:
                line = "data: " + json.dumps(
                    {"type": "error", "message": str(e)},
                    ensure_ascii=False) + "\n\n"
                self.wfile.write(line.encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST_abort(self):
        """Stop the turn that is running.

        Closing the browser's end of the stream is not enough: opencode keeps
        going, and the next message would queue behind a turn the user already
        gave up on. So the session is aborted here as well, which is what
        actually stops the model.
        """
        try:
            import agent
            agent.abort_turn()
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True})

    def do_POST_session(self):
        """Switch to another conversation, or start a new one.

        `{"id": null}` starts fresh; an id switches to that one. Either way the
        panel is rebuilt from the session that is now current, so what the user
        sees and what the agent will answer from are the same conversation.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        data = self._read_json(limit=100000, empty=False)
        if data is None:
            return

        sid = data.get("id")
        if sid is not None and (not isinstance(sid, str) or not sid.strip()):
            self._json(400, {"error": "id must be a non-empty string or null"})
            return
        try:
            if sid:
                agent.use_session(sid)
            else:
                agent.new_session()
            turns = agent.history()
            # Whether the conversation just switched to has a turn running.
            # The panel's send button is a stop control while one is, and a
            # send control when it is not — so switching to a busy
            # conversation has to put the button in its stopping state, or the
            # user presses send on a session that cannot accept a message and
            # the click does nothing.
            #
            # The session id goes with it. A busy conversation is one whose
            # turn is still running, and the panel subscribes to it so the
            # work is shown live rather than as the snapshot taken when the
            # user switched away.
            current = agent.current_session_id()
            busy = agent.session_busy(current)
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True, "turns": turns, "busy": busy,
                         "session": current})

    def do_POST_clear(self):
        """Forget the conversation, in the panel and in opencode.

        Clearing only the panel would leave the session in opencode's store,
        and the next reload would restore exactly what the user just asked to
        get rid of — which makes the button look broken.
        """
        try:
            import agent
            agent.clear_session()
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True})

    def do_POST_permission(self):
        """Answer a permission request the agent is blocked on.

        The agent asks before running anything the rules do not cover, and it
        waits. A request nobody answers is a turn that never ends — the panel
        shows a running agent and the user sees a hang with no explanation.
        So the request is surfaced and the answer comes back here.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        data = self._read_json(limit=100000, empty=False)
        if data is None:
            return

        response = data.get("response")
        # Two answers, matching the two buttons. "always" used to be accepted
        # here and was removed with the button that sent it: the auto-approve
        # switch in the panel header is that decision made once for the whole
        # session, and a per-command "always" beside it was a second control
        # covering part of the same ground.
        if response not in ("once", "reject"):
            self._json(400, {"error": "response must be once or reject"})
            return

        try:
            agent.answer_permission(data.get("session"), data.get("id"),
                                    response)
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True})

    def do_POST_auto_approve(self):
        """Turn automatic approval of permission requests on or off.

        With it on, the agent's requests are answered "always" the moment they
        arrive instead of being shown — a run that needs nobody at the
        keyboard. With it off, every request is surfaced as before.

        The state is read back from the module rather than echoed from the
        request, so the response says what is actually in force: a switch that
        reports success while the file it writes to is unwritable would be
        lying about the one thing the user needs to know.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        data = self._read_json(limit=1000, empty=True)
        if data is None:
            return

        on = data.get("on")
        if not isinstance(on, bool):
            self._json(400, {"error": "on must be true or false"})
            return

        state = agent.set_auto_approve(on)
        self._json(200, {"ok": True, "auto_approve": state})

    def do_POST_stage_image(self):
        """Set the viewer's backdrop to an image the user chose.

        The image arrives as a data URL because that is what a file input
        gives without a round trip through a form: the browser has already
        read the bytes, and posting them as a string keeps this to one
        request. It is decoded and written under webapp/static/, which is
        where the viewer already serves its own assets from — so the file is
        reachable at a plain URL and needs no second endpoint to read back.

        An empty `image` clears it and puts the colour backdrop back.
        """
        try:
            import agent
        except ImportError as e:
            self._json(500, {"error": f"agent module unavailable: {e}"})
            return

        data = self._read_json(limit=24000000, empty=True)
        if data is None:
            return

        image = data.get("image")
        if image is not None and not isinstance(image, str):
            self._json(400, {"error": "image must be a string or null"})
            return
        image = image or ""
        try:
            url = agent.set_stage_image(image)
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True, "url": url})

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Allow the page to POST from any origin during local development.
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


# The backstop's memory, in one place because two functions share it: the
# route stamps the last panel poll, the thread reads it.
_last_jobs_poll = [0.0]

# How long ago a run may have ended and still be worth a wake for. A day,
# not half an hour: a run that finishes while the user is away should still
# be reported when they come back, and only a genuinely old leftover is
# noise. Measured from when the run ended — see `_ended_recently` — not from
# when the model last published progress, which for a long run is when it
# started.
RECENT_SECONDS = 24 * 60 * 60

# How long a run may go before the model is asked to look at it.
#
# A design or a simulation leaves the model's turn ended an hour ago. The
# panel shows the live step, but the model is not watching, and a run that
# has quietly stalled — a crashed worker, a full disk — looks exactly like
# one that is progressing. The nudge is a turn, not a notification: the
# model reads the log and reports, so a stall is caught by something that
# can say so.
NUDGE_SECONDS = 60 * 60


def _ended_recently(group, pid, updated, window=RECENT_SECONDS):
    """Whether a run ended recently enough to be worth a wake.

    The reference is when the RUN ended, not when the step last published.
    A long run publishes nothing while it goes — BoltzGen does not write
    status.json, the model publishes at the start and again at the end — so
    a four-hour design's step carries a two-hour-old stamp by the time it
    finishes. Judging by that stamp refused to report exactly the runs the
    wake exists for: one ran four hours, finished, and sat unreported for
    nine.

    The log file's mtime is the run's own last write, which is when it
    ended. Without a log, fall back to the step's stamp, and without either
    believe the job rather than drop it.
    """
    stamp = None
    try:
        import agent
        path = agent._find_log(group, pid)
        if path:
            stamp = Path(path).stat().st_mtime
    except Exception:
        stamp = None
    if stamp is None and updated:
        try:
            from datetime import datetime
            t = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            stamp = t.timestamp()
        except ValueError:
            return True  # an unparseable stamp is not evidence of age
    if stamp is None:
        return True  # nothing to go on; believe it rather than drop a run
    return (time.time() - stamp) < window


def _wake_backstop():
    """Wake the model for a finished job the panel is not watching.

    The panel wakes the model when a watched job's process disappears. That
    watch lives in the browser: it starts when a turn ends with a `waiting`
    event and it dies with the page — a reload, a closed tab, a dropped
    connection, a conversation switched away from and not returned to. The
    run then finishes with nothing to report it, and the model's last words
    ("I'll report when it's done") are a promise nothing kept. Two runs in
    one session sat unreported for a quarter of an hour before the user
    asked.

    This thread is the server-side half of the same promise. It reads the
    same status.json, applies the same test `pending_jobs()` applies — a step
    still marked running whose recorded pid is gone has ended whether or not
    anyone wrote `done` — and sends the same wake message the panel would
    have sent.

    It stays out of the way when the panel is on it, by two rules:

      * a panel that is watching polls /agent/jobs every two seconds, and the
        timestamp of the last poll is the difference between "watching" and
        "absent". The resume and idle checks poll with `?poll=0` so they do
        not count as the watch.
      * a job that ended moments ago is given a few seconds before it is
        acted on, because the panel's own wake — a fetch, then a turn — takes
        a moment to show up as a busy session. Without the grace both sides
        wake for one run.

    Once per group, not once per step: a run publishes six steps and the
    process behind them is one, so six endings are one thing to report.
    """
    woken_groups = set()
    ended_at = {}
    pending_since = {}   # group -> when it was first seen still running
    last_nudge = {}      # group -> when it was last nudged
    while True:
        time.sleep(2)
        try:
            import agent
            status = json.loads(agent.STATUS_FILE.read_text())
        except Exception:
            continue  # between restarts, or a status file mid-rename
        jobs = [j for j in (status.get("jobs") or []) if isinstance(j, dict)]
        active = status.get("active")
        if isinstance(active, dict):
            jobs.append(active)

        now = time.time()
        try:
            pending = agent.pending_jobs()
        except Exception:
            continue
        live = {(j.get("group") or j.get("id") or "") for j in pending}

        # ---- hourly progress nudge -------------------------------------
        # Only for runs that are still going, once per hour per group, and
        # never while a turn is running — a nudge that landed on top of the
        # model's own reply would queue behind it and report nothing.
        if live:
            try:
                sid = agent.current_session_id()
                busy = bool(sid) and agent.session_busy(sid)
            except Exception:
                busy = False
            for g in live:
                first = pending_since.setdefault(g, now)
                if now - first < NUDGE_SECONDS:
                    continue
                if now - last_nudge.get(g, 0.0) < NUDGE_SECONDS:
                    continue
                if busy:
                    print(f"[nudge] {g}: skipped, a turn is running",
                          flush=True)
                    continue
                last_nudge[g] = now
                elapsed = now - first
                print(f"[nudge] {g}: firing after {_age_text(elapsed)}",
                      flush=True)
                threading.Thread(target=_send_nudge, args=(g, elapsed),
                                 daemon=True).start()
        for g in list(pending_since):
            if g not in live:
                del pending_since[g]  # the run ended; the next one starts fresh

        if pending:
            continue  # something is still running; not the moment to wake
        # Nothing is running. Every step still marked running or queued is
        # one whose process is gone — the same conclusion pending_jobs()
        # reached, read off the same file. A step with no pid recorded is
        # believed by that function and so keeps this loop out, exactly as
        # it keeps the panel waiting.
        ended = {}
        seen_ids = set()
        for j in jobs:
            if j.get("state") not in ("queued", "running"):
                continue
            # The active step and the jobs list hold the same run twice — a
            # publish writes both — so the same id can arrive twice and the
            # name would be reported as "step、step".
            jid = j.get("id") or ""
            if jid and jid in seen_ids:
                continue
            seen_ids.add(jid)
            # And only what ended recently. A step still marked running whose
            # pid died a day ago is a leftover, not a report anybody is
            # waiting for; without this the first tick after every restart
            # wakes the model for every test job ever left behind. Recency is
            # measured from the run's end — see `_ended_recently` — because a
            # long run's own status stamp is hours old the moment it stops.
            g = j.get("group") or j.get("id") or ""
            if not _ended_recently(g, j.get("pid"), j.get("updated")):
                continue
            ended.setdefault(g, []).append(j)
            ended_at.setdefault(g, now)
        if not ended:
            continue
        if now - _last_jobs_poll[0] < 6:
            continue  # the panel is watching; it will wake for these

        try:
            sid = agent.current_session_id()
            busy = bool(sid) and agent.session_busy(sid)
        except Exception:
            sid, busy = None, False
        if busy:
            continue  # a turn is already running — someone is reporting

        for g, js in ended.items():
            if g in woken_groups:
                continue
            if now - ended_at.get(g, now) < 8:
                continue  # grace: the panel's wake may be mid-start
            woken_groups.add(g)
            names = "、".join(
                (j.get("step") or j.get("group") or j.get("id") or "")
                for j in js if (j.get("step") or j.get("group") or j.get("id")))
            threading.Thread(target=_send_wake, args=(g, names or "任务"),
                             daemon=True).start()


def _running_log_tail(group):
    """The tail of a still-running job's log, or "".

    `finished_jobs_tail` only reports jobs that have ended — by design,
    since that is what the completion wake needs. A nudge is sent while the
    run is going, so its log is found here instead: the same status.json,
    the same `_find_log` fallback the rest of the machinery uses.
    """
    try:
        import agent
        status = json.loads(agent.STATUS_FILE.read_text())
    except Exception:
        return ""
    for j in (status.get("jobs") or []):
        if not isinstance(j, dict) or j.get("group") != group:
            continue
        path = j.get("log") or agent._find_log(group, j.get("pid"))
        if path:
            return agent._log_tail(path)
    return ""


def _age_text(seconds):
    """How long something has been running, in the largest useful unit."""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} 小时"
    if seconds >= 60:
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds:.0f} 秒"


def _send_nudge(group, elapsed):
    """One progress check, in its own thread: draining is the turn."""
    try:
        import agent
        body = (f"后台任务（{group}）已运行 {_age_text(elapsed)}，仍在进行。"
                f"请查看 webapp/static/status.json 里该任务的最新步骤与消息，"
                f"以及 runs/{group}/ 下的日志，向用户简要汇报当前进度；"
                f"若发现任务已停滞或报错，直接说明，不要粉饰。")
        tail = _running_log_tail(group)
        if tail:
            body += f"\n\n任务日志的最后几行：\n\n{tail}"
        for _ in agent.stream([{"role": "user", "content": body}]):
            pass
        print(f"[nudge] {group}: turn completed", flush=True)
    except Exception as e:
        # A nudge that fails is a missed check, and a missed check that
        # leaves no trace is indistinguishable from one that never fired.
        print(f"[nudge] {group}: FAILED: {e}", flush=True)


def _send_wake(group, names):
    """One wake turn, in its own thread: draining the stream is the turn."""
    try:
        import agent
        wake = f"后台任务已完成：{names}。请查看结果并汇报。"
        mine = [f for f in agent.finished_jobs_tail()
                if f.get("group") == group]
        if mine:
            parts = []
            for f in mine:
                head = f"—— {f.get('step') or f.get('group') or f.get('id')}" \
                    + ("（失败）" if f.get("state") == "error" else "") + " ——"
                tail = (f.get("log_tail") or "").strip()
                if tail:
                    parts.append(f"{head}\n{tail}")
            if parts:
                wake += "\n\n任务日志的最后几行：\n\n" + "\n\n".join(parts)
        for _ in agent.stream([{"role": "user", "content": wake}]):
            pass  # the turn runs to completion here; nothing to render to
    except Exception:
        pass  # a failed wake is a lost report, not a crashed server


# Modified 2026-11-06: explicit network opt-in and validated startup options.
def parse_args(argv=None):
    import argparse

    def port_number(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535")
        if not 1 <= number <= 65535:
            raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535")
        return number

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=port_number, default=8282)
    ap.add_argument("--host", default=os.environ.get("BIOBENCH_HOST") or "127.0.0.1",
                    help="bind address (default: BIOBENCH_HOST or 127.0.0.1)")
    return ap.parse_args(argv)


def main():
    args = parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    # The backstop starts before the server does, so a job that ends while
    # the panel is closed is reported whenever the user comes back to it.
    threading.Thread(target=_wake_backstop, daemon=True).start()
    print(f"== BioBench Viewer")
    print(f"   URL:        http://localhost:{args.port}")
    print(f"   Listening:  {args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("   Network access enabled: this server has no authentication.")
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
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
