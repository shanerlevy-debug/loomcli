---
name: weave-interpreter
description: Authoritative guide to the weave CLI (loomcli). Understands provider-agnostic agent ask/chat, command structure, auth flows, manifest apply/plan/destroy semantics, skill archive upload/activate pipeline, OU addressing, approval gates, environment variables, schema versions, and common error modes. Use when an agent needs to drive weave operations or diagnose CLI errors.
---

# Weave Interpreter

You are an expert in the `weave` CLI — the command-line tool shipped by the `loomcli` Python package that drives the Powerloom control plane declaratively. You help other agents + operators use weave correctly, diagnose its errors precisely, and write scripts around it safely.

## What weave is

- **Package:** `loomcli` on PyPI (`pip install loomcli`)
- **Console script:** `weave`
- **Source of truth:** https://github.com/shanerlevy-debug/loomcli
- **API it talks to:** Powerloom control plane, default `https://api.powerloom.org`; use `--api-url http://localhost:8000` for local docker-compose dev
- **Release cadence:** patch for CLI fixes; minor for schema-version bumps and command families
- **Current version:** 0.6.1-rc1 draft (2026-04-25); bundles v1 + v2 schema surfaces

## Command structure

Weave uses a two-level command tree: top-level commands + subgroups. As of 0.6.1-rc1:

### Top-level mutations + reads
| Command | Purpose | Arg shape |
|---|---|---|
| `weave apply` | Create/update resources from manifests | `<path>...` (positional; NOT `-f <path>`) |
| `weave plan` | Preview what apply would do | `<path>...` (positional) |
| `weave destroy` | Delete resources in a manifest | `<path>...` (positional) |
| `weave get` | List resources by kind | `<kind> [name]` |
| `weave describe` | Show full detail of one resource | `<kind> <address>` |
| `weave import` | Adopt an existing resource into a manifest | varies |
| `weave ask` | Ask a Powerloom agent one prompt and stream the answer | `<agent> "prompt"` |
| `weave chat` | Start an interactive terminal chat with a Powerloom agent | `<agent> ["first prompt"]` |
| `weave agent status` | Show runtime/model, sync state, and recent work for one agent | `<agent>` |
| `weave session tail` | Poll durable session events after invoke-time WS tickets expire | `<session-id>` |

### Agent ask/chat provider model

`weave ask` and `weave chat` are provider-agnostic. They call Powerloom's `POST /agents/{id}/invoke` endpoint and stream the resulting session. The CLI does not read OpenAI, Anthropic, Gemini, Bedrock, or other model keys locally. Runtime/provider selection comes from the Agent row (`runtime_type` + `model`) and the backend uses the user/org runtime credential configured in Powerloom.

Agent identifiers can be:

```bash
weave ask <agent-uuid> "prompt"
weave ask /org/ou/agent-name "prompt"
weave ask agent-name "prompt" --ou /org/ou
```

### Agent/session observability

These commands read runtime state only and do not mutate manifests:

```bash
weave agent status /org/ou/agent-name
weave agent sessions /org/ou/agent-name
weave agent watch /org/ou/agent-name --interval 3
weave session events <session-id>
weave session tail <session-id>
```

Use them to answer what an agent is currently doing, whether it has active sessions, and what durable events have been recorded.

### Top-level auth aliases (shortcuts for `weave auth <cmd>`)
| Command | Purpose |
|---|---|
| `weave login` | Sign in (default: browser-paste PAT flow) |
| `weave logout` | Clear credentials |
| `weave whoami` | Show signed-in user |

### Subgroups
| Subgroup | Commands |
|---|---|
| `weave auth` | `login`, `logout`, `whoami`, `pat create`/`list`/`revoke` |
| `weave skill` | `upload`, `activate`, `upload-and-activate`, `versions` |
| `weave workflow` | `apply`, `run`, `status`, `ls`, `cancel` (Phase 14 workflow CLI) |
| `weave agent-session` | `register`, `end`, `ls`, `get`, `tasks`, `task-complete` |
| `weave antigravity-worker` | Daemon for Antigravity IDE integration (stub) |

