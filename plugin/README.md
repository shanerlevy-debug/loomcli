# Powerloom Home — Claude Code Plugin

Run a full Powerloom control plane locally, inside Claude Code. Declarative agent + skill + workflow management, weave CLI integration, local MCP-served backend, SQLite state — no cloud dependency.

## What you get

- **Agentic hosted commands**: `/powerloom-home:weave-ask` and `/powerloom-home:weave-chat` wrap `weave ask` / `weave chat` for provider-agnostic hosted agent sessions.
- **Agent observability commands**: `/powerloom-home:weave-agent-status` and `/powerloom-home:weave-session-tail` wrap `weave agent status` / `weave session tail`.

- **Slash commands** — `/powerloom-home:weave-login`, `/powerloom-home:weave-status`, `/powerloom-home:weave-thread`, `/powerloom-home:weave-apply`, `/powerloom-home:weave-plan`, `/powerloom-home:weave-manifest`, `/powerloom-home:weave-diagnose`, `/powerloom-home:home-mode`.
- **Weave-interpreter skill** — comprehensive reference for the `weave` CLI auto-loaded when you ask Claude about weave operations.
- **Local MCP server (`powerloom-home`)** — 10 tools exposing Powerloom OU/Skill/Agent CRUD, backed by SQLite at `$CLAUDE_PLUGIN_DATA/powerloom-home.sqlite`.
- **Hooks** — Claude greets you with home-backend status at session start; automatically runs `weave plan` when you edit a Powerloom YAML manifest.

## Who should use this

| Tier | When |
|---|---|
| **Home (casual)** | Solo, local-only, zero infrastructure. Persists across Claude Code restarts. |
| **Home (advanced)** | Same but with docker-compose Postgres for more than toy scale (coming in v0.2) |
| **Home Supporter ($5/mo)** | Hosted on `<your-slug>.home.powerloom.org` — reachable from anywhere, survives machine sleep (coming later) |
| **Hosted Enterprise** | Use the hosted `api.powerloom.org` instead; `weave login` against production |

## Install

### From this repo (development install)

```powershell
# Windows
claude --plugin-dir D:\path\to\loomcli\plugin
```

```bash
# macOS / Linux
claude --plugin-dir /path/to/loomcli/plugin
```

### From marketplace (once shipped)

```bash
claude plugin install powerloom-home@<marketplace-name>
```

## Prerequisites

- Claude Code installed
- Python 3.11+ on PATH (for the MCP server)
- `pip install mcp` (MCP SDK) — or let the plugin do it automatically on first run (coming in v0.2)
- Optional: `pip install loomcli` — for shell-out to `weave` CLI from slash commands (the plugin works without it, just with reduced capabilities)

## Usage

### First run

When the plugin is enabled, Claude Code starts the MCP server automatically. Claude will greet you with status:

```
Welcome — Powerloom home backend is live.
DB: C:\Users\you\AppData\Roaming\claude\...\powerloom-home.sqlite
Version: 0.1.0-dev
```

### Create your first fleet

```
/powerloom-home:home-mode
```

Walks you through creating an OU, a skill, and an agent — all locally, no cloud.

### Author a manifest

```
/powerloom-home:weave-manifest
```

Describes what you want ("an agent that reviews pull requests") and Claude scaffolds a valid manifest against the bundled schema.

### Apply

```
/powerloom-home:weave-apply path/to/manifest.yaml
```

### Ask or chat with a hosted agent

```
/powerloom-home:weave-ask /dev-org/alfred "What should I work on next?"
/powerloom-home:weave-chat /dev-org/alfred
```

These commands call `weave ask` / `weave chat`. The CLI does not call provider APIs directly; Powerloom uses the target agent's configured runtime/model and the user/org runtime credential.

### Inspect live agent work

```
/powerloom-home:weave-agent-status /dev-org/alfred
/powerloom-home:weave-session-tail <session-id>
```

These commands read runtime state only. They do not patch manifests or change the agent's provider/model.

### Diagnose an error

```
/powerloom-home:weave-diagnose "<paste error here>"
```

