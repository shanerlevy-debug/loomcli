---
name: powerloom-onboarding
description: Onboard a fresh agent session (Claude Code, Codex CLI, Gemini CLI, or Antigravity) to Powerloom from zero. Covers loomcli install, authentication against api.powerloom.org or a self-hosted control plane, runtime-specific plugin/extension setup, the weave-tracker §4.10 thread-registration workflow, and pointers to the other skills in this plugin. Use when an agent session is asking "how do I get started with Powerloom?", has just been spawned and needs to come up to speed, or is hitting "weave: command not found" / "not signed in" type errors before any real work has begun.
---

# Powerloom — Fresh Agent Onboarding

You are an expert at bringing a brand-new agent session up to speed on Powerloom. The session in front of you may be Claude Code, Codex CLI, Gemini CLI, or Antigravity — most steps are the same; the runtime-specific deltas are called out where they matter.

This skill is the "first 10 minutes" doc. It gets you to the point of having `weave` installed, authenticated, your runtime's plugin loaded, and your first tracker thread filed. After that, hand off to the `weave-tracker` skill (workflow detail) and `weave-interpreter` skill (CLI reference).

## TL;DR — your first 5 minutes

```bash
# 1. Install the CLI
pip install loomcli
weave --version            # confirm it's on PATH

# 2. Sign in (browser PAT-paste flow; falls back to --pat <token> for headless)
weave login
weave whoami               # confirm identity

# 3. Load your runtime's plugin (one of these)
claude --plugin-dir /path/to/loomcli/plugin                          # Claude Code
codex plugin marketplace add /path/to/loomcli/plugins/codex          # Codex CLI
gemini extensions install /path/to/loomcli/plugins/gemini/powerloom-weave --consent --skip-settings

# Or let weave print the exact command for your checkout:
weave plugin instructions codex
weave plugin instructions gemini

# 4. File your first thread for the work this session is about to do
weave thread create --project powerloom \
  --title "<imperative phrase>" --priority high \
  --description "<context, definition of done>"
weave thread pluck <returned-id>

# 5. Do the work. Reply on the thread for significant decisions.
weave thread reply <id> "decision: chose option A because..."
weave thread done <id>     # at PR merge
```

If any step above failed, read the matching numbered section below.

## What is Powerloom

Powerloom is an AI-native control plane for fleets of agents — declarative agent/skill/workflow management, a hosted MCP backend, a CLI (`weave`, shipped by the `loomcli` package), and a tracker subsystem that serves as the cross-session work queue. The hosted control plane lives at `https://api.powerloom.org`; the same software runs locally via docker-compose for development.

You, as an agent session, interact with Powerloom through the `weave` CLI. The tracker (threads + replies) is how your session's work becomes a durable record that survives context-compaction and is visible to other sessions.

## Step 1 — Install loomcli

```bash
pip install loomcli
weave --version
```

Expected output: `weave 0.6.x` or higher. If `weave: command not found`, your `pip` user-bin directory isn't on `$PATH`. Either add it (`pip show -f loomcli | grep weave` to find the install path) or use a venv:

```bash
python3 -m venv ~/.venvs/powerloom
source ~/.venvs/powerloom/bin/activate    # or .\Scripts\activate on Windows PowerShell
pip install loomcli
weave --version
```

For local development against an unreleased loomcli (e.g. you cloned the repo to test a new feature):

```bash
pip install -e /path/to/loomcli
```

Python 3.11+ is required.

## Step 2 — Sign in

Three ways to authenticate, in order of preference:

### Browser-paste (default, what you almost always want)

```bash
weave login
```

This opens `https://powerloom.org/settings/access-tokens` in your browser. Mint a Personal Access Token (PAT), paste it into the hidden-input prompt back in the terminal. The CLI verifies against `/me` and writes credentials to disk.

For headless systems where you don't want a browser to launch:

```bash
weave login --no-browser
```

Then mint the PAT yourself and paste it.

### Direct PAT injection (scripts, CI, agent invocations)

```bash
weave login --pat <token>
```

Same verification path, no prompts. **Never paste the raw token into chat or commit it.** If the user gave you a PAT, treat it like a credential — use it once, don't echo it back.

### Localhost dev-mode (development only)