## Global options

Available on every command via the root callback. Specified before the subcommand:

```
weave [--api-url URL] [--config-dir PATH] [--justification TEXT] [--version] <cmd> ...
```

| Flag | Env var | Effect |
|---|---|---|
| `--api-url` | `POWERLOOM_API_BASE_URL` | Override control plane URL (default: `https://api.powerloom.org`) |
| `--config-dir` | `POWERLOOM_HOME` | Override credentials/config directory |
| `--justification` | `POWERLOOM_APPROVAL_JUSTIFICATION` | Inject `X-Approval-Justification` header (required when approval policy demands) |
| `--version` | — | Print CLI version and exit |

Additional env vars (no CLI flag):

| Env var | Effect |
|---|---|
| `POWERLOOM_WEB_URL` | Override Web UI URL (default: `https://powerloom.org`); used by `weave login` browser flow |

## Auth flows

Three ways to sign in, in order of preference for production:

### 1. Browser-paste (default)

```
weave login
```
Opens `https://powerloom.org/settings/access-tokens` → user mints a PAT → pastes into hidden-input prompt → CLI verifies against `/me` → writes to credentials file.

Suppress browser launch for headless systems:
```
weave login --no-browser
```

### 2. Direct PAT injection (scripts / CI)

```
weave login --pat <token>
```
Same verification; no prompts.

### 3. Dev-mode impersonation (localhost only)

```
weave login --dev-as admin@dev.local
```
Requires `POWERLOOM_AUTH_MODE=dev` on the control plane. Only works for docker-compose dev setups.

## Credential storage

Cross-platform via `platformdirs`:
- **Linux / macOS:** `~/.config/powerloom/credentials`
- **Windows:** `%APPDATA%\powerloom\powerloom\credentials`

The directory is created on first successful login. A "missing folder" before first login is expected, not a bug.

Override location via `POWERLOOM_HOME=<path>` env var or `--config-dir <path>` flag.

## PAT management (auth pat subgroup)

```
weave auth pat create --name my-laptop [--expires-at 2027-01-01T00:00:00Z]
weave auth pat list
weave auth pat revoke <pat-uuid>
```

The raw token from `pat create` is shown ONCE — the API does not return it again. `pat list` shows only metadata (id, prefix, created_at, etc.).

## Manifest apply semantics

### apply takes PATHS positionally

```
weave apply /path/to/manifest.yaml                    # single manifest
weave apply /path/to/dir/                              # directory (all *.yaml)
weave apply m1.yaml m2.yaml m3.yaml                    # multiple paths
weave apply -y /path/to/manifest.yaml                  # -y / --auto-approve skips interactive confirmation
cat manifest.yaml | weave apply -                      # `-` reads stdin
```

**Common mistake:** `-f <path>` does NOT work. Weave's apply is positional, unlike `kubectl apply -f`. This causes "Usage: weave apply [OPTIONS] PATHS..." errors.

### Apply-results table

After apply, weave prints a table:
```
Apply results
+-------+---------+--------+--------+-----------------+
| Kind  | Address | Action | Status | Error (summary) |
+-------+---------+--------+--------+-----------------+
```

Plus (since 0.5.3) full error bodies below the table for any failed rows. If the summary column looks clipped, the full message is below — read there.

### Idempotency

`weave apply` compares desired state (manifest) against current state (API) and only changes what differs. Re-applying the same manifest is a no-op. Safe in scripts.

### auto-approve

`weave apply -y` skips the "Proceed? [y/N]" confirmation. Required for scripted use; weave hangs otherwise.

## Skill archive lifecycle

Skills are two-part resources: a **shell** (manifest metadata) + **archive content** (the actual SKILL.md + code). Requires two operations:

### 1. Create / update the shell

```
weave apply /path/to/skill-manifest.yaml
```
Creates a `Skill` resource with `current_version_id: null`.

### 2. Upload and activate the archive

```
weave skill upload-and-activate /ou-path/skill-name /path/to/archive.zip
```

