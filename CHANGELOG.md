# Changelog

All notable changes to the Powerloom schema and CLI are documented here. This repo uses two independent version streams:

- **Schema:** `schema-vX.Y.Z` git tags. Semver — breaking changes bump major, additive bump minor, docs-only bump patch.
- **CLI:** `vX.Y.Z` git tags on this repo. Trigger PyPI publish via `.github/workflows/publish.yml`.

## Unreleased

## v0.7.22 — 2026-05-03 (CLI)

**`weave open` Windows fix — interactive runtime prompts now work.** The Claude Code "trust this folder?" arrow-key picker (and Codex's first-run consent) couldn't read keyboard input after `weave open` on Windows. Root cause: `os.execvpe` on Windows is implemented as spawn-and-exit — the parent shell closes the console handle before the child's TTY is ready. Fixed by switching the Windows hand-off to `subprocess.run` with inherited stdio + `sys.exit` on the child's return code; the POSIX path (`os.execvpe`) is unchanged so signals still propagate cleanly there. PR: [#80](https://github.com/shanerlevy-debug/loomcli/pull/80).

## v0.7.21 — 2026-05-03 (CLI)

**`weave open` Windows fixes — bare-clone worktree ref + non-UTF-8 codepage decode.** Two bugs surfaced from a real Windows user trace running `weave open` on a host with `cp932` (Japanese) as the OEM codepage:

- **Worktree creation failed with `fatal: not a valid object name: 'origin/main'`.** `git clone --bare` uses `+refs/heads/*:refs/heads/*` as its default fetch refspec, so upstream branches land directly under `refs/heads/*` in the bare repo — `refs/remotes/origin/*` never gets populated. The worktree-add now uses the bare branch name (resolved against `refs/heads/<branch>`, which `git fetch --prune origin` keeps in sync). Existing bare clones at `~/.powerloom/repos/<slug>.git/` survive — only the worktree-add command shape changed.
- **`UnicodeDecodeError` on cp932 (and other non-UTF-8 OEM codepages).** The stdout reader thread of preflight subprocess calls (most likely `gh auth status`) crashed when the child emitted non-ASCII bytes (smart quotes, box-drawing chars). Every `subprocess.run` in `loomcli/_open/` now passes `encoding="utf-8", errors="replace"`. Affects `_run_git`, `_gh_auth_ok`, `_git_credential_helper_ok`, `_ssh_agent_has_github_key`, `_worktree_dirty`.

PR: [#77](https://github.com/shanerlevy-debug/loomcli/pull/77).

## v0.7.20 — 2026-05-02 (CLI)

**Weave Open Launch UX milestone — complete.** End-to-end paste-from-web flow: brand-new user clicks "Open in agent" on the Powerloom web UI, copies a `weave open lt_…` command, pastes it on any terminal on any machine, and lands in a fully-contextualised agent session in <15 s — no `weave login`, no manual git config, no MCP wiring, no skill installs. Closes [milestone 8567b658](https://app.powerloom.org/projects/loomcli/milestones/8567b658-a7f8-45bb-9428-112d85577da7) on the loomcli side; the engine half landed across [Powerloom PRs #301, #316, #324, #326](https://github.com/shanerlevy-debug/Powerloom/pulls?q=is%3Apr+is%3Aclosed+launch).

The big bump (0.7.16 → 0.7.20 in one shot) reflects the milestone-not-sprint cadence — five sprints of loomcli work landing together.

### What `weave open <token>` does now

1. **Redeem** the launch token (with a local cache so Ctrl-C between redeem-and-clone resumes cleanly — no fresh token needed from the web UI).
2. **Auth bootstrap** — exchange the launch token for a 90-day machine credential at `~/.config/powerloom/auth.json`. After the first launch, subsequent weave commands authenticate automatically. Refreshes silently within the 14-day window via the engine's refresh endpoint.
3. **Pre-flights** — git on PATH, runtime binary, `~/.powerloom/` writable, disk space, and (when org policy is `local_credentials`) usable git credentials for the host. Aggregated, fail-fast, every issue surfaced in one pass.
4. **Bare-clone** the project to `~/.powerloom/repos/<project>.git/` (one per project, shared across worktrees).
5. **`git worktree add`** a fresh checkout to `~/.powerloom/worktrees/<scope>-<short_id>/`.
6. **MCP install** — drop a project-local `.mcp.json` with a pre-authed Powerloom server (when not already registered globally).
7. **Skill install** — pull each `spec.skills[*]` from `/skills/{id}/archive` into `<worktree>/.claude/skills/<slug>/` with idempotent version sidecars.
8. **Rules sync** — write `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` from the org's live convention overlay (per the launch spec's `rules_sync` directives).
9. **Register** the agent-session via `POST /agent-sessions` and drop `<worktree>/.powerloom-session.env`.
10. **`exec`** the runtime binary (`claude` / `codex` / `gemini`) in the worktree so the user's terminal becomes the agent.

### New surfaces

- **`weave open <token>`** — the headline paste-from-web command (sprint `cli-weave-open-20260430`).
- **`weave open --resume <session-id>` / `--reuse <scope>`** — exec into an existing worktree without re-cloning.
- **`weave reveal`** — open the worktree of the current/specified session in the OS file manager (Explorer / Finder / `xdg-open`). Addresses the "wait, my code is in `~/.powerloom/`?" confusion.
- **`weave gc`** — list / `--apply` remove abandoned worktrees + bare clones + expired launch-spec cache entries. `--include-active` for explicit cleanup of in-progress sessions (typed-confirmation gated).
- **`weave doctor`** — extended with a launch-readiness section: org clone-auth-mode, machine-credential health, local-cred probe (when applicable), active-sessions count.
- **`weave profile set --worktree-root <path>`** — persist a non-home worktree root for users on small home drives.

### New private modules in `loomcli/_open/`

`auth_bootstrap.py`, `git_ops.py`, `launch_cache.py`, `mcp_install.py`, `preflight.py`, `resume.py`, `rules_sync.py`, `runtime_exec.py`, `session_reg.py`, `skill_updates.py`, `skills_install.py` — one module per concern, with `commands/open_cmd.py` as the thin orchestrator.

### Engine-side dependencies (already shipped)

- `POST /launches` / `GET /launches/{token}` (Powerloom PR #301)
- `POST /auth/machine-credentials/exchange|revoke|refresh` + `GET /users/me/machines` (Powerloom PRs #316, #324)
- `clone_auth_mode` org setting + GitHub App installation token mint (Powerloom PR #326)

### Test plan / scope

- 964 tests passing across the full suite (net +400 tests for this milestone).
- Per-sprint integration tests pinned with mocked engines so CI is offline-friendly.
- Manual smoke covered by the user (paste flow on a fresh machine end-to-end).

### Sprint-by-sprint thread breakdown

- **Sprint 2 (`cli-weave-open-20260430`):** `c78ead6d` skeleton + redeem; `864c55a4` bare-clone + worktree; `5fab82ed` register session + .env; `53573d73` exec runtime; `5790b2d6` --resume / --reuse / worktree-root; `53fddf29` apply rules_sync.
- **Sprint 3 (`auth-bootstrap-20260430`):** `fbb69176` write/load 90d credential; `648bca84` refresh-on-use + expiry handling.
- **Sprint 4 (`clone-auth-policy-20260430`):** `9233e176` pre-flight checks; `79e876b1` local-credentials path + actionable error UX.
- **Sprint 6 (`skills-mcp-bootstrap-20260430`):** `d1b883af` skill install; `d240bfd7` drop .mcp.json with pre-authed Powerloom server; `647858ec` skill version pinning + resume update prompt.
- **Sprint 7 (`polish-doctor-resume-20260430`):** `b594a3d6` resume-on-interrupt cache; `f3ebdda4` weave doctor launch-readiness; `f6d8f7b0` weave reveal; `7a81d721` weave gc.

(Sprint 1 + 5 are engine + UI; tracked in the Powerloom repo.)

## v0.7.16 — 2026-05-01 (CLI)

**`weave open <token>` — paste-from-web bootstrap into a fully contextualised agent session.** Sprint 2 of the Weave Open Launch UX milestone (loomcli/8567b658). Engine half (POST/GET `/launches`, `tracker_launches` table, 5-min redeem cache) shipped in [Powerloom PR #301](https://github.com/shanerlevy-debug/Powerloom/pull/301).

### One command, end-to-end bootstrap

```
weave open lt_a8f3b2c1d4e5f6
```

Redeems the launch token from the Powerloom web UI's "Open in agent" modal, then on the user's machine: pre-flights (`git`, runtime binary), bare-clones the project to `~/.powerloom/repos/<project>.git/`, `git worktree add`s a fresh checkout to `~/.powerloom/worktrees/<scope>-<short_id>/`, syncs CLAUDE.md / AGENTS.md / GEMINI.md from the org's live conventions, registers the agent-session, drops `.powerloom-session.env`, then `os.execvpe`'s the runtime binary so the user's terminal becomes the agent. Brand-new user, blank folder, single paste, ~10s.

### What landed (six threads of sprint cli-weave-open-20260430)

- **Skeleton + redeem (`c78ead6d`):** new `weave open` command. Args: `<token>`, `--dry-run`, `--reuse <scope>`, `--resume <session-id>`, `--root <path>`. Maps redeem 404 / 410 / 401 to actionable messages with the launches URL.
- **Bare-clone + git worktree (`864c55a4`):** `loomcli/_open/git_ops.py`. `short_id` derives from `launch_id` so two clicks on the same scope create siblings (concurrency-safe), but a 5-min-cache resume returns the same launch_id → same short_id → idempotent worktree path → resume-on-interrupt works. Translates known git auth failures (401 / 403 / "Authentication failed") to `CloneAuthError` with hint.
- **Session register + env file (`5fab82ed`):** `loomcli/_open/session_reg.py`. POST `/agent-sessions`, write `<worktree>/.powerloom-session.env` (POWERLOOM_SESSION_ID / SCOPE / PROJECT_ID / LAUNCH_TOKEN_REDEEMED_AT / RUNTIME / BRANCH), idempotently append the env file to `<worktree>/.gitignore`.
- **Runtime hand-off (`53573d73`):** `loomcli/_open/runtime_exec.py`. RUNTIME_BINARIES table (`claude_code` → `claude`, `codex_cli` → `codex`, `gemini_cli` → `gemini`). `assert_runtime_available` pre-flight before clone. `os.execvpe` for clean signal propagation. Antigravity special-cased to instructional banner directing to `weave antigravity-worker`.
- **`--resume` / `--reuse` / `worktree-root` config (`5790b2d6`):** new `loomcli/_open/resume.py` (find_by_session_id + find_by_scope by mtime + best-effort dirty probe). New profile field `worktree_root` (`weave profile set --worktree-root <path>`) for users on small home drives.
- **Apply rules_sync directives (`53fddf29`):** `loomcli/_open/rules_sync.py`. Per-directive in `spec.rules_sync`, invokes `loomcli.commands.conventions_cmd.sync` per runtime so the worktree's CLAUDE.md / AGENTS.md / GEMINI.md reflect the org's *live* conventions (not just whatever was committed on the cloned branch). Per-runtime failures are non-fatal warnings.

### New surfaces

- `loomcli/_open/` private subpackage (one module per concern; orchestrator stays thin in `commands/open_cmd.py`).
- `loomcli/schema/launch_spec.py` — Pydantic mirror of the engine's `LaunchSpec` with `extra="ignore"` for forward-compat.
- `loomcli/config.py` + `loomcli/commands/profile_cmd.py` — `worktree_root` profile field.

### Test plan

70 new tests across `tests/test_open_*.py` covering each module independently + end-to-end through the orchestrator. Full suite: 855 passed, 1 skipped, 0 failed.

## v0.7.15 — 2026-04-30 (CLI)

**Plugin platform-MCP bridge — hosted Powerloom tools for CC sessions outside the monorepo.** Closes the gap that surfaced right after powerloom monorepo PR #290 (CMA push gating refactor). Operators running `weave register --token=pat-deploy-...` from a directory **outside** the powerloom monorepo wrote a valid deployment credential but a fresh CC session in that directory had **no** Powerloom or Weave tools available — the previous architecture only exposed platform tools via the monorepo's project-local `.mcp.json`.

### Plugin manifest declares a second MCP server

`plugin/.claude-plugin/plugin.json` adds `powerloom-platform` alongside the existing `powerloom-home` entry. The two complement each other:

| Server | Scope |
|---|---|
| `powerloom-home` (existing) | Local SQLite home-edition tools (offline-first, casual / home-tier users) |
| `powerloom-platform` (new) | Hosted platform tools (sessions, threads, projects, agents, skills, memory) via the operator's deployment credential |

### `powerloom-platform` is a stdio→HTTP MCP proxy

New module `plugin/mcp-server/powerloom_platform/`:

- **`credential.py`** — reads the deployment credential at startup using the same path-resolution pattern as `powerloom_home.auto_register` (XDG / `%APPDATA%` / `/etc/powerloom`). Refuses credentials missing `api_base_url` or `deployment_token`; refuses credentials with mismatched `credential_kind` (won't hijack a `gemini_cli` credential).
- **`__main__.py`** — connects upstream to `{api_base_url}/mcp` via `mcp.client.streamable_http` with `Authorization: Bearer {deployment_token}`. Forwards `tools/list` and `tools/call` requests transparently. Lazy connection (opens on first MCP request, not at boot) so unpaired hosts pay nothing.
- **Graceful zero-tools mode** when no credential exists OR malformed OR upstream unreachable. Operators see an empty tool list rather than errors; once they `weave register` and restart CC, tools appear.

### Test plan

14 stateless tests in `plugin/tests/test_powerloom_platform.py` covering credential resolution (missing/valid/wrong-kind/legacy/canonical-priority/malformed) + MCP URL composition (trailing-slash robustness, subpath handling). 50/50 plugin tests pass.

### Files

```
plugin/.claude-plugin/plugin.json                         MOD (added powerloom-platform mcpServers entry)
plugin/mcp-server/powerloom_platform/__init__.py          NEW
plugin/mcp-server/powerloom_platform/credential.py        NEW
plugin/mcp-server/powerloom_platform/__main__.py          NEW
plugin/tests/test_powerloom_platform.py                   NEW
loomcli/pyproject.toml                                    MOD (0.7.14 → 0.7.15)
CHANGELOG.md                                              MOD (this entry)
```

### Tracker

Powerloom thread `e1e61ca6` ("Plugin: bridge hosted Powerloom MCP tools into CC sessions outside the monorepo (Fix B)") — closed via the merge of PR #68.

### Companion

Powerloom monorepo PR #290 (CMA push gating refactor) closed `5dffcb15`. Together this PR + #290 answer Shane's two friction points from 2026-04-30:

1. Why does CMA fire on every skill upload? → #290 (gated on consumer)
2. Why don't tools appear after `weave register` outside the monorepo? → this release (bridge MCP server)

## v0.7.14 — 2026-04-30 (CLI)

**Plugin auto-register for Claude Code MCP server (Agent Lifecycle UX M2-P3).** Closes the M2 milestone (parent thread `6ab575d7`). Pairs with platform PR #263 (M2-P1) + loomcli PR #66 (M2-P2 / v0.7.13) shipped earlier today.

### Claude Code MCP server auto-registers sessions

When the powerloom_home MCP server boots (via the SessionStart hook in the Claude Code plugin), it now:

1. Looks for a Claude Code deployment credential at `~/.config/powerloom/deployment-claude_code.json` (written by `weave register --token=...` after M2-P2).
2. If found, POSTs `/agent-sessions` with the deployment_token to mint a session row tied to the deployment + working scope.
3. Tracks the session_id in module state for the lifetime of the MCP server.
4. Best-effort POSTs `/agent-sessions/{id}/end` at process shutdown.

This is **Option B** from the M2 design: plugins drop the requirement to explicitly call `weave agent-session register`. Sessions open automatically on IDE start. Operators with no deployment credential keep the legacy PAT-based flow (no breaking change).

### `powerloom_session_status` MCP tool

New tool exposed by the powerloom_home MCP server. Reports the auto-registered session for visibility:

```json
{
  "active": true,
  "session_id": "...",
  "scope": "powerloom",
  "agent_id": "...",
  "agent_slug": "claude_code_session",
  "deployment_id": "...",
  "api_base_url": "https://api.powerloom.org"
}
```

Returns `{"active": false}` with an actionable reason when no credential / failed POST.

### `weave agent-session register` falls back to deployment credential

When no PAT is configured (`weave login` never run or credential expired), `weave agent-session register` now discovers a deployment credential via M2-P2's `list_deployment_credentials()`. Prefers IDE-kind (claude_code, gemini_cli, codex_cli); falls back to host/default. Lets Codex/Gemini plugins (and operators on hosts paired via `weave register` only) call `weave agent-session register` without `weave login`.

### Operator workflow now end-to-end

1. UI: `/agents` → New agent → pick "Claude Code Session" template → submit (M2-P1 templates).
2. Detail page → Deployments tab → Add deployment → mint command (M2-P4 IDE-aware).
3. Run `weave register --token=...` on the laptop — credential lands at `~/.config/powerloom/deployment-claude_code.json` (M2-P2 per-user XDG).
4. Open Claude Code in any working tree — MCP server auto-registers the session (M2-P3).
5. UI shows "Session active" for the deployment.

### Tests

- 15 new tests in `plugin/tests/test_auto_register.py` covering credential discovery (per-kind, legacy fallback, wrong-kind skip, malformed JSON, missing fields), session POST (200/401/network-error), Bearer auth header, scope-from-cwd + override, session close.
- Full loomcli suite: 821 passed, 1 skipped.

### Files

```
loomcli/plugin/mcp-server/powerloom_home/auto_register.py    NEW (220 LOC)
loomcli/plugin/mcp-server/powerloom_home/__main__.py         MOD — auto-register + close hooks + new MCP tool
loomcli/loomcli/commands/agent_session_cmd.py                MOD — _client() falls back to deployment credential
loomcli/plugin/tests/test_auto_register.py                   NEW (260 LOC)
loomcli/CHANGELOG.md                                         MOD
loomcli/pyproject.toml                                       MOD (0.7.13 → 0.7.14)
```

## v0.7.13 — 2026-04-30 (CLI)

**Per-user XDG paths + multi-credential support (Agent Lifecycle UX M2-P2).** Pairs with Powerloom platform PR #263 (M2-P1) which added IDE agent templates + `credential_scope` to the register response. v0.7.13 is the loomcli side of bringing IDE-style agents (Claude Code / Gemini CLI / Codex CLI) up to deployment-bound credential parity.

### `weave register` honors server-driven `credential_scope`

The register response (M2-P1) now carries:

```
credential_scope: "host" | "user"
credential_kind:  "default" | "claude_code" | "gemini_cli" | "codex_cli"
```

`weave register --token=...` reads these and writes to:

- `host/default` → `/etc/powerloom/deployment.json` (M1 reconciler shape, unchanged)
- `user/<kind>` → `~/.config/powerloom/deployment-<kind>.json` (NEW for M2 IDE)

The kind suffix lets multiple IDE deployments coexist on one machine — Claude Code on Shane's laptop writes `deployment-claude_code.json`, Codex writes `deployment-codex_cli.json`, no collision.

### Multi-credential discovery

New `config.list_deployment_credentials()` returns a `{kind: payload}` dict of every readable credential. Used by:

- `read_deployment_credential(kind="claude_code")` — fetch a specific kind. Plugin auto-register flows (M2-P3 next) call this to find their credential.
- `read_deployment_credential()` — legacy API. Maintains v0.7.12 behavior: returns the host/default credential if present, falls back to alphabetical-first per-kind. The reconciler daemon in v0.7.12 keeps working unchanged.

### Refuse-to-clobber tradeoff

In v0.7.12 the register command refused pre-call if a credential existed at the default path. v0.7.13 moves the check post-call so M2 IDE registrations don't false-positive against an existing reconciler credential (different paths, no actual conflict). Tradeoff: a refused registration burns one server-side registration token (operator archives the orphaned deployment via UI).

### Migration from v0.7.12

Existing v0.7.12 deployments running with `weave register` keep working — the server's pre-M2-P1 register response was missing `credential_scope`/`credential_kind`, but the loomcli code defaults to `host/default` when those fields are absent. Same path, same daemon behavior.

To take advantage of M2 IDE deployments after upgrading:

1. Mint a Claude Code / Gemini CLI / Codex CLI deployment in the UI (templates available after M2-P1 + M2-P4 land).
2. Run `weave register --token=...` on the host — credential lands at `~/.config/powerloom/deployment-<kind>.json`.
3. M2-P3 (next PR) wires plugins to read these credentials at session start.

### Files

```
loomcli/loomcli/commands/agent_register.py    MOD — honor credential_scope/kind
loomcli/loomcli/config.py                     MOD — multi-credential paths + lookups
loomcli/CHANGELOG.md                          MOD
loomcli/pyproject.toml                        MOD (0.7.12 → 0.7.13)
loomcli/tests/test_register_command.py        MOD — 8 new M2-P2 tests
```

## v0.7.12 — 2026-04-30 (CLI)

**`weave register` + deployment-bound credentials (Agent Lifecycle UX P3).** Replaces the operator-host PAT-editing dance from v0.7.10 with a single registration command that mints a host-bound credential. Pairs with Powerloom platform PRs #246 (P1 — agent_templates registry) and #248 (P2 — `agent_deployments` lifecycle) which shipped the same day.

### `weave register --token=...`

Pair the current host with an agent deployment. Trades a one-shot registration token (`pat-deploy-...`, minted from `/agents/<id>` → Deployments tab → Add deployment in the UI) for a long-lived deployment token (`dep-...`) bound to this host.

```
sudo weave register --token=pat-deploy-AbCdEf...
sudo weave register --token=... --api-url=https://api.example.com   # self-hosted control plane
sudo weave register --token=... --output ~/.config/powerloom/dep.json   # custom path
sudo weave register --token=... --force   # overwrite an existing credential
```

Writes the credential to `/etc/powerloom/deployment.json` when `/etc/powerloom` is writable (Linux host-wide), else to the per-user XDG config dir. File is 0600 on POSIX. Refuses to clobber an existing credential without `--force` so the operator doesn't accidentally orphan an active deployment by re-registering on the wrong host.

The credential carries: `deployment_id`, `agent_id`, `agent_slug`, `deployment_token`, `api_base_url`, `runtime_config`. The daemon reads it on startup and uses every field — no operator-side `--agent`, no PAT, no `.env`.

### `weave agent run` — deployment mode

When `/etc/powerloom/deployment.json` exists, `weave agent run` (no positional argument) reads the credential and:

- Authenticates via the deployment_token (separate identity from the operator's PAT, scoped to the deployment row server-side).
- Uses the credential's `api_base_url` so a deployment registered against a self-hosted control plane stays pointed at it.
- On every tick:
  - **Long-poll runtime config:** `GET /deployments/{id}/runtime-config` with `If-None-Match: <etag>`. 304 most of the time → cheap. 200 on operator-pushed change → daemon picks up the new `interval_seconds`/`confidence_threshold`/`dry_run`/`model` for the next tick. No daemon restart required.
  - **Heartbeat:** `POST /deployments/{id}/heartbeat`. Powers the UI's online/degraded/offline status. A 401 here means the deployment was archived server-side; the daemon exits with code 3 and a clear message ("re-pair this host with `weave register`").

The legacy PAT path (`POWERLOOM_ACCESS_TOKEN` + `weave agent run <agent>`) still works when no deployment credential is present — the daemon falls back transparently.

### `deploy/reconciler/docker-compose.yml`

Read-only bind mount `/etc/powerloom:/etc/powerloom:ro` added so the container can read the host-side credential. Operator workflow on a fresh EC2 box:

```
# (1) Install loomcli
pip install loomcli==0.7.12

# (2) Mint a registration token in the UI, then on the host:
sudo weave register --token=pat-deploy-AbCdEf...

# (3) Bring the daemon up. It reads /etc/powerloom/deployment.json
#     through the bind mount; no .env editing.
sudo systemctl enable --now powerloom-reconciler
```

Compared to v0.7.10's `.env`-and-PAT story, the v0.7.12 path drops six steps and one cross-tool credential lookup.

### Migration from v0.7.11

Existing v0.7.11 deployments running with `POWERLOOM_ACCESS_TOKEN` keep working — the daemon only switches to deployment mode when it finds `/etc/powerloom/deployment.json`. To migrate:

1. Mint a deployment + registration token in the UI (`/agents/<id>` → Deployments tab).
2. Run `sudo weave register --token=...` on the host.
3. Restart the daemon (`sudo systemctl restart powerloom-reconciler` if using the bundled unit).
4. Optionally remove `POWERLOOM_ACCESS_TOKEN` from the `.env` once the daemon is happy.

The PAT-bound mode stays in the codebase indefinitely for dev workflows + CI smoke tests where minting a UI deployment is overkill.

### Files

```
loomcli/loomcli/commands/agent_register.py             NEW
loomcli/loomcli/commands/agent_daemon.py               MOD
loomcli/loomcli/config.py                              MOD (deployment-credential helpers)
loomcli/loomcli/cli.py                                 MOD (wire `weave register`)
loomcli/deploy/reconciler/docker-compose.yml           MOD (mount /etc/powerloom)
loomcli/CHANGELOG.md                                   MOD
loomcli/pyproject.toml                                 MOD (0.7.11 → 0.7.12)
loomcli/tests/test_register_command.py                 NEW
loomcli/tests/test_daemon_deployment_credential.py     NEW
```

## v0.7.11 — 2026-04-29 (CLI)

**Two operator-host papercuts surfaced during the 2026-04-29 EC2 reconciler bring-up.** Both cosmetic-ish but trip the operator at exactly the wrong moment (during initial bring-up); fixing them now keeps the v0.7.10 deploy story honest.

### Fix: systemd unit's `docker compose pull` fails on locally-built image

`deploy/reconciler/powerloom-reconciler.service` ran `ExecStartPre=/usr/bin/docker compose pull --quiet` on every restart. The companion `docker-compose.yml` builds the image locally as `powerloom-reconciler:local` (no registry push), so `compose pull` has no remote tag to fetch and errors out. With `--quiet` the failure was silent enough that operators thought the unit was healthy when actually `ExecStart` was inheriting the failure.

Replaced with `ExecStartPre=/usr/bin/docker compose build --pull`. Build is incremental + cached when nothing changed, so the no-op restart cost is negligible. The `--pull` flag still ensures the python:3.12-slim base layer gets refreshed when an upstream rebuild lands. Inline comment in the unit explains the swap and points at the registry-push path if we ever publish the image.

### Fix: `weave --version` reports stale version

`loomcli/__init__.py` had `__version__` hardcoded as a string constant (`"0.7.7"` after the v0.7.10 release — drifted because the constant wasn't bumped at release time). v0.7.11 sources the version from package metadata via `importlib.metadata.version("loomcli")`, with a `0.0.0+unknown` fallback for editable / source-tree usage where the wheel hasn't been installed.

Single source of truth for the version is now `pyproject.toml`. Future releases bump it once and `__version__` follows automatically — no more drift.

### Other

- Dockerfile comment block updated to recommend v0.7.11 over v0.7.10 (same floor for `POWERLOOM_ACCESS_TOKEN` env-var auth; v0.7.11 just fixes the bring-up papercuts).

## v0.7.10 — 2026-04-29 (CLI)

**EC2 / VPS deployment artifacts for the reconciler daemon + `POWERLOOM_ACCESS_TOKEN` env-var auth.** Closes the operator-host packaging gap that v0.7.9 left open — operators running the daemon in Docker or systemd now have an opinionated bring-up path that doesn't require bind-mounting a credentials file.

### `POWERLOOM_ACCESS_TOKEN` env-var auth

`loomcli.config._read_credentials_file()` now resolves the access token from `POWERLOOM_ACCESS_TOKEN` first, falling back to the legacy `<POWERLOOM_HOME>/credentials` file when the env var is unset. Resolution rules:

- Env var with whitespace-only value falls through to file (catches docker-compose interpolation footguns).
- Trailing whitespace / newlines stripped (catches copy-paste-with-newline footguns).

The TTY-host `weave login` flow is untouched; the file-based path remains the default for desktop usage. Six new tests in `test_env_access_token.py`.

### `deploy/reconciler/` — operator-host artifacts

Five files for spinning up the daemon on an EC2 / VPS / on-prem box:

- **`Dockerfile`** — slim Python 3.12, pinned to this loomcli release (`ARG LOOMCLI_VERSION=0.7.10`). Non-root user, `weave doctor --quiet` healthcheck, `weave agent run reconciler` as PID 1.
- **`docker-compose.yml`** — service definition with env-passthrough for `POWERLOOM_ACCESS_TOKEN` / `POWERLOOM_API_BASE_URL` / `POWERLOOM_AGENT` / dry-run / interval / confidence flags.
- **`.env.example`** — config template with mint instructions for the PAT.
- **`powerloom-reconciler.service`** — systemd unit that wraps `docker compose up` with restart-on-failure + journald logging.
- **`setup-ec2.sh`** — idempotent bootstrap. Installs Docker + compose-plugin, creates the `powerloom` system user, copies assets to `/opt/powerloom-reconciler/`, seeds `.env` from the example, installs the systemd unit. Operator finishes by editing `.env` and running `systemctl enable --now powerloom-reconciler`.

The full operator runbook lives in the Powerloom monorepo at `docs/operating-self-hosted-agents.md`.

## v0.7.9 — 2026-04-29 (CLI)

**`weave agent run` — universal self-hosted-agent daemon (PR #59).** Closes the operator-side half of the universal-self-hosted-daemon reframe (Powerloom thread `aad43ba0` + Powerloom PR #228). Operators run the daemon on their own host (your local server, a customer's, anywhere) to drive any agent registered with `runtime_type='self_hosted'` against the platform's `GET /agents/{id}/work-queue` endpoint.

### `weave agent run`

```
weave agent run <agent>                foreground daemon (default 10s tick)
weave agent run <agent> --once         single tick + exit (cron-friendly)
weave agent run <agent> --dry-run      decide but don't take destructive actions
weave agent run <agent> --interval 30  poll cadence
weave agent run <agent> --confidence 0.9  action threshold (default 0.8)
weave agent run <agent> --limit 25     items per tick (default 10)
```

Foreground only — Ctrl+C stops cleanly. For background runs, use the OS supervisor of choice (systemd, supervisord, NSSM on Windows, or `nohup … &`).

### Skill registry

The daemon dispatches each work item to a registered skill handler keyed on the item's `task_kind`. The reconciler is the v1 reference implementation (`pr_reconciliation` skill — wraps `/reconciler/decide` + `/reconciler/{rebase,merge}`, respects `--dry-run`, `--confidence`, and the platform-side approval gate). Future self-hosted agents (legal review, ad optimization, anything) plug in by registering a new skill — no new CLI surface, no new platform endpoints.

### Architecture invariants

- Daemon never holds destructive credentials. Every action flows through the platform API; the daemon is a polling-loop dispatcher.
- Decisions billed against the org's BYOK `runtime_type='cma'` runtime credential — set up once via the platform UI, no operator-host configuration of provider keys.
- Daemon is stateless between ticks. The platform DB is the source of truth for `reconciler_pr_state` and any future skill's state table.

### Tests

- 753 passing (743 prior + 10 new in `test_agent_daemon.py`).

## v0.7.8 — 2026-04-28 (CLI)

**Token-efficiency pass for agent consumers (PR #57).** Closes powerloom threads `#256`-`#261`. Tightens the JSON surface and trims the bytes-per-call budget for Claude Code / Codex / Gemini sessions driving weave non-interactively.

### Standardized JSON surface

- Every subcommand now honors the global `-o json` (or the auto-flip from `POWERLOOM_FORMAT=json` / agent-mode detection). Per-command `--json` flags retired in favor of the global option — single output policy across `apply`, `plan`, `destroy`, `get`, `audit`, `approval`, `agent`, `agent-session`, `compose`, `conventions`, `doctor`, `import-project`, `migrate`, `plugin`, `profile`, `project`, `session`, `skill`, `sprint`, `thread`, `workflow`.

### Brief lists

- `weave thread list / show` and `weave sprint list / show` default to a compact row format under agent mode (id8 + slug + status + title) instead of the rich table. Drops ~60% of the bytes returned for typical "scan threads I own" calls. Pass `-o table` to force the wide form.

### Bulk + multi-ref ops

- **`weave thread bulk-create`** — accepts a YAML/JSON file of thread specs and creates them in one round-trip. Returns id-only by default for chaining into subsequent commands.
- **`weave sprint add-thread`** now accepts multiple refs in one invocation: `weave sprint add-thread <sprint> ref1 ref2 ref3`. Previously required N separate calls.
- **`--id-only` / `--slug-only`** flags on `thread create`, `sprint create`, `project create` — emit just the new id (or slug) on stdout for trivial chaining without `jq`.

### Tests

- 743 passing. New + updated coverage in `test_thread_cmd.py`, `test_sprint_cmd.py`, `test_project_cmd.py`, `test_command_registry.py`, `test_doctor_plugin_cmd.py`, `test_profile_cmd.py`, `test_auto_json_output.py`, `test_agent_session_cli.py`. New shared fixture in `conftest.py`.

### Pairs with

- The `--from-branch` slug derivation already in v0.7.2 — agents using `register --from-branch` followed by repeat `thread bulk-create` sessions now see roughly half the prior token usage on the discovery + setup phase.

## v0.7.7 — 2026-04-28 (CLI)

**Windows hardening + JSON-by-default for agents + new `weave project` group.** Sister-agent PR #54 + rebase-fix on top of v0.7.6.

### Windows / cross-platform

- `weave plugin install <client> --execute` now resolves the client executable's full path via `shutil.which()` and execs that path directly. Fixes Windows npm-shim installs where PowerShell can run `codex` but Python's `subprocess.run(["codex", ...])` raises `[WinError 2]`.
- `weave plugin install claude-code --execute` documented as the standard Claude Code setup path; forwards `--project-dir` and `--use-env-substitution` to `weave setup-claude-code`.
- Windows plugin assets now export to `%USERPROFILE%\.powerloom\plugins` by default so Codex/Gemini/Claude can read the marketplace directory even when `weave` runs under Microsoft Store Python AppData virtualization. `POWERLOOM_HOME` and `POWERLOOM_PLUGIN_HOME` still override.
- New `tests/test_stdio_encoding.py` ensures rich's box-drawing chars render correctly under cp1252 / cp932 (the long-standing Windows table-rendering issue).

### Agent ergonomics

- **Auto-JSON output** — when `POWERLOOM_FORMAT=json` is unset, the CLI now sniffs whether stdout is a TTY (or set by an agent runner). Agent processes get JSON automatically; humans get rich tables. Manual `--json` still wins.
- New `tests/test_auto_json_output.py` covers the heuristic.

### `weave project`

- New top-level command group: `weave project list / get / create / archive`. Wraps the engine's `/projects` endpoints with the same UX as `weave thread`.

### Pairs with

- v0.7.6's `weave thread search` (already shipped) — `weave project list` is the project-side companion.

## v0.7.6 — 2026-04-28 (CLI)

**`weave thread search <query>`** — global cross-project thread search. Pairs with the new search bar on the console UI's `/projects` page.

```
weave thread search connie
weave thread search "fix flaky"
weave thread search agent-onboarding --limit 10
```

Closes powerloom thread `2dbbefde`. Same engine endpoint (`GET /threads/search`); case-insensitive substring on title + description + slug, org-scoped.


## v0.7.5 — 2026-04-28 (CLI)

**Friendly name for agent sessions.** Demo-day ask from Shane: when registering an agent session, give it a human-readable display name distinct from the machine slug.

### What changed

- **`weave agent-session register --friendly-name "Shane CC laptop"`** — new optional flag. Persists to the new `agent_sessions.friendly_name` column (Powerloom migration 0074). The console UI shows this as the primary label on the sessions list and falls through to the machine slug when null.
- **`weave agent-session start`** — interactive prompt added: after scope + summary, asks for a friendly name (default = the scope slug, so pressing Enter keeps the existing behavior).

### Pairs with

- Powerloom migration 0074 (adds `agent_sessions.friendly_name` column).
- Powerloom UI `/agents?tab=sessions` — new "Coordination sessions" section listing CC / Codex / Gemini / Antigravity sessions registered via `weave agent-session register`.


## v0.7.4 — 2026-04-28 (CLI)

**Thread management UX (PR #49 by sister agent).** Closes powerloom thread `0040b2b3`.

### What changed

- **`Slug` column** in `weave thread list` output. Threads are easier to identify at a glance + reference in subsequent commands.
- **Short 8-char ID resolution** for `pluck` / `done` / `show` / `update` / `reply` — paste the leading 8 chars of an id, the CLI resolves it. (Equivalent to `git log --abbrev-commit`.)
- **Profile `default_project`** — set once via `weave profile set default_project <slug>` and bare commands like `weave thread list` use it without `--project`.
- **CWD-based project detection** — repo paths matching `ui/`, `loomcli/`, etc. resolve to `powerloom-ui`, `loomcli` projects automatically.
- **`bare thread list`** now falls back to default project (was: hard-required `--project` or `--mine`).

### Compat

- `_CLIENT_TO_ACTOR` map widened to accept `human` / `cma` / `reconciler` actor kinds (added in v0.7.2 for non-dev paths but missed in #49's actor-kind validator).

## v0.7.3 — 2026-04-28 (CLI)

**`weave conventions sync` auto-detects OU scope.** Closes powerloom thread `53b781c8`. The SessionStart hook in `.claude/settings.json` no longer needs a hardcoded `--scope` argument — the CLI walks four detection paths in order until one resolves.

### Detection chain

1. Explicit `--scope <dotted-path>` (existing, highest priority)
2. Cached scope from the last successful sync in this config dir (existing, v0.6.8+)
3. **NEW:** Active sub-principal's home OU — fetched via `/me`, then walked through `/ous/tree` to build the dotted slug path
4. **NEW:** `git remote get-url origin` → matched against `tracker_projects.github_repo_url`; the project's `ou_id` becomes the scope (pairs with Powerloom migration 0072)

If all four fail, the error message now lists each path tried.

### What you can drop

The `.claude/settings.json` hook can shrink from:
```bash
weave conventions sync --scope bespoke-technology.powerloom --runtime claude_code --quiet
```
to:
```bash
weave conventions sync --runtime claude_code --quiet
```

(Recommended once you've signed in once: the auth-gated detection paths need a valid token. The hook continues to work with the explicit `--scope` for full back-compat.)

### Tests

- 15/15 passing. Existing `test_sync_no_scope_no_cache_exits_2` updated to assert the new "Could not determine OU scope" error shape.


## v0.7.2 — 2026-04-28 (CLI)

**Agent-session registration for non-developers.** The `register` command no longer assumes a git checkout. Three explicit scope-detection paths, friendlier errors, and a new interactive `start` command for users who don't have (or want) a branch dependency.

### What changed

- **`weave agent-session register --workspace-id <id>`** — new path for hosted clients (Antigravity, mobile, in-browser sessions) that don't have a local git checkout. Workspace id becomes the scope (slugified + date-suffixed).
- **`weave agent-session register --scope <slug>`** — explicit path. `--summary` now auto-generates from `--scope` if omitted, so a single `--scope` flag is enough.
- **`weave agent-session start`** — new interactive command for PMs / ops / non-dev users. Prompts for a scope + summary, defaults `--actor-kind=human`, never touches git.
- **Smarter error messages** — when no scope-detection input is supplied, the error suggests the right path based on cwd context (cwd-is-git → `--from-branch`; cwd-is-not-git → `--scope` / `--workspace-id` / `start`).
- **`--from-branch` non-session-branch behavior** — instead of hard-erroring on branches that don't match `session/<scope>-<yyyymmdd>`, the CLI now slugifies the branch name and appends today's date with a warning, so devs working off oddly-named feature branches aren't blocked.

### Pairs with

- Powerloom thread `a9091bbc` (de-couple agent-session registration) and `51174e34` (attach GitHub repo info to projects when agents connect).
- Engine route `/agent-sessions` already accepts nullable `branch_name`; this release closes the CLI-side gap.

### Tests

- 24/24 in `test_agent_session_cli.py` (3 new: `--scope` alone, `--workspace-id`, bare-`register` smart-error)
- 1 new for `start` interactive prompt path


## v0.7.1 — 2026-04-27 (CLI)

**Plugin install error UX.** `weave plugin install <client> --execute` now pre-flight-checks that the client binary (`gemini` / `codex` / `claude`) is on `PATH` before subprocess-ing into it. When the binary is missing, the error message points the user at the install URL instead of surfacing a bare `[WinError 2] The system cannot find the file specified`.

Reproducer that prompted the fix:
```
PS> weave plugin install gemini --execute
gemini extensions install C:\Users\…\plugins\0.7.0\gemini\powerloom-weave --consent --skip-settings
Install failed: [WinError 2] The system cannot find the file specified
```

After:
```
PS> weave plugin install gemini --execute
gemini extensions install C:\Users\…\plugins\0.7.0\gemini\powerloom-weave --consent --skip-settings
Install failed: 'gemini' is not on PATH.
  Install the Gemini CLI first: https://github.com/google-gemini/gemini-cli (npm: `npm install -g @google/gemini-cli`).
  Once installed, re-run weave plugin install gemini --execute.
```

Same pre-flight applies to `codex` and `claude` install commands.


## v0.7.0 — 2026-04-27 (CLI)

**Cascading conventions.** `weave conventions sync` now fetches the **effective** convention set for the agent's OU — folding org-wide policy, every OU ancestor, the leaf OU, and (in future) project-scoped conventions into a single rule list with child-wins-with-deny semantics. Org admins can author once at the root and have every descendant session pick it up automatically.

### What changed

- **`weave conventions sync --scope <ou-path>`** — `--scope` is now an OU dotted path (e.g. `bespoke-technology.powerloom` or `/dev-org/engineering`) and the CLI calls the new `/memory/semantic/conventions/effective?ou_path=…` endpoint. Older engines that don't have `/effective` (Powerloom < 2026-04-27) get a transparent fallback to the legacy `/match` route, so an upgraded CLI doesn't break an older deployment.
- **Marker block now shows inheritance.** Each rendered convention carries a source-scope tag (`org`, `ou`, `project`) and, when inherited from above, a `_Inheritance: org → ou:engineering → ou:powerloom_` trail so agents reading `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` know which level authored each rule.
- **Body items folded.** When a child OU restates a parent's bullet, the parent's contribution is dropped and the child's text takes its position. A child convention with `deny_parent: true` truncates the chain entirely above it.

### Pairs with

- **Powerloom migration 0070** — adds `scope` (`'org'|'ou'|'project'`), nullable `ou_id`, `project_id` FK, `deny_parent` boolean. Backwards-compat default of `scope='ou'` so existing rows keep working.
- **`/memory/semantic/conventions/effective`** endpoint — cascading resolver. Walks `ou_closure` from the leaf upward, applies child-wins-with-deny, returns folded rows with `inheritance_chain` for UI/CLI rendering.
- **`recommended_loomcli_version`** bumped to `0.7.0` in the engine's capabilities response.

### Tests

- Existing 28 convention tests pass on the new schema.
- 14 new tests in `test_memory_conventions_cascading.py` (engine side) covering org-level create, scope guards, parent/child cascade, deny_parent truncation, archive exclusion, inheritance-chain shape.


## v0.6.9 — 2026-04-27 (CLI)

**Pip-install ergonomics + agent UX.** Combines two independently-landed feature blocks under one release: bundled plugin assets ship inside the wheel (no more git-clone-of-loomcli prerequisite for any client), and the agent surface picks up auto-detection + contextual defaults + a batch runner.

### Bundled plugin assets

- **`weave plugin path <client> [--json]`** — prints the exported plugin path for a given client (`claude-code`, `codex`, `gemini`, `antigravity`). Triggers asset export on first call to `<config_dir>/plugins/<version>/`.
- **`weave plugin install <client> --execute`** — now resolves the bundled-asset path and shells out to the client's native install command. Dry-run (no `--execute`) prints a copy-pasteable form instead, formatted with `subprocess.list2cmdline` on Windows / `shlex.join` on POSIX so no shell escaping needed.
- **`weave plugin doctor`** JSON output now includes `export_root` and per-client `install_command` fields so scripting can chain into plugin doctor without re-running install.
- **`weave doctor`** picks up: `python.executable`, `python.version`, `stdio.encoding` (helpful on Windows where cp932/cp1252 break rich rendering), and `plugin.export_root`.

### Agent UX & workflow ergonomics

- **Agent Mode detection**: auto-detects AI agents (`GEMINI_CLI`, `CLAUDE_CODE`, etc.) and defaults output to JSON.
- **Contextual defaults**: agent commands (`status`, `ask`, `chat`, etc.) now use the agent/OU linked to the current git branch's active coordination session.
- **Hierarchical OU discovery**: `weave get ous --tree` provides a visual and JSON-standardized tree of the organization unit hierarchy.
- **Batch command**: `weave batch` allows sequential execution of multiple weave commands in a single process, improving cross-shell compatibility.
- **Workflow ergonomics**: `weave agent-session init <branch>` handles branch creation and registration in one step; `--from-branch` now supports flexible branch names.
- **Agent-friendly errors**: standardized JSON error objects via `PowerloomApiError.to_dict()` when in Agent Mode.
- **`weave agent-session register --from-branch`** — error path now prints `cwd:` so the user sees which directory `loomcli` thought it was in. `--if-not-active` honors `--json`: emits `{"status": "already_active", "session": {…}}` instead of swallowing the bail-out so callers can detect re-runs.

### Capabilities pin

- Engine PR `Powerloom#171` updates `recommended_loomcli_version` from `0.6.8` → `0.6.9` so existing consumers get the upgrade nudge through `weave whoami` / capability checks.

### Tests

- 27 new + updated for plugin asset export (`test_doctor_plugin_cmd.py`) and `register --from-branch --json` branches (`test_agent_session_cli.py`)
- Plus the agent-mode + batch coverage from the init-loomcli-20260427 branch
- Full suite still green


## v0.6.8 — 2026-04-27 (CLI)

**Conventions sync.** Surfaces OU-scoped Powerloom conventions (engine v064 / `/memory/semantic/conventions/*`) into the project-rules file the runtime reads at session start.

### New commands

- **`weave conventions sync --scope <dotted-path> [--runtime claude_code|codex_cli|gemini_cli|antigravity|all] [--workdir DIR] [--dry-run] [--quiet]`** — Fetch conventions matching the OU scope-ref + write them to CLAUDE.md / AGENTS.md / GEMINI.md inside an HTML-comment marker block. Re-syncs replace the block; hand-edits outside survive. Caches the last-used scope so subsequent runs (e.g. SessionStart hook) don't need `--scope`.
- **`weave conventions show --scope <dotted-path>`** — Read-only preview of what would sync.
- **`weave conventions list [--status active|archived]`** — Cross-OU view of every convention visible to your org.

### Pairs with

- **Powerloom PR #169** — adds `weave conventions sync --scope bespoke-technology.powerloom --runtime claude_code --quiet` to `.claude/settings.json` SessionStart hook so every Claude Code session opening the Powerloom repo gets the latest conventions auto-merged.
- The convention authoring/management UI is filed as a follow-up thread (`ui-convention-authoring-management-page` in powerloom-ui).
- Project-scope conventions (today OU-only) is filed as a follow-up (`conventions-project-scope-extension` in powerloom-engine).
- Scope auto-detection (drop the hardcoded `--scope` from the hook) is filed as a low-priority follow-up.

### Tests

- 15 new in `test_conventions_cmd.py` covering: discovery, show (table + empty), sync (create / replace / append / dry-run / all-runtimes / cached-scope / no-scope-no-cache / invalid-runtime / quiet / corrupted-half-marker), list (table + status filter)
- Full suite: **702 passed** (was 684 in v0.6.7)


## v0.6.7 — 2026-04-27 (CLI)

**AX improvements + first-class agent management.** Surfaces Gemini's PR #41 work + the v067 follow-ups that landed since v0.6.6.

### New commands

- **`weave configure`** — AWS-style interactive wizard for profiles + PATs. First-time-setup-friendly.
- **`weave profile switch <name>`** — quick environment toggle between configured profiles.
- **`weave agent sub-principal mint`** — easy identity management for spinning up new sub-principals from the CLI.
- **`weave agent-session update`** — metadata updates for an existing coordination session (capabilities, scope, etc.).

### Cross-cutting

- **`--output` flag (global)** + **`POWERLOOM_FORMAT=json` env var** — every command can now emit machine-readable JSON for agent/CI consumption. Single place to opt into JSON instead of per-command flags.
- **Unified `agent_cmd.py`** — observability commands (status, logs, recent-work) folded back into the main agent module. The old `agent_observe_cmd.py` retired.

### Tests

- 684 passed (same as v0.6.6; this release is mostly UX surface, no test count delta)


## v0.6.6 — 2026-04-27 (CLI)

**Bulk-migration toolkit + sprint hierarchy + empty-folder bootstrap.** Surfaces a handful of post-v0.6.5 features so the post-org-reorg workflow has the CLI primitives it needs.

### New commands

- **`weave thread move <ref> --to <project> [--force]`** — Cross-project thread move with safe cleanup. Two-phase: without `--force` returns 409 with the cleanup plan (sprint memberships in source project, milestone/parent FK references, dependency edges that would become cross-project); with `--force` applies the cleanup + the move with full audit reply on the moved thread. Pairs with Powerloom #164 (`POST /threads/{id}/move`).
- **`weave agent-session bootstrap`** — Empty-folder onboarding flow for new clients (PR #37). Initializes a workspace from a project's bootstrap config without requiring a pre-existing checkout.

### Sprint hierarchy

- **`weave sprint create --milestone <uuid>`**, **`weave sprint update <ref> --milestone <uuid>`**, **`weave sprint list --milestone <uuid>`** — Sprints can now nest under milestones (Project > Milestone > Sprint > Thread > Subthread). Pairs with Powerloom #165 (migration 0068 + service-layer support). UUID-only today; milestone slug-resolution lands as a follow-up.

### Tests

- 4 new in `test_sprint_cmd.py` (--milestone create/update/list + UUID validation)
- 4 new in `test_thread_cmd.py` (move endpoint UUID/slug/force/409 paths)
- 24 new in `test_agent_session_cli.py` for bootstrap (Codex contribution)
- Full suite: **684 passed** (was 650 in v0.6.5; +34 net).


## v0.6.5 — 2026-04-27 (CLI)

**Sub-principal pipeline + sprint CLI + slug resolver across the board.** First non-rc release in the v0.6 line — drops the `-rcN` suffix per the new versioning convention (releases are stable by default, pre-releases happen on side-branches when needed).

### New commands

- **`weave sprint`** family — `create / list / show / update / activate / complete / archive / delete / add-thread / remove-thread / threads`. Pairs with the W1.5.2 engine routes (Powerloom #156) so sprints can be dogfooded end-to-end without curl. Sprint addressing accepts UUID, `project:slug`, or bare slug — same shape as W1.5.1 thread slugs.
- **`weave thread tree / sprint-tree / orphans`** — ASCII tree renderers for the W1.5.3 engine routes (Powerloom #153). `tree` shows the parent_thread_id + parent_of-edge tree rooted at one thread. `sprint-tree` does the same rooted at a sprint's top-level threads. `orphans` lists threads with no parent and no sprint membership ("what's loose right now?" triage).

### Sub-principal pipeline (v067 onboarding sprint)

The auto-attribution path that's been a no-op since v0.6.4-rc1 is finally wired end-to-end:

- `weave agent-session register` now find-or-creates a sub-principal named `<actor_kind>:<scope>` against `POST /me/agents` AFTER the agent-session POST succeeds. Caches the UUID in a new per-scope file at `<config_dir>/active-subprincipal-<scope>.txt` (since shell SessionStart hooks can't propagate `export VAR=…` to the parent shell — the env-var-only path was a no-op for every real session).
- `weave thread create / reply / pluck` resolves the active sub-principal in two tiers: (1) `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var (explicit override; wins when set), (2) the per-scope cache file (with branch derivation via `git rev-parse`). Falls back to "no attribution" gracefully when neither source resolves.
- New `loomcli.config.active_subprincipal_file(scope)` helper for the cache-file path.

After this release, every CC session running the existing `.claude/settings.json` SessionStart hook auto-stamps `session_attribution` on every tracker action with no human ritual. Codex/Antigravity/Gemini hooks are open follow-ups (tracked in the v067-onboarding sprint).

### Slug resolver (W1.5.1 + W1.5.3 dogfood)

- **`weave thread show / pluck / reply / done / close / wont-do / update`** all accept UUID, `project:slug`, or bare slug now. Slug shape validated client-side before hitting the network; 404 from `/by-slug` lookups renders a project + slug-specific error.

### Documentation

- README + plugin SKILL files updated for the new commands and the auto-attribution flow.
- The connect-your-agent doc references the new sprint + tree commands as primary examples for "you've onboarded — now what?"

### Tests

- 23 new in `test_sprint_cmd.py` (full sprint CRUD + slug resolution + JSON output + lifecycle parametrize).
- 9 new across `test_thread_cmd.py` + `test_agent_session_cli.py` for the sub-principal pipeline (env-var precedence, file fallback, per-scope isolation, graceful network failure, end-to-end attribution).
- Full suite: **650 passed**.


## v0.6.4-rc2 — 2026-04-27 (CLI)

**Agent onboarding friction reduction + security hardening.** This release focuses on making the first-time setup experience smoother for agents and humans alike, paired with server-side capability discovery.

### New Features & Fixes

- **`weave doctor` version health** — Now retrieves `min_loomcli_version` and `recommended_loomcli_version` from the server's `/capabilities` endpoint. Warns the user with a clear `pip install -U loomcli` hint if their CLI version is outdated or below recommendation.
- **`weave plugin doctor --fix`** — Automated repair for common setup issues. Detects missing binary or plugin files and executes the necessary install/enable commands (e.g., automatically enabling the Gemini extension).
- **CI Pipeline** — Added GitHub Actions workflow for automated `ruff` linting and `pytest` execution on every push and PR.
- **Dependency Update** — Added `packaging` to core dependencies for robust PEP 440 version comparison.
- **Onboarding Guide** — Added links to the new "Connect Your Agent" guide in README and command outputs.


## v0.6.4-rc1 — 2026-04-27 (CLI)

**Substantial new command surface + dogfood loop hardening.** Ships the `weave thread` family that CLAUDE.md / GEMINI.md / AGENTS.md §4.10 has been referencing, plus auto-attribution, plus several Codex/Gemini plugin install fixes, plus the post-prod-deploy MCP setup hotfix.

### New commands

- **`weave thread create / pluck / reply / done / close / wont-do / list / show / update`** — the canonical CLI surface for the §4.10 tracker-thread workflow. Closes the "use weave thread create" parenthetical that was an escape-hatch in the project-rules docs.
- **`weave thread my-work [--watch --interval N]`** — heads-up display for "what am I working on right now," with poll-mode + summary-line + JSON output.
- **`weave agent-session status / watch`** — inspect coordination sessions with optional polling.
- **`weave thread reply` auto-stamping** — when `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var is set, both `create` and `reply` automatically populate `metadata_json.session_attribution` so audit trails distinguish "you" from "your agent session acting as you." Pure opt-in — direct human callers without the env see no behavior change. `--no-attribution` flag opts out per-command.

### Skill + plugin updates

- **`powerloom-onboarding` skill** — fresh-agent first-10-minute walkthrough for Claude Code, Codex CLI, and Gemini CLI. Mirrors across all three plugin packages.
- **`weave-tracker` skill** — full thread-lifecycle reference; the §4.10 "fallback when subcommands aren't shipped" section retired (the subcommands are here now).
- **Codex marketplace + Gemini install fixes** — corrects plugin install paths + the install instructions in the docs.

### Hotfix: `weave setup-claude-code` writes correct .mcp.json schema

Pre-hotfix, `weave setup-claude-code` wrote `.mcp.json` with server entries at the top level (no `mcpServers` wrapper), which Claude Code rejects with `mcpServers: Does not adhere to MCP server configuration schema`. The hotfix:
- Writes the canonical `{"mcpServers": {...}}` shape.
- Auto-migrates pre-hotfix broken files on next run (detects + relocates top-level server entries; removes the duplicates).
- Switches default to writing the literal `Bearer pat_xxx` token instead of `${POWERLOOM_MCP_TOKEN}` substitution (CC's HTTP-transport MCP config doesn't reliably env-var-substitute inside header values; verified 2026-04-27 against api.powerloom.org). New `--use-env-substitution` flag opts back into the `${VAR}` form for committed/shared workspaces.

### Co-deployed with Powerloom #146 (P0 routing-loop fix)

This release pairs with the engine-side fix to the AWS ALB routing loop that was returning HTTP 463 to fresh CC sessions. Both must be deployed together for the demo loop to work end-to-end.

### Tests

627/627 pass (524 pre-existing + 22 weave-thread + new my-work + L4 auto-stamp + setup-claude-code migration tests). Two pre-existing unrelated failures remain on `main` (covered in their own threads).

- **Client plugin packages**: keeps the existing Claude Code plugin and adds OpenAI Codex + Gemini CLI plugin/extension packages under `plugins/`.

## v0.6.3-rc1 — 2026-04-26 (CLI)

**v057 closeout + v061-v064 first slices — schema-version negotiation, target-OU compose gating, TypeDefinition + Convention stdlib derivations.** Four landed-together commits from the v057-closeout side branch, reconciled onto v0.6.2-rc1 (the import-project release) and re-bumped to v0.6.3-rc1.

### New

- **`X-Powerloom-Schema-Version` header** — `loomcli/client.py` injects the header on every request, sourced from `loomcli.schema.SCHEMA_VERSION`. Engines pre-dating v057 ignore it; engines on v057+ run it through the `core/schema_version_check.py` 426 gate (engine companion in PR #133, ships in same deploy).
- **426 response formatter** — `_format_version_mismatch()` recognizes the engine's canonical 426 body shape (`error.detail.{supported_versions, client_sent}`) and emits an upgrade-hint message; non-canonical 426s fall back to the default extractor.
- **`metadata.target_ou_path` on Compose manifests** — manifest authors can publish a composed kind to a specific OU instead of the default org root. Engine-side resolution lands in PR #133 (`services/compose.py` resolve_target_ou_id + migration 0054).
- **TypeDefinition stdlib derivation** — first cell-level memory primitive shipped as a v2 stdlib kind. New schema at `schema/v2/stdlib/type-definition.schema.json`; loomcli builder at `loomcli/schema/v2/stdlib/type_definition.py`; example manifest at `examples/minimal/type-definition.yaml`.
- **Convention stdlib derivation** (v064) — `compose(Policy[intent], Scope[applies_to])`, the 11th stdlib kind. Top-down authored rules with `enforcement_mode` + `status` enums and `references_json` cross-links. Schema at `schema/v2/stdlib/convention.schema.json`.

### Schema

- 2.0.0-draft.1 → 2.0.2-draft.1 (additive: TypeDefinition + Convention; target_ou metadata on Compose).

### Tests

- 18 tests for X-Powerloom-Schema-Version header injection + formatter
- 100 tests for compose target_ou validation + apply path
- 172 tests for TypeDefinition schema validation
- 19 tests for Convention schema validation
- All 484 existing tests still pass — full suite 553/553 green

### Reconcile note

Originally three sequential `0.6.1-rcN` releases on `session/v057-closeout-20260425`, plus an additional `0.6.2-rc1` cut on the same branch carrying Convention. The `0.6.2-rc1` slot was pre-empted by the `weave import-project` cut (different feature, also dated 2026-04-26). This reconciles all four commits + bumps to `0.6.3-rc1` as a single unified release; the intermediate rc bumps are squashed away.

## v0.6.2-rc1 — 2026-04-26 (CLI)

**v059 self-import MVP — `weave import-project`.** New CLI command that imports a Powerloom-shaped checkout (Project.md + docs/phases/*.md + KNOWN_ISSUES.md + docs/out-of-scope.md + docs/handoffs/*.md + ReadMe.md) into the engine's tracker subsystem. The CLI walks the local files and POSTs their contents; the engine does the parsing + apply via the new `POST /projects/import/from-source` endpoint (Powerloom PR #129). No engine-package dependency on the client side.

### New

- **`weave import-project <path>`** — uploads a checkout's source files to the engine and reports created/updated counters. Flags: `--dry-run` (rollback semantics), `--slug` / `--name` (defaults `powerloom` / `Powerloom`), `--slug-override` (apply against a sandbox project without disturbing the canonical one). Idempotent — re-runs against the same source produce 0 new threads via the engine's dedupe-key matching.
- **`loomcli/commands/import_project_cmd.py`** — collects the canonical source path set, enforces the 5 MB upload cap locally before round-trip, passes `git rev-parse --short HEAD` as `commit_sha` for source-drift provenance.
- **`tests/test_import_project_cli.py`** — 13 tests: command discovery, auth gate, empty-repo rejection, source-file collection, body shape (slug/name/dry-run/override), result rendering (success summary, dry-run banner, per-op errors), API-error propagation. Engine-side parsing is covered by Powerloom's own integration tests.

## v0.6.1-rc1 — 2026-04-25 (CLI, side-branch draft)

**v057 stdlib expansion: `FailureRecoveryFrame`.** First Frame-Semantics derivation lands as a v2.0.1 stdlib kind. Operators can now author scope-attached recovery templates that bind the canonical four frame elements (`Action_Attempted` / `Error_Type` / `Corrective_Action` / `Final_Outcome`) to specific failure patterns agents should follow. v058+ will let consolidation distill these from episodic runs (`provenance: distilled_from_episodic`); v057 ships only the operator-authored path.

> Side-branch release. Cut on `session/memory-schema-arc-20260425`; canonical `vNNN` and PyPI tag assigned at reconcile-to-main.

### New

- **`schema/v2/stdlib/failure-recovery-frame.schema.json`** — additive. Derivation: `compose(Process[recovery_procedure], Policy[trigger_conditions], Scope[applicable_scope])`. Required spec fields: `display_name`, `applicable_scope_ref`, `action_attempted`, `error_type`, `corrective_action`, `final_outcome`. `error_type.category='other'` requires `error_type.signature` (enforced via JSON Schema `if`/`then`). Schema version bumps `2.0.0-draft.1 → 2.0.1-draft.1`.
- **`examples/minimal/failure-recovery-frame.yaml`** — minimal manifest covering the canonical rate-limit-429 retry pattern.
- **`tests/schema/test_failure_recovery_frame.py`** — 23 tests: minimal validates, every required-field omission rejected, every error category accepted, `category='other'` requires signature, every final-outcome enum accepted, `max_attempts` bounds, provenance default + enum, derivation metadata sanity.
- **`schema/v2/powerloom.v2.bundle.json`** — `oneOf` extended to include the new kind. Description bumped 8→9 stdlib derivations.
- **`scripts/generate_schema_package.py`** — `SCHEMA_VERSION` now sourced from `schema/v2/VERSION` instead of hardcoded. Avoids future drift between the literal in the script and the actual schema version.

### Engine pin (downstream)

Powerloom engine should bump `loomcli>=0.6.1rc1,<0.7.0` in `api/pyproject.toml` to consume the new kind. Engine-side validators + `kind_registry` discovery handled in the matching engine PR on the same session branch.

### Compat

- Existing `0.6.0-rc2` manifests continue to validate unchanged — the v057 work is purely additive on the v2.0.0 surface.
- `_V2_STDLIB_KINDS` in `migrate_cmd.py` stays at the original 8 — `FailureRecoveryFrame` is new in v2 with no v1 equivalent, so it has no v1→v2 migration path to surface.

## v0.6.0-rc2 — 2026-04-24 (CLI)

**First actual pre-release of the 0.6.0 series.** The 0.6.0rc1 pyproject bump merged on the long branch but never got tagged (PEP 440 string `0.6.0rc1` didn't match the publish workflow's `v*.*.*-*` tag pattern). 0.6.0-rc2 ships with everything rc1 was supposed to — plus one live-test bug fix.

### Fix
- **`weave login` default API URL** (PR #10) — `POWERLOOM_API_BASE_URL` default flipped from `http://localhost:8000` → `https://api.powerloom.org`. A fresh `pip install loomcli` + `weave login` now talks to the hosted cluster out of the box. Docker-compose dev workflows already pass `POWERLOOM_API_BASE_URL` or `--api-url` so they're unaffected.

### Carried forward from 0.6.0rc1 (which never shipped)
See the 0.6.0rc1 entry below — all five milestones (stdlib polish, compose operator, migrate tool, loomcli.schema package, v2 schema bundle) are in this release.

## v0.6.0rc1 — 2026-04-24 (CLI)

**v056 schema v2.0.0 surface — first pre-release.** Ships the Chomskian 6 authoring stack: six primitives (Entity/Event/Relation/Process/Scope/Policy), eight stdlib derivations (Organization/OU/Agent/Skill/WorkflowType/Workflow/MemoryPolicy/MCPDeployment), and the `compose` operator. Also ships the migration tooling to bring v1.2.0 manifests forward.

### New

- **`loomcli.schema` Pydantic package** — generated from `schema/v2/*.schema.json` via `scripts/generate_schema_package.py` (datamodel-code-generator). Importable as `from loomcli.schema.v2 import stdlib, primitives, compose, common`. Regeneration lives in the same script; CI compares the generated output to the committed files.
- **`weave compose` operator** (3 subcommands): `scaffold` prints a starter Compose manifest; `lint` runs three-pass validation (meta-schema + slot-shape + shallow scope_ref pattern) with cross-file `$ref` resolution; `show` fetches effective kind schemas from the v056 `/kind-registry` endpoint.
- **`weave migrate v1-to-v2`** — bumps apiVersion with full parity for the 8 stdlib kinds, emits re-express-as guidance for the 9 retired-in-v2 kinds (Group, GroupMembership, RoleBinding, SkillAccessGrant, AgentSkill, AgentMCPServer, MCPServerRegistration, standalone Scope, Credential). Supports `--in-place`, `--out`, `--check`, directory recursion.
- **v2 schema bundle** — `schema/v2/` now shipped inside the wheel under `_bundled_schema/v2/` so pip-installed `weave` can validate v2 manifests offline.
- **T5 compose-doc polish** — `compose.schema.json` description expanded with explicit Policy slot authoring guidance (`policy_type` is free-form text; worked example; Entity/Event/Relation hints). Closes the T5-05 benchmark failure cluster where models left `policy_type` off Policy slots.

### Deferred to 0.6.0 final (v056 M6/M7)

- Engine `/kind-registry` route + compose reconciler (the `show` command wires against them but they aren't deployed yet).
- Migration guide doc (`docs/migration-v1-to-v2.md`).
- CHANGELOG reconciliation with Powerloom ReadMe.md once v056 ships.

### Compat

- `powerloom.app/v1` manifests continue to work against current engine deployments; v056 adds v2 support additively.
- Legacy alias `powerloom/v1` still accepted at migration time (warn-level note).

## v0.5.4 — 2026-04-24 (CLI)

**Close the Pydantic ↔ schema drift for v1.2.0.** Before this release, the CLI bundled the v1.2.0 JSON Schema but the parallel Pydantic models in `loomcli/manifest/schema.py` only covered v1.1.0 shapes. Manifests using any v1.2.0 addition (`system` / `auto_attach_to` on Skill; `coordinator_role` / `task_kinds` / `memory_permissions` / `reranker_model` on Agent; the new kinds `WorkflowType` / `MemoryPolicy` / `Scope`) passed schema validation then crashed with `"Extra inputs are not permitted"` on Pydantic parse.

### Fix scope

- **`SkillSpec`** extended with `system: bool` + `auto_attach_to: AutoAttachSelector | None`. New `AutoAttachSelector` class enforces the selector's exact shape + enum values.
- **`AgentSpec`** extended with `coordinator_role: bool`, `task_kinds: list[TaskKind]`, `memory_permissions: list[str]`, `reranker_model: str | None`.
- **New kinds registered:** `WorkflowType`, `MemoryPolicy`, `Scope`. Each has its own Spec model with field-level validation (enums, numeric ranges). All three use a shared new `OUIdScopedMetadata` class (since v1.2.0 new kinds use `metadata.ou_id: uuid` rather than `metadata.ou_path`).

### Tests

28 new tests in `tests/test_schema_1_2_0_drift.py` covering: every new Skill/Agent field, every new kind's default + custom values, enum rejection, kind-registry wiring, `OUIdScopedMetadata` shape. Full suite: 194/194.

### Bug-fix context

Surfaced running the reference-fleet bootstrap against Shane's prod org:

```
/bespoke-technology/studio/bespoke-brand-style: doc 1 (Skill):
pydantic spec drift (bug — schema passed but model rejected):
system: Extra inputs are not permitted;
auto_attach_to: Extra inputs are not permitted
```

The manifest was correct — the CLI just didn't know about the v1.2.0 extension fields it had advertised via its schema bundle.

### Looking forward

This class of drift is what v056's Milestone 5 closes permanently — the `loomcli.schema` Python package will be generated from the JSON Schema via `datamodel-code-generator`, so future schema additions can't desync from Pydantic. Until then, schema-v1.2.0 + Pydantic-v1.2.0 are now aligned and 28 regression tests guard the alignment.

## v0.5.3 — 2026-04-24 (CLI)

**Approval-gate support + full error visibility.** Closes two gaps surfaced when Shane ran the reference-fleet bootstrap against production.

### New: approval-gate support

Organizations with a `justification_required` approval policy could not use `weave apply` at all — the API returned HTTP 409 with `code: justification_required` before processing the request, and the CLI had no way to send the required `X-Approval-Justification` header.

Now supported two ways:

- **`weave --justification "reason" <any command>`** — global flag. Applies to every request made in that invocation.
- **`POWERLOOM_APPROVAL_JUSTIFICATION=reason`** env var — same effect, useful in scripts (e.g. the `bootstrap.sh`/`bootstrap.ps1` scripts set it automatically).

The header flows from CLI flag → env var → `RuntimeConfig.approval_justification` → `PowerloomClient` constructor → `X-Approval-Justification` HTTP header on every request.

### Fix: apply-results table no longer truncates errors

`weave apply` was rendering the Rich results table with an Error column clipped to 80 characters, which hid the most important information when things went wrong (the full error body was being discarded before display). The table-row summary still shows the first 80 chars for scannability, but full error bodies are now printed below the table for any row that failed, unclipped + indented for readability.

### Bug fix context

Surfaced when Shane's bootstrap tried to create skills on a prod org with an approval policy:
```
HTTP 409 POST /skills: {'code': 'justification_require... 'message': 'this polic...
```
— table clipped the error, leaving both the CLI user AND the fix-it developer guessing what the full message was. Both fixed in this release.

### Tests

9 new tests in `tests/test_approval_justification.py` covering: env-var read path, empty-string-is-none, client header injection present/absent, CLI flag flows to config, help output documents the flag. Full suite: 166/166 passing.

## v0.5.2 — 2026-04-24 (CLI)

**Skill archive upload/activate commands.** Closes the gap `weave apply` leaves open — apply creates/updates the Skill *shell* (manifest metadata), but archive content (the zip with SKILL.md + code + prompts) has to be uploaded separately. Before 0.5.2, that meant curl. Now it's declarative.

### New commands

- **`weave skill upload <address> <archive.zip>`** — POSTs the archive to `/skills/{id}/versions` as multipart/form-data. Prints the returned version UUID, frontmatter name/description, sha256, and size. Does NOT activate — the skill's `current_version_id` stays unchanged until you activate explicitly.
- **`weave skill activate <address> <version-uuid>`** — PATCHes the skill's `current_version_id`. Promotes the uploaded version to runnable.
- **`weave skill upload-and-activate <address> <archive.zip>`** — The common case. Upload + activate in one operation. If activation fails after upload, surfaces both facts so the user can retry activation without re-uploading.
- **`weave skill versions <address>`** — Lists all uploaded versions for a skill (UUID, frontmatter name, sha256, size, upload timestamp).

### Address syntax

All commands take a `<address>` argument of the form `/ou-path/skill-name`, e.g. `/bespoke-technology/studio/bespoke-brand-style`. The skill name is the trailing segment; everything before it is the OU path. Address resolution uses the same `AddressResolver` as `weave apply` — consistent with the rest of the CLI.

### Archive support

- `.zip` → `application/zip`
- `.tar.gz` / `.tgz` → `application/gzip`
- Anything else → `application/octet-stream`

The API validates the archive server-side (SKILL.md frontmatter schema, path-traversal protection, size caps).

## v0.5.1 — 2026-04-24 (CLI)

**Auth UX overhaul — real production login now works, plus PAT management.** First release where `weave` is actually usable against `api.powerloom.org` without manually editing the credentials file.

### New commands

- **`weave login`** — top-level alias for `weave auth login`. Default behavior is a browser-paste flow: opens `https://powerloom.org/settings/access-tokens` in your browser, prompts you to paste the PAT you just minted, verifies it against `/me`, and writes credentials on success. Similar register to `gh auth login` / `aws sso login`.
- **`weave logout`** / **`weave whoami`** — top-level aliases for the parallel `weave auth` commands.
- **`weave login --pat <token>`** — non-interactive mode for scripts and CI. Verifies the token against `/me` before persisting.
- **`weave login --no-browser`** — headless/remote-system mode. Prints the URL and prompts for paste without launching a browser.
- **`weave auth pat create --name <label> [--expires-at <iso>]`** — mint a new PAT. Raw token is shown once.
- **`weave auth pat list`** — list PAT metadata for the signed-in user.
- **`weave auth pat revoke <id>`** — revoke a PAT by UUID.

### Preserved

- `weave login --dev-as <email>` — unchanged dev-mode impersonation. Still requires `POWERLOOM_AUTH_MODE=dev` on the API (localhost/docker-compose only).

### Deferred

- Fully-automated OIDC device-code flow (the `--oidc` stub) — lands in loomcli 0.6.0 alongside schema 2.0.0. Requires API-side device-code endpoints + a Web UI approval page; not in 0.5.1 scope.

### Notes

- Web UI URL is configurable via `POWERLOOM_WEB_URL` for local/staging development. Default: `https://powerloom.org`.
- Credentials directory is unchanged — `%APPDATA%\powerloom\powerloom\credentials` on Windows, `~/.config/powerloom/credentials` on Linux/macOS. The directory is created on first successful login.
- `__version__` also bumped from a stale `0.3.0` to match the pyproject version.

## v0.5.0 — 2026-04-23 (CLI)

**Ships schema-v1.2.0 to PyPI.** First CLI release since the renumber-to-PEP-440 fix (prior `pyproject.toml` carried an invalid `v0.4.0` string that the publish preflight would have rejected — no wheel ever reached PyPI at that version). Bumping straight to `0.5.0` to keep CLI-stream numbering monotonic and to pair cleanly with the schema-v1.2.0 payload.

- Schema bundle in wheel now includes `workflow-type`, `memory-policy`, `scope` kinds + v1.2.0 Agent/Skill extensions.
- No CLI-surface breaking changes. `weave --help` unchanged. Existing manifests keep validating.
- Gated on Powerloom engine v052 for the new kinds (earlier engines tolerate them as unknown and will 404 on apply).

## v0.3.0 — 2026-04-22 (CLI)

**First PyPI publish — `pip install loomcli` installs the `weave` console script.**

Consolidates the repo from schema-only (weavecli era) into the authoritative home for both the schema AND the CLI:

- CLI source migrated in from the Powerloom monorepo's `cli/` directory as part of the Alfred-MVP-arc v034 post-ship work. Powerloom monorepo no longer vendors CLI source; its dev story is `pip install loomcli` like everyone else.
- Repo renamed `weavecli` → `loomcli` to avoid PyPI name collision with an unrelated project. PyPI wheel name: `loomcli`. Python module: `loomcli`. CLI binary command: `weave` (unchanged — narrative fit: Loom is the tool, Powerloom is the platform, `weave` is what Loom does).
- PyPI publish workflow (`.github/workflows/publish.yml`) wired with OIDC Trusted Publishing. Tag push `vX.Y.Z` → preflight (tag matches pyproject, schema bundle present) → build wheel + sdist → smoke-test the wheel (install + `weave --version` + `--help`) → verify schema bundle inside wheel → publish to PyPI. No API token needed.
- JSON Schema inlined at `schema/v1/` (no more submodule relationship with Powerloom monorepo — the wheel bundles schema directly).
- Repo layout flattened: `loomcli/` at root, `schema/v1/` at root, `tests/` at root (with `tests/schema/` for schema test suite).

**CLI features (inherited from Powerloom monorepo's CLI at the time of migration):**

- Declarative manifests — `weave plan`, `weave apply`, `weave destroy` (kubectl-style YAML, JSON Schema validated).
- Resource inspection — `weave get`, `weave describe`, `weave import`.
- Phase-14 workflow authoring + execution — `weave workflow apply|run|status|ls|cancel`.
- Multi-session coordination — `weave agent-session register|end|ls|get|tasks|task-complete`.
- Auth — `weave auth login` (OIDC device-code stubbed; dev-mode impersonation works), `weave auth whoami`.
- PyInstaller single-binary build (`build-binary.sh` + `loomcli.spec`).

## schema-v1.2.0 — 2026-04-23

**Additive release. No breaking changes.** All v1.1.0 manifests continue to validate. Engine negotiation advertises `supported_schema_versions: ["1.0.0", "1.1.0", "1.2.0"]`; CLI picks highest mutual.

### New kinds

- **`WorkflowType`** — declarative authoring of workflow types. `coordinator_agent_id`, `memory_policy_id`, `task_kinds`, `runtime_targets` fields. Previously only REST-created.
- **`MemoryPolicy`** — per-org memory governance config: `review_cadence_hours`, `timeout_action` (`approve|forget|escalate`), `review_deadline_hours`, `tentative_weight`, `org_scope_requires_approval`, `max_memories_per_session`, `consolidation_gate`.
- **`Scope`** — explicit scope declarations (previously implicit via OU). `parent_scope_ref`, `inheritance_mode` (`full|isolated|selective`), optional `selective_inheritance` block, `retention_override`.

### Agent kind — additive fields

- `coordinator_role: bool = false` — marks an LLM-coordinator; auto-attaches grading skills.
- `task_kinds: [routing|qa|analogy|execution|coordination]` — context-assembler representation hints.
- `memory_permissions: [scope_ref]` — explicit scope allowlist for memory reads.
- `reranker_model: string | null` — optional per-agent reranker override.

### Skill kind — additive fields

- `system: bool = false` — marks skill for auto-attach per selector rules.
- `auto_attach_to: object | null` — selector with `agent_kinds`, `task_kinds`, `runtime_types`, `coordinator_role_required`. Conditions ANDed.

### Powerloom engine pairing

Powerloom engine v052 (target) accepts the new fields + adds Pydantic shapes. Existing engines at v044+ tolerate the new fields (ignored server-side on older schemas).

### Source

Draft derived from the 6-report memory/schema architecture review in `github.com/shanerlevy-debug/Powerloom:docs/memory-evolution/`. See the companion Powerloom PR #61 for the synthesis + phasing plan.

## schema-v1.1.0 — 2026-04-22

Additive sync with Powerloom engine v044 changes:
- `Skill`: added `skill_type` (`archive|tool_definition`) + `tool_schema` (JSON, required when `skill_type=tool_definition`).
- `Agent`: added `runtime_type` + `agent_kind` enum expanded to `user|service`.
- `McpDeployment`: added `isolation_mode` (`shared|dedicated`) + `template_kind` enum expanded to 15 values (echo, files, postgres, slack, powerloom_meta + 10 SaaS templates).

## schema-v1.0.0 — 2026-04-21

Initial schema extraction. Matches Powerloom monorepo v024 in shape.

**Kinds published (12 existing + 1 preview):**

- `OU`, `Group`, `GroupMembership`, `RoleBinding`
- `Skill`, `SkillAccessGrant`, `Credential`
- `MCPServerRegistration`, `MCPDeployment`
- `Agent`, `AgentSkill`, `AgentMCPServer`
- `Workflow` (preview — runtime lands in monorepo Phase 14)

**Dialect extensions introduced:**

- `x-powerloom-ref` — cross-kind reference with target kind + ID resolution
- `x-powerloom-server-field` — field populated by server, rejected on input
- `x-powerloom-immutable` — field cannot change after create
- `x-powerloom-reconciler-hint` — signals what reconciler cares about
- `x-powerloom-secret-ref` — field resolves via `CredentialStore`
- `x-powerloom-default-from-server` — default chosen server-side (policy)
- `x-powerloom-auxiliary` — field comes from an `AuxiliaryClass` (Phase 15.4)
- `x-powerloom-example` — agent-demo-grade example payload
- `x-powerloom-tier-availability` — tier gating annotation
- `x-powerloom-apply-order` — reconciler ordering hint

**Notes:**

- `apiVersion` rename: monorepo shipped `powerloom/v1`; schema publishes `powerloom.app/v1`. CLI accepts both through schema-v1; monorepo migrates to canonical form on next build.
- No CLI release in this tag — CLI shipped separately at v0.3.0 once repo renamed and CLI migrated in.
