# The viewer's agent panel

The chat panel on the right of the viewer is driven by
[opencode](https://opencode.ai) — an open-source terminal coding agent — run
headless and vendored into this repo.

## Why opencode

The agent has to read the viewer's JSON state, run the skill scripts, and know
this repo's conventions. opencode already does all three: it has a permission
system, a session store with context compaction, and native support for the
Agent Skills layout BioBench already uses. A hand-rolled tool loop would be a
worse version of the same thing, and would need its own permission system,
session store and compaction before it was safe to point at a repo.

## What is vendored

| Path | What |
|------|------|
| `vendor/opencode.json` | the pinned version and upstream reference |
| `vendor/install_opencode.sh` | fetches that version into `vendor/bin/` |
| `vendor/bin/opencode` | the binary — **not committed**, 177 MB and platform-specific |
| `vendor/prompts/biobench.md` | the system prompt for the `biobench` agent |
| `../opencode.jsonc` | project config: the two agents, permissions, instructions |

Install it once per machine:

```bash
bash vendor/install_opencode.sh
```

The script is idempotent — a machine that already has the pinned version does
nothing. `--force` reinstalls.

## The two agents

Both are defined in `opencode.jsonc` and share the same prompt.

**`biobench-readonly`** (the default) — answers questions, changes nothing.
Its bash permission is `deny` for everything except `cat`, `ls`, `grep`,
`find`, `head`, `tail`, `wc`, and `--help` on the skill scripts.

**`biobench`** — can also run the skill scripts. Its permission is `ask` for
everything, with the skill scripts and read-only commands allowed outright.
So a docking run still asks before it starts, but reading a file does not.

The panel has a switch between them. It defaults to read-only, because most
questions are "what is this" and an agent that can run a docking sweep should
not be the one that answers them by accident.

## Configuring the model

opencode holds the credentials, not this repo. Either:

```bash
opencode auth login          # interactive, stores in ~/.local/share/opencode
```

or a provider block in `opencode.jsonc`:

```json
{
  "provider": {
    "deepseek": {
      "options": { "apiKey": "{env:DEEPSEEK_API_KEY}" }
    }
  },
  "model": "deepseek/deepseek-chat"
}
```

`{env:...}` reads an environment variable, so the key stays out of the file.
See [opencode's provider docs](https://opencode.ai/docs/providers) for the
full list.

## How the panel talks to it

`webapp/agent.py` is a client for opencode's HTTP API. The page posts to
`/agent` on the viewer's server; that starts the vendored server if it is not
running, sends the message, and collects the reply.

The API key never reaches the browser — the page is static, so anything served
to it is readable by whoever has the URL. The key stays in opencode's
credential store, which this code never reads.

Tool calls are collected from opencode's SSE event stream (`/event`), because
the synchronous message endpoint returns only the finished text. The panel
shows them under the reply, so the user can see that the agent actually looked
at the structure before answering.

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /agent/status` | `{installed, running}` — the panel asks on load |
| `POST /agent` | `{messages, agent}` → `{reply, tools}` |

## When it is not installed

The panel still renders. `GET /agent/status` reports `installed: false`, the
panel says so with the command to fix it, and the send button is disabled —
rather than letting the user type into a box that cannot answer.