The archive must be a `.zip` or `.tar.gz` containing `SKILL.md` at the root, with frontmatter:
```yaml
---
name: skill-name               # lowercase alphanumeric + hyphens, ≤64 chars
description: One-line description of what this skill does
---
```

The API validates frontmatter server-side (name regex, description length ≤1024 chars, not reserved words `anthropic`/`claude`, path-traversal protection, size caps).

### Alternate flow (separate steps)

```
# Upload only (does NOT activate)
weave skill upload /ou/name /archive.zip
# ... prints version UUID

# Activate that version
weave skill activate /ou/name <version-uuid>

# List all versions
weave skill versions /ou/name
```

## Address syntax

`weave` resources use slash-separated paths:

```
/<ou-path>/<resource-name>
```

Examples:
- `/bespoke-technology/studio/bespoke-brand-style` — a skill named `bespoke-brand-style` in the `studio` OU
- `/acme/engineering/code-reviewer` — a skill in `/acme/engineering`
- `/bespoke-technology/studio` — an OU (no resource name suffix)

Address resolution uses `AddressResolver` which pre-fetches `GET /ous/tree` and caches for the invocation.

## Approval gates (v0.5.3+)

Organizations can configure policies that require a **justification** for resource mutations. When a policy matches:

**Without `--justification`:**
```
HTTP 409 POST /skills: {"error": {"code": "justification_required", "message": "..."}}
```

**With `--justification`:**
```
weave --justification "creating the reference fleet" apply /path/manifest.yaml
```

Or via env var:
```bash
export POWERLOOM_APPROVAL_JUSTIFICATION="creating the reference fleet"
weave apply /path/manifest.yaml
```

The header `X-Approval-Justification` is sent on every request for the invocation. Non-gated operations ignore it.

### Related: full approval flow (two distinct cases)

1. **Justification-required** (409 with `justification_required`) — provide text and proceed. No human approval.
2. **Approval-required** (202 with pending approval ID) — request queued for human approval. Resource not created until approved.

Case 2 requires polling via the approvals API; weave doesn't currently auto-poll. Ask for guidance if you encounter a 202 on apply.

## Schema versions

Two current schema versions for manifests:

### powerloom.app/v1

Production-compatible manifest surface for current hosted control planes.

### powerloom.app/v2

Chomskian 6-primitive root + stdlib derivations. Requires:
- loomcli 0.6.x (bundles v2 schema)
- A Powerloom engine version that accepts v2 manifests

If the target control plane has not been upgraded for v2, `apiVersion: powerloom.app/v2` in a manifest causes:
```
apiVersion: 'powerloom.app/v2' is not one of ['powerloom.app/v1', 'powerloom/v1']
```

Shape parity means v1.2.0 and v2.0.0 manifests of the same resource differ ONLY in apiVersion. After v056 ships, re-applying a resource with the other apiVersion is a no-op.

## Common error shapes

### Connection refused (HTTP 0 / WinError 10061)
The API URL isn't reachable. Check `POWERLOOM_API_BASE_URL` or `--api-url`. For prod: `https://api.powerloom.org`. For local: `http://localhost:8000` requires `docker compose up -d`.

### HTTP 401 Unauthorized
Token missing, expired, or invalid. Run `weave logout && weave login` (or `weave login --pat <fresh-token>`).

### HTTP 403 Permission denied
Token valid but the user lacks RBAC for the operation. Either grant a role or use a user with the needed permission.

### HTTP 404 Not found
Resource / OU doesn't exist. For skills: have you run `weave apply` on the Skill manifest before trying `weave skill upload`? The shell must exist before the archive.

### HTTP 409 justification_required
Org has an approval policy. Pass `--justification "reason"` or set `POWERLOOM_APPROVAL_JUSTIFICATION`.

### HTTP 409 conflict
A resource with that name already exists. If intentional, use `weave apply` (which updates) instead of direct POST.

### 202 Accepted with pending approval
Operation needs human approval before taking effect. Currently no auto-polling in weave; check the approvals UI or API.

### schema validation failed (client-side, before the request)
Your manifest doesn't match the bundled schema. Check: apiVersion matches a supported version, all required fields present, no unexpected properties (many kinds use `additionalProperties: false`).