## MCP tools exposed

All prefixed `powerloom_` when called from tool-use:

| Tool | What it does |
|---|---|
| `powerloom_whoami` | Identity + DB path + version |
| `powerloom_list_ous` | All OUs in the home org |
| `powerloom_get_ou` | One OU by path |
| `powerloom_create_ou` | Create an OU (idempotent by path) |
| `powerloom_list_skills` | All skills; filter by ou_path |
| `powerloom_get_skill` | One skill by ID |
| `powerloom_create_skill` | Create a skill (with v1.2.0 `system` + `auto_attach_to` support) |
| `powerloom_list_agents` | All agents; filter by ou_path |
| `powerloom_get_agent` | One agent by ID (returns attached skills) |
| `powerloom_create_agent` | Create an agent (full v1.2.0 fields + skill attachments by name) |
| `powerloom_recent_audit` | Recent audit-log entries |

## Migrating to hosted Powerloom

When your home fleet outgrows the local setup, migrate to the hosted `api.powerloom.org`:

1. Sign up / log in at `powerloom.org`
2. Create a PAT at `/settings/access-tokens`
3. `weave login --pat <your-token>`
4. Run the pending `weave home-to-enterprise migrate` tool (coming in v0.2) — reads your home SQLite + POSTs equivalents to hosted

## What's NOT in v0.1 (tracked for later)

- **Docker-compose advanced mode** — Postgres + reconciler in containers. v0.2.
- **Background reconciler** — currently a stub. v0.2.
- **Cloudflare Tunnel integration** — for exposing home to public DNS. v0.3.
- **Supporter tier hosted subdomain** — $5/mo. Requires Stripe + provisioning. v0.4+.
- **MCP deployment management** — requires local MCP launcher. Not yet scoped.
- **Federation (Layer C)** — cross-org memory convergence. Powerloom roadmap, post-v056.

## Known limitations (v0.1)

- The MCP server is single-user — no RBAC, no multi-org. Home edition is single-tenant by construction.
- Reconciliation happens synchronously on create; no background reconciler yet. Fine for solo use.
- Skill archives are stored as file-path references, not the archive bytes themselves. Upload semantics are stubbed.
- No approval gates — solo admin assumes self-approval. If you enable approval-gated policies, the home mode will reject them. Hosted enterprise is the right choice for that.

## Development

### Layout

```
plugin/
├── .claude-plugin/
│   └── plugin.json           # Plugin manifest — registers MCP server + metadata
├── skills/
│   ├── weave-interpreter/
│   └── weave-tracker/
│       └── SKILL.md          # The comprehensive weave CLI reference
├── commands/                 # 11 slash commands (login/status/apply/plan/manifest/diagnose/home-mode/ask/chat/agent-status/session-tail)
├── hooks/
│   └── hooks.json            # SessionStart greeting + PostToolUse manifest-detect
├── mcp-server/
│   └── powerloom_home/
│       ├── __init__.py
│       ├── __main__.py       # Entry point — `python -m powerloom_home`
│       └── db.py             # SQLite backend with full CRUD + audit
└── tests/
    ├── test_db.py            # pytest-compatible DB layer tests
    └── smoke.py              # Standalone smoke test (no pytest dep)
```

### Running tests

```bash
cd plugin
python tests/smoke.py                # quick end-to-end DB smoke
python -m pytest tests/ -q           # full test suite (requires pytest)
```

### Testing the MCP server standalone

```bash
cd plugin/mcp-server
POWERLOOM_HOME_DB_PATH=/tmp/test.sqlite python -m powerloom_home
# speaks MCP protocol over stdio; use an MCP client to invoke tools
```

## Links

- Main Powerloom repo: https://github.com/shanerlevy-debug/Powerloom
- loomcli (CLI + schemas): https://github.com/shanerlevy-debug/loomcli
- Hosted Powerloom: https://powerloom.org
- Docs: https://github.com/shanerlevy-debug/loomcli/tree/main/docs

## License

Proprietary. © 2026 Bespoke Technology Solutions.