```bash
weave --api-url http://localhost:8000 login --dev-as admin@dev.local
```

Only works against a control plane running with `POWERLOOM_AUTH_MODE=dev`. Don't try this against production.

### Verify

```bash
weave whoami
```

Should print your identity, org, and the API URL it's signed into. If it says "Not signed in," step 2 didn't take — check the troubleshooting section.

### Where credentials live

Cross-platform via `platformdirs`:
- **Linux / macOS:** `~/.config/powerloom/credentials`
- **Windows:** `%APPDATA%\powerloom\powerloom\credentials`

Override with `POWERLOOM_HOME=<path>` env var or `--config-dir <path>` flag.

### Pointing at a self-hosted Powerloom

If the user is running their own control plane (homelab, on-prem, internal staging), set the API URL **before** `weave login`:

```bash
export POWERLOOM_API_BASE_URL=https://powerloom.internal.example.com
weave login
```

Or pass `--api-url` per-invocation (it's a global option, before the subcommand):

```bash
weave --api-url https://powerloom.internal.example.com login
```

The credential file is keyed by API URL — you can be signed in to multiple control planes simultaneously and switch by toggling `POWERLOOM_API_BASE_URL`.

## Step 3 — Install the plugin/extension for your runtime

The CLI works without any plugin. The plugins add slash-commands, skills (like this one), and runtime-specific glue. Install the one matching your client.

### Claude Code

```bash
# from a clone of the loomcli repo
claude --plugin-dir /path/to/loomcli/plugin

# or, once the plugin is published to a marketplace
claude plugin install powerloom-home@<marketplace-name>
```

Slash commands appear under `/powerloom-home:weave-*` (login, status, ask, chat, plan, apply, manifest, agent-status, session-tail, thread, diagnose). The `weave-tracker`, `weave-interpreter`, and `powerloom-onboarding` skills auto-load when relevant.

### Codex CLI

```bash
codex plugin marketplace add /path/to/loomcli/plugins/codex

# From a loomcli checkout, this prints the correct local path:
weave plugin instructions codex
```

Point Codex at `plugins/codex`, the marketplace root, not `plugins/codex/powerloom-weave`. The skill files (`weave-tracker`, `weave-interpreter`, `powerloom-onboarding`) live under `plugins/codex/powerloom-weave/skills/` and are loaded by Codex when their descriptions match the user's request. The plugin manifest is at `plugins/codex/powerloom-weave/.codex-plugin/plugin.json`; marketplace metadata is at `plugins/codex/.agents/plugins/marketplace.json`.

After adding the marketplace, enable `powerloom-weave@powerloom` in Codex if it is not auto-enabled.

### Gemini CLI

```bash
gemini extensions install /path/to/loomcli/plugins/gemini/powerloom-weave --consent --skip-settings
gemini extensions enable powerloom-weave

# From a loomcli checkout, this prints the correct local path:
weave plugin instructions gemini
```

Restart Gemini CLI or reload commands after installing. Slash commands appear under `/weave:*` and `/weave:thread:*`. The `GEMINI.md` context file in the extension is auto-merged into your project's Gemini context — that's where the tracker workflow + provider-agnostic invocation rules come in.

Gemini extensions don't use markdown skill files — they use TOML slash commands. The onboarding equivalent for Gemini is `/weave:onboard`.

### Antigravity (IDE)

Antigravity uses Gemini's loading conventions (per `docs/antigravitykb.md` in the Powerloom repo) — install the Gemini extension above and the same context applies. Read `GEMINI.md` at the project root + this skill, then the same `weave` commands work.

## Step 4 — Read the §4.10 working agreement

Open the project-rules file matching your runtime:

- **Claude Code:** `CLAUDE.md` at the project root
- **Gemini CLI / Antigravity:** `GEMINI.md` at the project root
- **Codex CLI:** `AGENTS.md` at the project root

All three mirror the same content (per CLAUDE.md §4.9 — `CLAUDE.md` is canonical, the others are derived). Read **at least §4.10** before doing any code or doc work. The short version:

> Every agent session that does meaningful work files a tracker thread for that work via `weave`. The thread becomes the durable record (TodoWrite lists die when context compacts; threads don't), the shared queue (so other sessions don't double-up on your work), and the audit trail (so future-you can answer "why did this take three days?").

If your runtime has a `weave-tracker` skill or equivalent, this is a good time to load it. The full lifecycle (create → pluck → reply → done), description-shape conventions, error modes, and quick reference card all live there. Don't re-derive any of that from scratch — read the skill.

## Step 5 — File your first thread

Walked example, assuming the work in front of you is "fix the dashboard loading spinner that hangs on slow networks":

```bash
# 1. Create
weave thread create --project powerloom \
  --title "Fix dashboard loading spinner hang on slow networks" \
  --priority high \
  --description "$(cat <<'EOF'
**Reported:** 2026-04-27 by Shane

Dashboard at app.powerloom.org/dashboard shows a spinner that never resolves
when the network is throttled below ~1Mbps. Initial console error suggests
the WS upgrade is timing out before the JS hydration completes.

## Repro

1. Open Chrome DevTools -> Network -> throttle to "Slow 3G"
2. Navigate to https://app.powerloom.org/dashboard
3. Spinner spins indefinitely; no fallback after 30s

## Definition of done

- Spinner gives up after 10s and renders the cached last-known-good view
- Console error references a clear timeout, not the WS upgrade exception
- Manual repro above produces the fallback view

## Out of scope

- Underlying WS upgrade race (separate thread)
- Redesign of the spinner asset
EOF
)"
```

The CLI prints the new thread's `id` (a UUID prefix like `f1a2b3c4`). Copy it.

```bash
# 2. Pluck — claim it for this session
weave thread pluck f1a2b3c4

# 3. Work. Reply on the thread when something noteworthy happens.
weave thread reply f1a2b3c4 "Repro confirmed in Chrome 126 + Firefox 128 — both hang. Root cause appears to be in /static/js/hydrate.js line 240."

# 4. At PR merge, mark done
weave thread done f1a2b3c4
```

The merge commit message for the PR should include the thread URL — `https://app.powerloom.org/projects/powerloom/threads/f1a2b3c4` — so the cross-link is bidirectional.

For the canonical example of how a thread description should read, run `weave thread show 8d2c7502` (Alfred connection) or any of the other reference threads: `c41a8294`, `3671bfaf`, `9210c2c2`, `e011a581`, `2be84503`, `a0a715dc`, `9d29ac25`. They all follow the context-up-front / repro / definition-of-done / out-of-scope shape.

### If `weave thread …` doesn't exist yet

The `weave thread` subcommand family is shipping as part of loomcli 0.6.3+ (tracked as thread `2be84503`). If your installed loomcli predates it, fall back to either:

- The helper script at `scratch/create_dogfood_threads.py` in the Powerloom repo, or
- Direct API calls:

```bash
curl -X POST https://api.powerloom.org/projects/<project_id>/threads \
  -H "Authorization: Bearer $POWERLOOM_PAT" \
  -H "Content-Type: application/json" \
  -d '{"title":"...","priority":"high","description":"..."}'
```

The `weave-tracker` skill covers this fallback in detail. Upgrade loomcli (`pip install -U loomcli`) when convenient.

## Step 6 — Know what else exists

Other skills shipped in this plugin, and when each is right:

| Skill | When to load it |
|---|---|
| **`weave-tracker`** | Anything thread-related beyond the create/pluck/reply/done basics — multi-session coordination, sub-principal attribution, error mode reference, idempotency rules, finding open work to pick up. The full operational guide. |
| **`weave-interpreter`** | Anything CLI-shaped that isn't threads — `weave apply` semantics, manifest schema versions, skill archive lifecycle, OU addressing, approval gates, error code reference, scripting weave safely. The reference manual for the broader CLI. |
| **`powerloom-onboarding`** | This skill. Re-load it if a sub-agent or fresh sub-session needs the same first-10-minutes walk. |

Beyond the skills, the runtime-specific commands you have:

- **Claude Code:** `/powerloom-home:weave-login`, `/powerloom-home:weave-status`, `/powerloom-home:weave-ask`, `/powerloom-home:weave-chat`, `/powerloom-home:weave-thread`, `/powerloom-home:weave-apply`, `/powerloom-home:weave-plan`, `/powerloom-home:weave-manifest`, `/powerloom-home:weave-agent-status`, `/powerloom-home:weave-session-tail`, `/powerloom-home:weave-diagnose`, `/powerloom-home:home-mode`. The MCP server (`powerloom-home`) exposes 11 tools for OU/Skill/Agent CRUD against a local SQLite backend.
- **Gemini CLI:** `/weave:ask`, `/weave:chat`, `/weave:status`, `/weave:plan`, `/weave:agent-status`, `/weave:session-tail`, `/weave:thread:create`, `/weave:thread:pluck`, `/weave:thread:reply`, `/weave:thread:done`, `/weave:thread:list`, `/weave:onboard`.
- **Codex CLI:** the skills above; slash commands depend on your Codex version.

## Common error modes during onboarding

| Error | Cause | Fix |
|---|---|---|
| `weave: command not found` | loomcli installed but not on `$PATH` | Add pip user-bin to PATH, or use a venv (see Step 1) |
| `Connection refused` / `WinError 10061` | API URL unreachable | Check `POWERLOOM_API_BASE_URL` or `--api-url`. Prod is `https://api.powerloom.org`; local needs `docker compose up -d` first |
| `HTTP 401 Unauthorized` on `weave whoami` | Token missing, expired, or invalid | `weave logout && weave login` (or rerun `weave login --pat <fresh-token>`) |
| `HTTP 403 Permission denied` | Token valid but lacks RBAC for this op | Either grant the role or use a user with the permission |
| `HTTP 409 justification_required` | Org has an approval policy | Pass `--justification "reason"` or set `POWERLOOM_APPROVAL_JUSTIFICATION` |
| `HTTP 409 Thread already plucked` | Two sessions tried to claim the same thread | `weave thread show <id>` to see who has it; coordinate before reassigning |
| `weave login` opens browser but PAT page is blank | API URL points at a control plane that doesn't have the PAT-mint UI | Either you set the wrong `POWERLOOM_WEB_URL` or the self-hosted instance is missing the web tier |
| Plugin slash-commands don't appear after install | Client wasn't restarted, or plugin-dir path is wrong | Restart the runtime; verify the directory has the expected manifest (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, or `gemini-extension.json`) |

For anything else, fall back to the diagnostic ladder in `weave-interpreter`:

```bash
weave --version          # CLI installed?
weave whoami             # signed in? against the right API URL?
weave doctor             # auth, API capabilities, actor kinds, PATH
weave plugin doctor      # local plugin packages and client binaries
weave get ou             # can you read?
weave plan <manifest>    # safest preview if a manifest is misbehaving
```

## Where to read more

- **`CLAUDE.md` / `GEMINI.md` / `AGENTS.md`** at the Powerloom project root — the working agreement. §4.10 is required reading for every session; the rest covers delivery format, doc discipline, git/GitHub flow, multi-session coordination.
- **`weave-tracker` skill** — full thread workflow operational guide.
- **`weave-interpreter` skill** — full `weave` CLI reference.
- **`docs/coordination.md`** in the Powerloom repo — multi-session protocol if you're not the only agent on the project.
- **`docs/architecture/self-import-mvp.md`** in the Powerloom repo — how Powerloom imports its own roadmap into the tracker (the mechanism you're now part of).
- **Tracker UI:** `https://app.powerloom.org/projects/powerloom` — humans look at threads here.
- **API reference:** `https://api.powerloom.org/docs` — OpenAPI/Swagger.
- **loomcli source:** `https://github.com/shanerlevy-debug/loomcli` — CLI + schemas.
- **Powerloom repo:** `https://github.com/shanerlevy-debug/Powerloom` — control plane, web, terraform, project docs.

## Codex-specific install note

The canonical onboarding doc lives at `loomcli/plugin/skills/powerloom-onboarding/SKILL.md` (Claude Code path); a verbatim copy lives at `loomcli/plugins/codex/powerloom-weave/skills/powerloom-onboarding/SKILL.md` (Codex path). Keep the runtime install commands aligned with `weave plugin instructions <client>`.

If you find an inconsistency between the two copies, the Claude Code path is the source of truth (per CLAUDE.md §4.9). File a thread under project `powerloom` to flag the drift.