## Diagnostic commands

When something's wrong, run in this order:
1. `weave --version` — confirm CLI version
2. `weave auth whoami` — confirm signed-in identity (confirms API URL + token valid)
3. `weave get ou` — confirm the target OU exists
4. `weave get <kind>` — confirm expected resources exist
5. `weave describe <kind> <address>` — deep detail on one resource
6. `weave plan <path>` — preview what apply would do (safest diagnosis of manifest issues)

## Scripting weave

- Always pass `-y` / `--auto-approve` to `apply` and `destroy`
- Set `POWERLOOM_APPROVAL_JUSTIFICATION` env var if the target org has policies
- Parse `weave get ... --output json` (if supported) rather than the human table
- Check `$LASTEXITCODE` (PowerShell) or `$?` (bash) after each invocation
- For idempotent deploy scripts, `weave apply` is the right primitive — it no-ops when state matches

## Things to avoid

- **Don't use `weave apply -f <path>`** — there's no `-f`. Paths are positional.
- **Don't skip `-y` in scripts** — the prompt will hang indefinitely.
- **Don't write v2.0.0 manifests today** — until v056 ships, they'll be rejected. Use v1.2.0.
- **Don't trust the apply-results table alone for error detail (pre-0.5.3)** — errors were clipped to 80 chars. Upgrade to ≥0.5.3 for full error bodies.
- **Don't paste raw PATs into source code / chat** — they grant full account access. Use env vars or credential files.
- **Don't share credential files across users** — the auth is per-principal.
- **Don't run `weave login --dev-as` against production** — it requires `POWERLOOM_AUTH_MODE=dev` which prod doesn't have.

## Version history (CLI)

| Version | Date | Highlights |
|---|---|---|
| 0.3.0 | 2026-04-22 | First PyPI publish |
| 0.4.0 | — | Broken (invalid `v0.4.0` version string in pyproject); never published |
| 0.5.0 | 2026-04-23 | Schema 1.2.0 (WorkflowType / MemoryPolicy / Scope) |
| 0.5.1 | 2026-04-24 | Auth UX: top-level aliases, browser-paste login, PAT commands, config path lazy |
| 0.5.2 | 2026-04-24 | `weave skill upload/activate/upload-and-activate/versions` |
| 0.5.3 | 2026-04-24 | `--justification` flag + `POWERLOOM_APPROVAL_JUSTIFICATION` env var; full error bodies in apply-results |
| 0.6.0-rc2 | 2026-04-24 | Schema v2 bundle, `compose`, `migrate v1-to-v2`, generated `loomcli.schema` package |
| 0.6.1-rc1 | 2026-04-25 | v2.0.1 draft stdlib expansion; this plugin branch adds `weave ask` / `weave chat` |

## Quick reference card

```
# Auth
weave login                                          # default: browser-paste
weave login --pat <token>                            # non-interactive
weave login --dev-as admin@dev.local                 # localhost dev
weave logout
weave whoami

# Agent sessions
weave ask /ou/path/agent "prompt"                    # one turn, streamed
weave chat /ou/path/agent                            # interactive terminal chat
weave agent status /ou/path/agent                    # live work snapshot
weave session tail <session-id>                      # durable event tail

# Apply manifests
weave apply <path> [<path>...]                       # positional; multiple OK
weave apply -y <path>                                # auto-approve
weave --justification "reason" apply <path>          # with justification
weave plan <path>                                    # preview without applying
weave destroy <path>                                 # delete

# Read
weave get <kind>                                     # list
weave describe <kind> <address>                      # detail

# Skills
weave apply <skill-manifest.yaml>                    # create shell
weave skill upload-and-activate /ou/name archive.zip # add archive + activate
weave skill versions /ou/name                        # list versions

# PATs
weave auth pat create --name <label>                 # mint (shown once)
weave auth pat list
weave auth pat revoke <uuid>

# Global options (before subcommand)
weave --api-url https://api.powerloom.org <cmd>
weave --justification "..." <cmd>
weave --version
```
