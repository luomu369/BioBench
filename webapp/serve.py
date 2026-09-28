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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
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

        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self.send_error(413, "request too large")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return

        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            self._json(400, {"error": "no messages"})
            return

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
            for ev in agent.stream(messages, agent=which):
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

        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            self.send_error(413, "request too large")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return

        sid = data.get("id")
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
            busy = agent.session_busy(agent.current_session_id())
        except Exception as e:
            self._json(200, {"error": str(e)})
            return
        self._json(200, {"ok": True, "turns": turns, "busy": busy})

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

        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            self.send_error(413, "request too large")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
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

        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000:
            self.send_error(413, "request too large")
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
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

        length = int(self.headers.get("Content-Length") or 0)
        # A photograph is a few megabytes and a screenshot of a desktop more.
        # The cap is on the encoded body, which is a third larger than the
        # file, so it is generous rather than tight.
        if length > 24_000_000:
            self.send_error(413, "image too large")
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return

        image = data.get("image") or ""
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
