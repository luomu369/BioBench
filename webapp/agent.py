#!/usr/bin/env python3
"""The agent behind the viewer's chat panel — a client for opencode.

The viewer is a static page, so it cannot hold an API key: anything served to
a browser is readable by that browser. This module runs inside the viewer's
own server instead. The page posts a message here, this drives an opencode
server, and the answer comes back. The key never leaves the machine — it lives
in opencode's own credential store, which this never reads.

Why opencode rather than a hand-rolled tool loop: the agent needs to read the
viewer's JSON state, run the skill scripts, and know this repo's conventions.
opencode already does all of that — it has a permission system, a session
store, context compaction, and native support for the Agent Skills layout
BioBench already uses. Writing that again would produce a worse version of it.

The vendored binary and its project config live in vendor/ and
opencode.jsonc. See vendor/install_opencode.sh.

Configuration:

  BIOBENCH_OPENCODE_PORT   the port opencode serves on (default 4096)
  OPENCODE_SERVER_PASSWORD  HTTP basic auth, if the server was started with it
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The repo root, one level up from webapp/. opencode serves the whole project
# — its config, the skills, the scripts — so it is started there and its
# vendored copy lives there too, not under webapp/.
ROOT = HERE.parent
VENDOR = ROOT / "vendor"
BINARY = VENDOR / "bin" / "opencode"

PORT = int(os.environ.get("BIOBENCH_OPENCODE_PORT") or 4096)
BASE = f"http://127.0.0.1:{PORT}"

# The viewer's own state, which is where a task's progress is published.
STATUS_FILE = HERE / "static" / "status.json"

# Where the API key lives when it is not in the environment.
#
# An `export` in a terminal lasts as long as that terminal. The viewer is
# started once and outlives every shell that launched it, and a key that
# vanishes with a closed terminal leaves the panel silently unable to answer
# — opencode gets an empty key, the provider returns 401, and the reply comes
# back with no text and no error. So the key is read from a file as well, and
# the file is in .gitignore.
KEY_FILE = HERE / "agent.key"


def _load_key(name):
    """One API key, from the environment or from agent.key.

    The file holds `NAME=value` lines, so several providers can share it. The
    environment wins: a key set for one run should not be overridden by
    whatever is on disk.
    """
    if os.environ.get(name):
        return
    if not KEY_FILE.is_file():
        return
    try:
        for line in KEY_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == name:
                os.environ[name] = v.strip().strip("\"'")
                return
    except OSError:
        pass  # an unreadable key file is not a reason to fail the panel


# The agent the panel talks to. "biobench" can run skills; the readonly one
# answers without touching anything. The panel uses the readonly agent for
# questions and the full one when the user asks for a run — see agent_name().
DEFAULT_AGENT = "biobench"

# Whether permission requests are answered without asking.
#
# Off by default: the prompt is the one place the user sees what the agent is
# about to run, and a command list they never saw is a command list they
# cannot judge. With it on, every request is answered "always" the moment it
# arrives — the agent runs whatever it decides to run, which is what makes a
# long skill sequence usable without a click per step, and what makes it
# unsafe to leave on around a structure you care about.
#
# The state lives in a file rather than in memory because the viewer is
# restarted more often than the preference changes, and a switch that resets
# itself on every restart is not a switch.
AUTO_APPROVE_FILE = HERE / ".auto-approve"


def auto_approve():
    """Whether to answer permission requests without asking the user."""
    try:
        return AUTO_APPROVE_FILE.is_file()
    except OSError:
        return False


def set_auto_approve(on):
    """Turn automatic approval on or off. Returns the new state."""
    try:
        if on:
            AUTO_APPROVE_FILE.write_text("on\n")
        else:
            AUTO_APPROVE_FILE.unlink()
    except OSError:
        pass  # a switch that cannot be written is a switch that stays off
    return auto_approve()

# How long to wait for a turn. opencode runs the model, calls tools, and may
# run a skill script that takes minutes — a docking sweep is not a chat
# response. A short timeout turns that into a failure that looks like the
# agent is broken.
TIMEOUT = 1800


def _auth():
    """Basic-auth credentials, if the server was started with a password."""
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not password:
        return None
    user = os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode"
    return user, password


def _request(method, path, body=None, timeout=60):
    """One call to the opencode server, returning the decoded response.

    urllib rather than the opencode SDK: the viewer's server has no
    dependencies beyond the standard library, and adding a package for four
    HTTP calls would be a poor trade.
    """
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    auth = _auth()
    if auth:
        import base64
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip() else None


def server_up():
    """Whether an opencode server is reachable."""
    try:
        _request("GET", "/global/health", timeout=5)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def binary_path():
    """The vendored opencode binary, or None if it has not been installed."""
    return BINARY if BINARY.is_file() else None


def start_server():
    """Start the vendored opencode server if it is not already running.

    Returns (ok, message). Starting it here rather than requiring a separate
    command means the panel works the first time the viewer is opened, which
    is the only time anyone tries it.
    """
    if server_up():
        return True, "already running"

    binary = binary_path()
    if binary is None:
        return False, ("opencode is not installed. Run: "
                       "bash vendor/install_opencode.sh")

    import subprocess

    # The project root is where opencode.jsonc lives, and opencode finds its
    # config by walking up from the working directory — so it has to be
    # started from the repo root, not from webapp/.
    log = HERE / "opencode-server.log"

    # The key has to reach the child, not just this process. opencode reads
    # {env:...} from its own environment, so a key loaded here but not passed
    # on would resolve to an empty string in the server and fail as a 401
    # that looks like a bad key rather than a missing one.
    _load_key("STEPFUN_API_KEY")
    env = dict(os.environ)

    with open(log, "a") as out:
        subprocess.Popen(
            [str(binary), "serve", "--port", str(PORT),
             "--hostname", "127.0.0.1"],
            cwd=str(ROOT), stdout=out, stderr=out, env=env,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )

    # It binds quickly but not instantly, and a request in that window fails
    # in a way that looks like a broken install rather than a race.
    import time
    for _ in range(40):
        if server_up():
            return True, "started"
        time.sleep(0.25)
    return False, f"opencode did not come up — see {log}"


def ensure_session():
    """The session the panel converses in, creating one only if there is none.

    One session for the viewer, so the agent keeps context across messages:
    the user asks what the structure is, then asks to dock into it, and the
    second question depends on the first having been answered.

    The session on file is the one the panel is showing, and it is used as it
    stands. It is never replaced on the panel's behalf — not when it is busy,
    not when it looks stuck. A busy session means a turn is still running, and
    the message the user just typed belongs with that turn: opencode answers
    it when the turn ends, in the same conversation, with everything that was
    said before it still in context. Replacing the session instead moved the
    message to an empty conversation, which read as the agent forgetting what
    it had been doing minutes earlier.

    A session that never finishes is a real problem, and it is the user's to
    resolve: the stop button aborts the turn, and the ＋ button starts a new
    conversation. Neither is guessed at here. Deciding on the user's behalf
    that a conversation was over — because it had been quiet, or because
    something looked wrong — is how the context they were relying on
    disappeared without anything having been pressed.

    A new session is created only when there is no file, or the one on file
    names a session that no longer exists: the server restarted with a fresh
    store, or the conversation was deleted from the history list.
    """
    state_file = HERE / ".opencode-session"
    if state_file.is_file():
        sid = state_file.read_text().strip()
        if sid:
            try:
                _request("GET", f"/session/{sid}", timeout=10)
                return sid
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                pass  # gone — the server restarted with a fresh store

    session = _request("POST", "/session",
                       {"title": "BioBench viewer"}, timeout=30)
    sid = session["id"]
    state_file.write_text(sid)
    return sid


# Where a user-chosen backdrop image is kept, and the extensions accepted.
#
# Under webapp/static/ because that is the directory the viewer already serves
# its own assets from, so the file is reachable at a plain URL and needs no
# endpoint of its own to read back.
STAGE_IMAGE = HERE / "static" / "stage-bg"
STAGE_IMAGE_EXTS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def set_stage_image(data_url):
    """Write the viewer's backdrop image, or clear it when given nothing.

    Takes a data URL — `data:image/png;base64,...` — because that is what a
    file input produces without a form post, and returns the URL the page
    should use as its background.

    The extension comes from the declared type rather than the bytes: the
    browser has already decided what it read, and sniffing the header here
    would be a second opinion on the same question. A type that is not an
    image is refused rather than written under a guessed name, because a file
    the viewer will try to draw as a backdrop and cannot is a blank stage with
    no explanation.

    Only one image is kept. A new one replaces the old, including when the
    type changes and the extension with it — otherwise the directory collects
    a file per attempt and the URL stops pointing at what is on screen.
    """
    if not data_url:
        for ext in list(STAGE_IMAGE_EXTS.values()):
            try:
                STAGE_IMAGE.with_suffix(ext).unlink()
            except OSError:
                pass
        return ""

    if not data_url.startswith("data:") or "," not in data_url:
        raise RuntimeError("not a data URL")
    header, _, payload = data_url.partition(",")
    mime = header[5:].split(";")[0].strip().lower()
    ext = STAGE_IMAGE_EXTS.get(mime)
    if not ext:
        raise RuntimeError(f"unsupported image type: {mime or 'unknown'}")

    import base64
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as e:
        raise RuntimeError(f"could not decode image: {e}")
    if not raw:
        raise RuntimeError("empty image")

    # The old file goes first, so a change of type does not leave the previous
    # one behind under a different extension.
    for other in list(STAGE_IMAGE_EXTS.values()):
        if other == ext:
            continue
        try:
            STAGE_IMAGE.with_suffix(other).unlink()
        except OSError:
            pass
    STAGE_IMAGE.with_suffix(ext).write_bytes(raw)
    return f"stage-bg{ext}"


def session_busy(sid):
    """Whether a session has a turn running.

    Asked when the panel switches conversations, so the send button can be put
    in the right state: a stop control while a turn runs, a send control when
    none does. Switching to a busy conversation and showing a send button
    there is a button that does nothing when pressed.

    This is a question about the session's state, not a decision about it —
    nothing here abandons a session for being busy. That is the user's call,
    through the stop button or the ＋ in the panel header.
    """
    if not sid:
        return False
    try:
        status = _request("GET", "/session/status", timeout=10) or {}
        return (status.get(sid) or {}).get("type") == "busy"
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False  # cannot tell; show a send button rather than a stop one


def history(limit=200):
    """The session's turns, oldest first, as the panel can render them.

    A turn is one user message plus the assistant messages it produced. A
    turn that called tools produces several assistant messages — one per
    step — and only the last carries the answer, so they are folded together
    rather than shown as separate replies.

    The window has to be wide for the same reason list_sessions() needs one:
    `?limit=N` returns the last N messages, and a turn that ran a skill
    produces a message per step. Forty of them is ordinary for a docking run,
    so a window of forty holds nothing but the tail of one turn and the
    question it answered falls outside — the panel restores an empty chat
    beside an agent that still remembers the whole conversation.

    This exists because the conversation outlives the page. opencode keeps it
    in its own session store; the panel kept its copy in memory, so a reload
    showed an empty chat beside an agent that still remembered everything.
    """
    # The session on disk is read directly rather than through
    # ensure_session(), which would create a new one when the current is busy
    # — and a busy session is exactly the case where there is history worth
    # showing. An empty or missing file means there is nothing to restore.
    state_file = HERE / ".opencode-session"
    if not state_file.is_file():
        return []
    sid = state_file.read_text().strip()
    if not sid:
        return []
    try:
        msgs = _request("GET", f"/session/{sid}/message?limit={limit}",
                        timeout=30) or []
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return []  # the session is gone; nothing to restore

    turns = []
    for m in msgs:
        info = m.get("info") or {}
        role = info.get("role")
        if role not in ("user", "assistant"):
            continue
        text, reasoning, tools = _extract_text(m.get("parts"))
        if role == "user":
            if text:
                turns.append({"role": "user", "content": text})
            continue
        # An assistant message with no text is a step that only called tools.
        # Its tools belong to the turn in progress, not to a turn of their own
        # — and the turn in progress may not have an assistant entry yet, if
        # this is the first step of it. In that case one is started, so the
        # tools have somewhere to land and the answering step that follows
        # fills in the reply rather than orphaning them.
        if not text:
            if not (turns and turns[-1]["role"] == "assistant"):
                turns.append({"role": "assistant", "reply": "",
                              "reasoning": "", "tools": []})
            turns[-1]["tools"].extend(tools)
            if reasoning:
                turns[-1]["reasoning"] += (
                    ("\n\n" + reasoning) if turns[-1]["reasoning"] else reasoning)
            continue
        if turns and turns[-1]["role"] == "assistant" and not turns[-1]["reply"]:
            # A second answering message in the same turn — the model
            # continued after the tools. Keep the longer one, which is the
            # substantive answer rather than a one-line acknowledgement.
            if len(text) > len(turns[-1]["reply"]):
                turns[-1]["reply"] = text
            turns[-1]["tools"].extend(tools)
        elif (turns and turns[-1]["role"] == "assistant"
              and turns[-1]["reply"]):
            # Another answering message after one that already answered. A
            # turn that ran a skill produces one message per step, and the
            # last is not always the fullest — so they are joined rather than
            # shown as separate replies to one question.
            turns[-1]["reply"] += "\n\n" + text
            turns[-1]["tools"].extend(tools)
            if reasoning:
                turns[-1]["reasoning"] += (
                    ("\n\n" + reasoning) if turns[-1]["reasoning"] else reasoning)
        else:
            turns.append({"role": "assistant", "reply": text,
                          "reasoning": reasoning, "tools": list(tools)})
    return turns


def clear_session():
    """Drop the current session so the next message starts a fresh one.

    The session file is what ties the panel to a conversation. Removing it
    makes the next turn create a new session, which is a cleaner break than
    deleting the old one — a session that is mid-turn cannot be deleted from
    under opencode, and the file going away is enough to stop reusing it.
    """
    state_file = HERE / ".opencode-session"
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass


def abort_turn():
    """Stop the turn in the current session.

    Without this the model keeps running after the user gives up on it — the
    panel's stream is closed but opencode is not, so the turn finishes in the
    background and the next message queues behind it. Aborting is what makes
    the stop button actually stop something.

    Every busy session is aborted, not just the current one. A turn abandoned
    because its session was busy keeps running in opencode, and it holds a
    permission request that only its own abort can release — leaving it going
    means the next turn inherits the same hang.
    """
    try:
        status = _request("GET", "/session/status", timeout=10) or {}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        status = {}
    targets = [sid for sid, st in status.items()
               if (st or {}).get("type") == "busy"]
    sid = current_session_id()
    if sid and sid not in targets:
        targets.append(sid)
    if not targets:
        return False
    ok = False
    for target in targets:
        try:
            ok = _request("POST", f"/session/{target}/abort",
                          timeout=30) or ok
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
    return ok


def current_session_id():
    """The session the panel is in, or "" if there is none yet."""
    state_file = HERE / ".opencode-session"
    if not state_file.is_file():
        return ""
    return state_file.read_text().strip()


def new_session():
    """Start a fresh conversation and make it the current one."""
    session = _request("POST", "/session", {"title": "BioBench viewer"},
                       timeout=30)
    sid = session["id"]
    (HERE / ".opencode-session").write_text(sid)
    return sid


def use_session(sid):
    """Switch the panel to an existing conversation."""
    # Confirmed to exist before it is written down, so a stale id from an old
    # page cannot leave the panel pointing at a session that is gone.
    _request("GET", f"/session/{sid}", timeout=15)
    (HERE / ".opencode-session").write_text(sid)
    return sid


def delete_session(sid):
    """Remove one conversation from opencode's store."""
    return _request("DELETE", f"/session/{sid}", timeout=30)


