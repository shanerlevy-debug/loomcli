# Changelog

All notable changes to the Powerloom schema and CLI are documented here. This repo uses two independent version streams:

- **Schema:** `schema-vX.Y.Z` git tags. Semver — breaking changes bump major, additive bump minor, docs-only bump patch.
- **CLI:** `vX.Y.Z` git tags on this repo. Trigger PyPI publish via `.github/workflows/publish.yml`.

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