def list_sessions(limit=30):
    """Every conversation, newest first, labelled by its first message.

    A session id is not something a user recognises, so the label is the first
    thing they asked — which is what the conversation was about. Sessions with
    no messages yet are left out: an empty conversation is not a choice worth
    offering, and there are usually several from turns that were started and
    abandoned.

    The message window has to be wide. `?limit=N` returns the *last* N
    messages, and one turn that ran a skill produces a message per step — a
    docking run with its checks is forty of them. A window of twenty holds
    nothing but assistant messages, the user's question falls outside it, and
    the conversation disappears from the list for having nothing to show. So
    the window is sized to hold a long turn, and a session whose user message
    still falls outside it is listed under its title rather than dropped.
    """
    sessions = _request("GET", "/session", timeout=20) or []
    sessions = sorted(sessions, key=lambda s: (s.get("time") or {}).get(
        "updated") or 0, reverse=True)

    out = []
    for s in sessions[:limit]:
        sid = s.get("id")
        if not sid:
            continue
        try:
            msgs = _request("GET", f"/session/{sid}/message?limit=200",
                            timeout=20) or []
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
        first = ""
        count = 0
        for m in msgs:
            if (m.get("info") or {}).get("role") != "user":
                continue
            text, _, _ = _extract_text(m.get("parts"))
            if text:
                count += 1
                if not first:
                    first = text
        if not first:
            # Nothing asked in it, or the question is older than the window.
            # Either way it is not a conversation to offer — but a session
            # that has messages and no visible question is one whose window
            # was too narrow, and dropping it hides a real conversation.
            if not msgs:
                continue
            first = (s.get("title") or "").strip() or "（无提问）"
        out.append({
            "id": sid,
            "title": first[:60],
            "turns": count,
            "current": sid == current_session_id(),
        })
        if len(out) >= limit:
            break
    return out


def answer_permission(session_id, permission_id, response, remember=False):
    """Answer a permission request the agent is waiting on.

    `response` is "once", "always" or "reject". Without this the agent blocks
    on the request indefinitely — it cannot proceed and it cannot time out,
    so the panel shows a running turn that will never finish.

    Two endpoints are tried because opencode 1.18 runs both permission
    systems side by side: the requests it raises itself are v1 and are
    answered on `/session/{id}/permissions/{pid}`, while the v2 API under
    `/api/` is what an external caller creates and answers. Which one holds a
    given request is not knowable from the outside, so the v1 endpoint is
    tried first — it is the one opencode's own asks land in — and a 404 falls
    through to v2.

    "always" is remembered per session by v1 and written to the project's
    saved permissions by v2. Neither is wrong; the difference is that a v1
    "always" is forgotten when the server restarts, which is why the
    auto-approve switch is the durable control — it is read from disk on every
    request rather than held in opencode's memory.
    """
    if not session_id or not permission_id:
        raise RuntimeError("no permission request to answer")
    try:
        return _request("POST",
                        f"/session/{session_id}/permissions/{permission_id}",
                        {"response": response},
                        timeout=30)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    return _request("POST",
                    f"/api/session/{session_id}/permission/{permission_id}/reply",
                    {"reply": response},
                    timeout=30)


def _extract_text(parts):
    """The assistant's reply, out of opencode's message parts.

    A response is a list of parts — reasoning, text, tool calls. The text is
    the answer; the reasoning is the model's working, and the tool calls say
    what it looked at. All three are returned, because a reasoning model's
    answer is much easier to trust when the working that produced it is
    visible — and on a question about a specific structure, the working is
    often where the actual reading of the data happened.
    """
    text, reasoning, tools = [], [], []
    for p in parts or []:
        ptype = p.get("type")
        if ptype == "text":
            t = (p.get("text") or "").strip()
            if t:
                text.append(t)
        elif ptype == "reasoning":
            r = (p.get("text") or "").strip()
            if r:
                reasoning.append(r)
        elif ptype == "tool":
            name = p.get("tool") or ""
            state = p.get("state") or {}
            # The tool's input is what makes the line useful: "read" says
            # little, "read current.json" says what was looked at.
            inp = state.get("input") or {}
            target = (inp.get("filePath") or inp.get("path")
                      or inp.get("command") or inp.get("pattern") or "")
            tools.append(f"{name} {target}".strip())
    return "\n\n".join(text), "\n\n".join(reasoning), tools


def chat(messages, agent=None):
    """One turn of the conversation.

    `messages` is the panel's history; only the last user message is sent,
    because opencode keeps the rest in its own session store — resending it
    would double the context and make the agent see its own replies as the
    user's words.

    Returns (reply, reasoning, tools_used). Raises with a message that says
    what to do.
    """
    ok, detail = start_server()
    if not ok:
        raise RuntimeError(detail)

    last = None
    for m in reversed(messages or []):
        if m.get("role") == "user":
            last = m.get("content") or ""
            break
    if not last:
        raise RuntimeError("no user message to send")

    sid = ensure_session()
    body = {
        "parts": [{"type": "text", "text": last}],
        "agent": agent or DEFAULT_AGENT,
    }

    # The message is sent asynchronously and the tool calls are collected from
    # the event stream while it runs.
    #
    # The synchronous /message endpoint returns the finished reply but not the
    # tool parts — its response carries only step-start, text and step-finish.
    # The tool calls are published on /event instead, so that is where they
    # are read from. Sending synchronously and then reading the event stream
    # would miss everything that happened before the reply came back.
    try:
        _request("POST", f"/session/{sid}/prompt_async", body, timeout=30)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"opencode returned {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach opencode: {e.reason}") from None

    tools = _collect_tools(sid)
    reply, reasoning = _await_reply(sid)
    return reply, reasoning, tools


def stream(messages, agent=None, on_event=None, attach=None):
    """One turn, yielding events as they happen instead of at the end.

    Yields dicts: {"type": "text"|"reasoning"|"tool"|"done"|"error", ...}

    Why this exists alongside chat(): a turn that runs a skill takes minutes,
    and a panel that shows nothing for minutes looks broken. The model emits
    text as it goes and opencode publishes every increment on /event, so the
    increments are forwarded rather than waited out.

    The event stream is opened *before* the message is sent. Connecting
    afterwards loses everything published in between — and when the turn is
    short, the stream connects after it has already finished, so no further
    event ever arrives and the read blocks until the timeout. A background
    thread holds the connection and queues what it reads, so nothing is lost
    to the race either way.

    `attach` names a session to watch without sending anything. This is what
    the panel uses when it switches back to a conversation whose turn is still
    running: the work continues in opencode while the user is elsewhere, and
    coming back has to show it live rather than a snapshot taken when they
    left. Nothing is sent, so the turn is not disturbed and no second turn is
    queued behind it.
    """
    import queue
    import threading

    ok, detail = start_server()
    if not ok:
        yield {"type": "error", "message": detail}
        return

    # The id of the user message this turn answers, or None when it is not
    # known yet. The user's own message is echoed back through the event
    # stream with the same shape as the assistant's, and showing it as the
    # answer would put the question where the reply belongs — so it is
    # skipped wherever it is recognised.
    #
    # Attaching already knows it: the message was sent before this connection
    # existed, and its id is read out of the session below. A turn being
    # started learns it from the stream, where the echo arrives first.
    user_mid = None

    if attach:
        # Watching an existing turn. The session is confirmed to exist so a
        # stale id from an old page does not leave the panel waiting on a
        # stream that will never produce anything.
        try:
            _request("GET", f"/session/{attach}", timeout=15)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            yield {"type": "error",
                   "message": f"会话不存在或已删除：{e}"}
            return
        sid = attach
    else:
        last = None
        for m in reversed(messages or []):
            if m.get("role") == "user" and (m.get("content") or "").strip():
                last = m["content"]
                break
        if not last:
            yield {"type": "error", "message": "no user message to send"}
            return
        sid = ensure_session()

    def emit(ev):
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass
        return ev

    # The reader thread. It owns the connection for the whole turn and puts
    # every event on a queue, so the generator below consumes them without
    # holding the socket — and a slow consumer cannot stall the stream.
    q = queue.Queue()
    stop = threading.Event()

    def read_events():
        try:
            req = urllib.request.Request(f"{BASE}/event")
            auth = _auth()
            if auth:
                import base64
                token = base64.b64encode(
                    f"{auth[0]}:{auth[1]}".encode()).decode()
                req.add_header("Authorization", f"Basic {token}")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                for raw in r:
                    if stop.is_set():
                        break
                    line = raw.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        q.put(json.loads(line[5:]))
                    except json.JSONDecodeError:
                        continue
        except (urllib.error.URLError, OSError):
            pass
        finally:
            q.put(None)  # end of stream

    t = threading.Thread(target=read_events, daemon=True)
    t.start()

    # Give the connection a moment to establish before the message goes out,
    # so the first events are not published into a socket that is not yet
    # listening. Short: the queue absorbs anything that arrives early.
    import time
    time.sleep(0.3)

    if attach:
        # Watching a turn that is already running. Nothing is sent — the
        # message that started it went out while the user was in this
        # conversation, and sending anything now would queue a second turn
        # behind the one being watched.
        #
        # Only the turn in progress is replayed, not the whole conversation.
        # The panel has already rendered the earlier turns from
        # /agent/history, and replaying every tool call the session ever made
        # would list them a second time under the turn being watched. So the
        # messages are walked back from the end to the last user message, and
        # only what came after it is forwarded.
        try:
            prior = _request("GET", f"/session/{sid}/message?limit=200",
                             timeout=30) or []
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            prior = []
        # Walk back to the user message that started this turn. Everything
        # after it belongs to the turn; everything before it is history the
        # panel already has.
        start = 0
        for i in range(len(prior) - 1, -1, -1):
            if (prior[i].get("info") or {}).get("role") == "user":
                start = i + 1
                # Its id is kept so the event stream below can skip it. The
                # user's message is echoed through the same stream with the
                # same shape as the assistant's, and showing it as the answer
                # would put the question where the reply belongs.
                user_mid = (prior[i].get("info") or {}).get("id")
                break
        seen_text = seen_reasoning = ""
        for m in prior[start:]:
            if (m.get("info") or {}).get("role") != "assistant":
                continue
            ptext, preason, _ = _extract_text(m.get("parts"))
            if len(ptext) > len(seen_text):
                seen_text = ptext
            if len(preason) > len(seen_reasoning):
                seen_reasoning = preason
        # Only the text and the working are replayed. The tool calls are left
        # to the event stream below, which is already running and will publish
        # each call as it happens — replaying them here would list the ones
        # already made twice, once from this read and once from the stream's
        # own deduplication missing them.
        if seen_text:
            yield {"type": "text", "text": seen_text}
        if seen_reasoning:
            yield {"type": "reasoning", "text": seen_reasoning}
        # The turn may already be over — the user switched away, it finished,
        # and they have come back to a completed conversation. Waiting for an
        # idle event that was published before this connection would block
        # until the timeout, so the session's own state is asked instead.
        try:
            status = _request("GET", "/session/status", timeout=10) or {}
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            status = {}
        if (status.get(sid) or {}).get("type") != "busy":
            yield {"type": "done", "reply": seen_text}
            return
    else:
        body = {
            "parts": [{"type": "text", "text": last}],
            "agent": agent or DEFAULT_AGENT,
        }
        try:
            _request("POST", f"/session/{sid}/prompt_async", body, timeout=30)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            stop.set()
            yield {"type": "error",
                   "message": f"opencode returned {e.code}: {detail}"}
            return
        except urllib.error.URLError as e:
            stop.set()
            yield {"type": "error",
                   "message": f"could not reach opencode: {e.reason}"}
            return

    # Each text part arrives whole rather than as a delta — opencode
    # publishes the accumulated text on every update — so the last value seen
    # for a part is its current state, and that is what gets forwarded.
    seen_tools = set()
    tool_order = []
    text = reasoning = ""
    # The turn ends when opencode says so, and not before.
    #
    # Every timing-based rule here was tried and each one was wrong in the
    # same direction: a step that ran a skill script is followed by however
    # long that script takes, and a ResGen generation or a Vina sweep is
    # minutes. A grace period short enough to feel responsive reads that
    # silence as the end and reports "no reply" while the model is still
    # working; one long enough to be safe makes every short answer feel stuck.
    # There is no value that is right for both, so there is no value at all —
    # `session.idle` is the only thing that actually knows, and the stop
    # button is how the user cuts a long one short.
    finished = False

    while not finished:
        try:
            ev = q.get(timeout=1.0)
        except queue.Empty:
            continue
        if ev is None:
            break  # the stream ended
        etype = ev.get("type")
        if etype in ("permission.asked", "permission.v2.asked"):
            # The agent wants to run something the rules do not cover. It
            # waits for an answer, and without one the turn hangs forever —
            # which is indistinguishable from a slow run from the outside.
            # The request is forwarded so the panel can ask the user, and the
            # answer comes back through answer_permission().
            #
            # Both spellings are handled because opencode 1.18 publishes the
            # v2 event and keeps the old one for compatibility; the payloads
            # differ in shape, so each is read on its own terms.
            props = ev.get("properties") or {}
            # Not filtered by session id. An abandoned turn — one whose session
            # was left busy and replaced — still runs in opencode, and its
            # permission request arrives here with the old session's id. The
            # prompt is the only thing that can unblock it, so it is forwarded
            # whatever session it came from; the answer is sent to the session
            # named on the event, which is the one actually waiting.
            if etype == "permission.v2.asked":
                # v2 names the thing being asked for an "action" and the
                # patterns "resources"; "save" is what "always" writes.
                what = props.get("action") or "bash"
                patterns = props.get("resources") or []
            else:
                what = props.get("permission") or "bash"
                patterns = props.get("patterns") or []
            asking_sid = props.get("sessionID") or sid
            if auto_approve():
                # The switch is on, so the request is answered here rather
                # than shown. "always" rather than "once" so that a turn which
                # runs the same command in several steps is not stopped once
                # per step — the whole point of the switch is a run that does
                # not need anyone at the keyboard.
                #
                # A failure to answer is reported rather than swallowed: the
                # agent is blocked on this request, so a silent failure is a
                # turn that hangs with nothing on screen to explain it.
                try:
                    answer_permission(asking_sid, props.get("id"), "always")
                except Exception as e:
                    yield emit({"type": "error",
                                "message": f"自动许可失败：{e}"})
                continue
            yield emit({
                "type": "permission",
                "id": props.get("id"),
                "session": asking_sid,
                "permission": what,
                "patterns": patterns,
                "metadata": props.get("metadata") or {},
            })
            continue
        if etype == "session.idle":
            # The turn is over, said outright. This is the signal to wait for:
            # the event stream is a long-lived connection that never closes,
            # so silence cannot mean "finished", and a step finishing cannot
            # either — a step that ran a skill script is followed by however
            # long that script takes, which is minutes for a generation or a
            # docking sweep. Guessing from timing is what produced turns that
            # reported "no reply" while the model was still working.
            #
            # Not filtered by session. A turn abandoned because its session
            # was busy keeps running in opencode, and its permission request
            # arrives on this same stream with the old session's id — dropping
            # it left a prompt nobody could answer and a turn that hung with
            # nothing on screen to explain it.
            break
        if etype == "message.updated":
            # A message that finished with a stop reason and no tool calls
            # pending is the end of the turn, even if the idle event is missed.
            info = (ev.get("properties") or {}).get("info") or {}
            if info.get("finish") in ("stop", "end_turn", "length"):
                finished = True
            continue
        if etype != "message.part.updated":
            continue
        props = ev.get("properties") or {}
        # The session id is on the event for tool parts but not always for
        # text ones, so a missing field means "cannot tell" rather than "not
        # mine" — filtering on it alone drops text parts and the reply never
        # arrives.
        part = props.get("part") or {}
        ptype = part.get("type")

        # The user's own message is echoed back through the same stream with
        # the same shape as the assistant's, and showing it as the answer
        # would put the question where the reply belongs.
        #
        # Attaching already knows the id — it was read out of the session
        # above, because the echo went out long before this connection
        # existed and the "first messageID seen is the user's" rule would
        # instead skip the assistant's reply being watched for. A turn being
        # started has no id yet, so the first one seen is taken to be the
        # user's, which is what the echo always is.
        mid = part.get("messageID")
        if mid:
            if user_mid is None:
                user_mid = mid
                continue
            if mid == user_mid:
                continue

        if ptype == "text":
            new = part.get("text") or ""
            if new != text:
                text = new
                yield emit({"type": "text", "text": text})
        elif ptype == "reasoning":
            new = part.get("text") or ""
            if new != reasoning:
                reasoning = new
                yield emit({"type": "reasoning", "text": reasoning})
        elif ptype == "tool":
            call_id = part.get("callID") or part.get("id") or ""
            state = part.get("state") or {}
            inp = state.get("input") or {}
            # The first event for a call carries an empty input and the real
            # one arrives a moment later, so a call with nothing to show yet
            # is skipped rather than recorded blank — the later event for the
            # same callID then lands.
            if not inp or call_id in seen_tools:
                continue
            seen_tools.add(call_id)
            target = (inp.get("filePath") or inp.get("path")
                      or inp.get("command") or inp.get("pattern")
                      or inp.get("query") or "")
            label = f"{part.get('tool') or 'tool'} {target}".strip()
            tool_order.append(label)
            yield emit({"type": "tool", "tool": label})
        elif ptype in ("step-finish", "step-start"):
            # Neither of these ends the turn. A turn that called tools runs
            # several steps — read the files, then answer — and the gap
            # between them is however long the tool took. Only `session.idle`
            # or a finished message says the turn is over.
            pass

    stop.set()

    # Backstop: the stream can end before the last part is published, so the
    # finished message is read once more and anything missing is sent.
    try:
        final_text, final_reasoning, _ = _await_reply(sid)
    except Exception:
        final_text = final_reasoning = ""
    if final_text and final_text != text:
        text = final_text
        yield {"type": "text", "text": text}
    if final_reasoning and final_reasoning != reasoning:
        reasoning = final_reasoning
        yield {"type": "reasoning", "text": reasoning}

    # A turn that started a background task is not finished when the model
    # stops talking. The skill scripts publish their progress to status.json
    # and run for minutes — a docking sweep, a generation — and the model's
    # last words are "started, I'll report when it's done", which is a promise
    # nothing was keeping. The panel is told what is still running so it can
    # watch for it and bring the model back when it lands.
    pending = pending_jobs()
    if pending:
        # The tail of each job's log, read now rather than when the job ends.
        #
        # A run that takes an hour leaves the model waking up to a log it has
        # not seen, and the first thing it does is read it — which is a tool
        # call and a round trip before it can say anything. Reading the tail
        # here, while the turn is still open and the paths are known, means the
        # wake-up message carries the result with it.
        #
        # Only the last few lines. The log is the record; the wake-up needs the
        # ending, and a model handed the whole file reads it instead of
        # answering.
        # Only the path is carried here. The tail is read when the job ends,
        # by /agent/jobs, because a tail read now is the middle of the run and
        # the wake-up needs the ending.
        for j in pending:
            if not j.get("log"):
                j["log"] = _find_log(j.get("group"), j.get("pid"))
        yield {"type": "waiting", "jobs": pending}

    yield {"type": "done", "reply": text, "reasoning": reasoning,
           "tools": tool_order, "session": sid}


def _proc_starttime(pid):
    """When a process started, in clock ticks since boot, or None.

    Read from /proc rather than asked of the kernel, because this is the one
    thing that tells a reused pid from the original. Linux hands out pids in
    order and wraps at pid_max, so a pid that has been released comes back
    eventually — and `kill(pid, 0)` on the new process succeeds, which reads
    as the old run still going. The panel would then wait forever on work
    that finished hours ago.

    The start time cannot collide: two processes holding the same pid existed
    at different times by definition. Field 22 of /proc/<pid>/stat, counted
    after the comm field in parentheses — comm can itself contain spaces and
    parentheses, so the split is done from the last ')' rather than by field
    index from the front.
    """
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            raw = f.read()
    except (OSError, ValueError, OverflowError):
        return None
    try:
        return raw[raw.rindex(")") + 2:].split()[19]
    except (ValueError, IndexError):
        return None


def _pid_alive(pid, starttime=None):
    """Whether the process that was recorded is still there.

    Signal 0 is the question "may I signal you", which the kernel answers
    without touching the process. It is the cheapest liveness check there is
    and it needs no parsing.

    A pid the caller does not own answers EPERM rather than ESRCH — the
    process exists, it is simply not ours to signal — so that counts as alive
    too. Only "no such process" means gone.

    When a start time was recorded alongside the pid, it is compared as well:
    a pid that answers but started at a different time is a different process
    that inherited the number, and the run it belonged to is over.
    """
    if not pid:
        return None  # nothing to check
    try:
        pid = int(pid)
    except (ValueError, OverflowError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # cannot tell; assume alive rather than drop a live run
    if starttime:
        now = _proc_starttime(pid)
        # None means /proc could not be read — the process may have exited
        # between the signal and the read. Believe the signal, which is the
        # more recent answer.
        if now is not None and str(starttime) != str(now):
            return False
    return True


# Where a running job's log is looked for, relative to the repo root.
#
# The skills write their logs next to their output, and the names are theirs
# rather than ours — so the candidates are the ones actually in use, and a
# miss is not an error: a job with no log found simply wakes the model with
# nothing attached, which is what happened before this existed.
#
# Kept in step with publish.py's list, which is the one that matters: the
# publisher records the path while the process is still alive to ask, and this
# is only the fallback for a job published before that existed.
LOG_NAMES = ("md.log", "run.log", "gen.log", "dock.log", "boltzgen.log",
             "simulate.log", "vina.log", "fpocket.log", "prepare.log",
             "torsion.log", "admet.log", "search.log", "foldseek.log",
             "mmseqs.log", "resgen.log", "mpnn.log")


def _log_tail(path, lines=12):
    """The last few lines of a log file, or "" if there is nothing to read.

    Read as bytes and decoded with errors replaced: a log being written by
    another process can be read mid-line, and a decode error over a partial
    UTF-8 sequence would lose the whole tail over one character.
    """
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        with open(p, "rb") as f:
            try:
                f.seek(0, 2)
                size = f.tell()
                # Enough for a dozen lines of a wide OpenMM log, without
                # reading a 400 MB trajectory by mistake.
                f.seek(max(0, size - 8192))
            except OSError:
                f.seek(0)
            raw = f.read()
    except OSError:
        return ""
    text = raw.decode(errors="replace")
    out = [l for l in text.splitlines() if l.strip()]
    return "\n".join(out[-lines:])


def _find_log(group, pid=None):
    """A running job's log, if one can be found.

    The pid is the reliable route and is tried first, by reading the process's
    own command line: the skills take `--out <dir>` and write their log inside
    it, so the directory is right there in argv. That is the only route that
    works — `/proc/<pid>/cwd` is the repo root for a run started with absolute
    paths, and the job's group name has nothing to do with the directory
    (`1ru7_ra_md` ran out of `work/pockets_1ru7/md_ra`).

    The group name is the fallback, for a job whose process has already gone by
    the time this is asked. It matches the directory under runs/ or work/ when
    the skill named them the same, which some do.
    """
    if pid:
        out = _proc_outdir(pid)
        if out:
            for base in (out, out / "md_out", out / "out"):
                for name in LOG_NAMES:
                    cand = base / name
                    if cand.is_file():
                        return str(cand)
    if not group:
        return None
    for base in (ROOT / "runs", ROOT / "work"):
        d = base / group
        if not d.is_dir():
            continue
        # The directory itself, then each immediate subdirectory. The skills
        # nest their output — a docking run writes docking_out/vina.log under
        # the run directory rather than vina.log directly in it — so a search
        # of the top level alone misses most of them.
        for sub in [d] + [p for p in sorted(d.glob("*")) if p.is_dir()]:
            for name in LOG_NAMES:
                for cand in (sub / name,):
                    if cand.is_file():
                        return str(cand)
    return None


def _proc_outdir(pid):
    """The `--out` directory a running process was given, or None.

    Read from /proc/<pid>/cmdline, which is the argv the process started with.
    The skills all take `--out`, and it is where their log goes — so this is
    the one place the answer is written down by the thing that knows it.

    A relative path is resolved against the repo root, because that is where
    the viewer starts its children and where the skills are run from.
    """
    try:
        raw = open(f"/proc/{int(pid)}/cmdline", "rb").read()
    except (OSError, ValueError, OverflowError):
        return None
    argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            d = Path(argv[i + 1])
            return d if d.is_absolute() else (ROOT / d)
        # `--out=dir` is the other form a script may accept.
        if a.startswith("--out="):
            d = Path(a[6:])
            return d if d.is_absolute() else (ROOT / d)
    return None


def pending_jobs():
    """The tasks still running, as the panel needs to see them.

    Two sources, and the OS wins. `status.json` says what each step is and how
    the script describes its own progress; the pid recorded beside it says
    whether the process is actually still there. A step that claims `running`
    with a dead pid is a run that ended without saying so — killed, crashed,
    OOM — and waiting on it would hang the panel on work that finished
    minutes ago.

    A step with no pid falls back to its own word, because there is nothing
    better to go on: a `queued` step has not started yet and has no process,
    and a script that never reported one is still better believed than
    ignored.
    """
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    def describe(j):
        return {
            "id": j.get("id") or "",
            "group": j.get("group") or "",
            "step": j.get("step") or "",
            "index": j.get("index"),
            "total": j.get("total"),
            "state": j.get("state"),
            "skill": j.get("skill") or "",
            "message": j.get("message") or "",
            "pid": j.get("pid"),
            "pid_start": j.get("pid_start"),
            "log": j.get("log") or _find_log(j.get("group"), j.get("pid")),
        }

    def still_running(j):
        if j.get("state") != "running":
            # A queued step is a plan label — publish.py writes one per step
            # of a declared route, and nothing flips them until the final
            # publish marks the whole group done. Believing them keeps this
            # list non-empty forever after the process behind the run has
            # exited, and the panel, which wakes on an empty list, never
            # wakes: two long designs sat unreported for a quarter of an
            # hour each, with the run finished and the watcher polling
            # faithfully, for exactly this reason. A group is pending when
            # one of its steps is actually running.
            return False
        alive = _pid_alive(j.get("pid"), j.get("pid_start"))
        # None means no pid was recorded — believe the step's own word.
        return alive is not False

    # Grouped, because the queued labels belong to a group whose running
    # step is the work: they are listed while that step lives and dropped
    # when it does, rather than held on their own.
    jobs = [j for j in (status.get("jobs") or []) if isinstance(j, dict)]
    active = status.get("active")
    if isinstance(active, dict):
        jobs.append(active)
    live_groups = {(j.get("group") or j.get("id") or "")
                   for j in jobs if still_running(j)}
    out, seen_ids = [], set()
    for j in jobs:
        if (j.get("group") or j.get("id") or "") not in live_groups:
            continue
        # The active step and the jobs list hold the same run twice — a
        # publish writes both — so the same id can arrive twice and the
        # wake message would name one step twice.
        jid = j.get("id") or ""
        if jid and jid in seen_ids:
            continue
        seen_ids.add(jid)
        out.append(describe(j))
    # The active step is the one the user is waiting on even when it has not
    # been written into the jobs list yet, so it is included on its own.
    active = status.get("active")
    if isinstance(active, dict) and still_running(active):
        if not any(p["id"] == active.get("id") for p in out):
            out.append(describe(active))
    return out


def finished_jobs_tail(limit=3):
    """The logs of the jobs that finished most recently, newest first.

    Asked when nothing is running any more, which is the moment the panel is
    about to wake the model. The tail goes with the wake-up so the model does
    not spend its first tool call reading a log to find out what happened.

    Only jobs that ended are considered, and only the last few — a run that
    published five structures is one job with five steps, not five things to
    report. The newest by `updated` wins, because that is the one the user was
    watching.

    "Ended" means what `pending_jobs()` means by it, and the two have to agree:
    the panel asks that first, and wakes the model when it comes back empty. A
    job whose process is gone has ended whether or not anyone wrote `done` —
    that is the whole case the pid exists for, a script that crashed or was
    killed and never got to say so. Filtering on the state word alone would
    return nothing in exactly the case the tail is most needed, and the model
    would wake to a log it has to go and read.
    """
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    def ended(j):
        if not isinstance(j, dict):
            return False
        if j.get("state") in ("done", "error"):
            return True
        # Still marked running, but the process behind it is gone. Same test
        # pending_jobs() applies, so the two cannot disagree about what is
        # over. A job with no pid recorded is believed: there is nothing to
        # check it against, and dropping it would lose a run that finished
        # cleanly and simply never reported a pid.
        if j.get("state") not in ("queued", "running"):
            return False
        return _pid_alive(j.get("pid"), j.get("pid_start")) is False

    done = [j for j in (status.get("jobs") or []) if ended(j)]
    done.sort(key=lambda j: j.get("updated") or "", reverse=True)

    out = []
    for j in done[:limit]:
        path = j.get("log") or _find_log(j.get("group"), j.get("pid"))
        # A job whose log cannot be found is kept, with an empty tail. It was
        # skipped before, which dropped the whole entry: the wake then carried
        # no record of a run that had genuinely finished, and a job whose
        # group name is not a runs/ directory — a test, a one-off — vanished
        # from the list entirely. An empty tail says "no log found"; a
        # missing entry says nothing at all.
        out.append({
            "id": j.get("id") or "",
            "group": j.get("group") or "",
            "step": j.get("step") or "",
            "state": j.get("state"),
            "message": j.get("message") or "",
            "log": path or "",
            "log_tail": _log_tail(path),
        })
    return out


def _collect_tools(sid, idle_after=6.0):
    """The tool calls this turn made, read from the event stream.

    Returns when the session has been idle for `idle_after` seconds — the
    model has finished its turn and nothing more is coming. A fixed duration
    would either cut a long turn short or make every short one wait.
    """
    import threading
    import time

    found = []
    seen = set()
    done = threading.Event()

    def watch():
        try:
            req = urllib.request.Request(f"{BASE}/event")
            auth = _auth()
            if auth:
                import base64
                token = base64.b64encode(
                    f"{auth[0]}:{auth[1]}".encode()).decode()
                req.add_header("Authorization", f"Basic {token}")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                for raw in r:
                    line = raw.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    # Tool calls arrive as message.part.updated, with the part
                    # nested under properties. The same call is published
                    # several times as it moves pending → running → completed,
                    # so the callID is what deduplicates them.
                    if ev.get("type") != "message.part.updated":
                        continue
                    props = ev.get("properties") or {}
                    if props.get("sessionID") not in (None, sid):
                        continue
                    part = props.get("part") or {}
                    if part.get("type") != "tool":
                        continue
                    call_id = part.get("callID") or part.get("id") or ""
                    if call_id in seen:
                        continue
                    state = part.get("state") or {}
                    inp = state.get("input") or {}
                    # The input is what makes the line useful: "bash" says
                    # little, "bash ls skills/" says what was looked at. The
                    # first event for a call is published with an empty input
                    # and the real one arrives a moment later, so a call with
                    # nothing to show yet is skipped rather than recorded
                    # blank — the later event for the same callID then lands.
                    if not inp:
                        continue
                    seen.add(call_id)
                    target = (inp.get("filePath") or inp.get("path")
                              or inp.get("command") or inp.get("pattern")
                              or inp.get("query") or "")
                    found.append(
                        f"{part.get('tool') or 'tool'} {target}".strip())
        except (urllib.error.URLError, OSError):
            pass  # the stream ending is not a failure; the reply is separate

    t = threading.Thread(target=watch, daemon=True)
    t.start()

    # Wait for the reply, then let the stream drain a little longer so the
    # last tool call — the one whose result the answer is based on — is not
    # cut off.
    deadline = time.time() + TIMEOUT
    while time.time() < deadline and not finished:
        try:
            msgs = _request("GET", f"/session/{sid}/message?limit=1",
                            timeout=15) or []
            if msgs:
                info = msgs[-1].get("info") or {}
                if info.get("role") == "assistant" and _has_text(msgs[-1]):
                    time.sleep(idle_after)
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1.0)
    done.set()
    return found


def _has_text(msg):
    return any(p.get("type") == "text" and (p.get("text") or "").strip()
               for p in (msg.get("parts") or []))


def _await_reply(sid):
    """The assistant's reply and its reasoning, once the turn has finished.

    The last few messages are read rather than just the last one: a turn that
    called tools produces several assistant messages, and the one carrying
    the answer is not necessarily the final one — the last may be a short
    acknowledgement after the real reply. The newest message with text in it
    is the answer.
    """
    msgs = _request("GET", f"/session/{sid}/message?limit=6", timeout=30) or []
    for m in reversed(msgs):
        if (m.get("info") or {}).get("role") != "assistant":
            continue
        text, reasoning, _ = _extract_text(m.get("parts"))
        if text:
            return text, reasoning
    return "", ""


def configured():
    """Whether the panel can talk to an agent at all.

    True once the binary is installed and a server can be started — the model
    and its key are opencode's business, configured with `opencode auth login`
    or a provider block in opencode.jsonc, and are deliberately not read here.
    """
    return binary_path() is not None
