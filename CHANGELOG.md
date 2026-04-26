# Changelog

All notable changes to the Powerloom schema and CLI are documented here. This repo uses two independent version streams:

- **Schema:** `schema-vX.Y.Z` git tags. Semver — breaking changes bump major, additive bump minor, docs-only bump patch.
- **CLI:** `vX.Y.Z` git tags on this repo. Trigger PyPI publish via `.github/workflows/publish.yml`.

## v0.6.2-rc1 — 2026-04-26 (CLI, side-branch draft)

**v064 first slice: `Convention` stdlib derivation (11th v2 stdlib kind).** Top-down authored organizational rules — distinct from the bottom-up procedural memory templates the engine companion ships. Each Convention names a body (summary + optional checklist) plus an enforcement_mode (advisory / warn / enforce); the engine companion adds a dedicated `conventions` table + `/memory/semantic/conventions/*` route surface.

> Stacks on `0.6.1-rc4`. Resolves the Q6 procedural-memory storage open question via D-1: conventions ship as a v2 stdlib kind alongside Agent/Skill/etc.; procedural memory ships as `type_memory_procedural` table symmetric with grammar/lexicon.

### New

- **`schema/v2/stdlib/convention.schema.json`** — derivation `compose(Policy[intent], Scope[applies_to])`. Required spec fields: `display_name`, `applies_to_scope_ref`, `body`. Optional: `description`, `additional_scope_refs`, `enforcement_mode` (default `advisory`), `status` (default `active`), `references` (cross-link to concept_id / type_definition_id / stdlib name).
- **`examples/minimal/convention.yaml`** — code-review-checklist exemplar covering the canonical fields.
- **`tests/schema/test_convention.py`** — 19 tests covering required-field omissions, body shape, enforcement_mode + status enum, references kinds, additionalProperties lockdown.
- **`schema/v2/powerloom.v2.bundle.json`** — `oneOf` extended; description bumped 10 → 11 stdlib derivations.
- **`tests/schema/test_v2_schemas.py`** — discovery floor lifted 18 → 19.
- Schema version: `2.0.1-draft.1` → `2.0.2-draft.1`. CLI: `0.6.1-rc4` → `0.6.2-rc1`.

### Engine companion

The matching engine PR (same session branch) adds:
  - migration `0059_conventions.py` for the dedicated table,
  - migration `0060_type_memory_procedural.py` for the symmetric procedural table,
  - `services/conventions.py` for CRUD + scope resolution,
  - `routes/memory_semantic_conventions.py` (`POST/GET/PUT/DELETE /memory/semantic/conventions/*`),
  - `routes/memory_procedural.py` (`GET /memory/procedural/search`, `POST /memory/procedural/reinforce`),
  - `_STDLIB_NAMES += Convention` so `compose: extends: Convention` resolves.

### Compat

- Additive on the v2 surface. Pre-v064 manifests + engines unchanged.
- A v0.6.2-rc1 CLI talking to a pre-v064 engine that doesn't have migration 0059 will lint a Convention manifest cleanly but `weave apply` would 404/500. Forward-only for the full Convention story.

## v0.6.1-rc4 — 2026-04-26 (CLI, side-branch draft)

**v061 first slice: TypeDefinition stdlib derivation (10th v2 stdlib kind).** Operators declare named types the engine accumulates memory around — grammar (how it composes with others) + lexicon (specific instances + outcome signals). The manifest itself ships here; engine companion adds the `type_memory_grammar` + `type_memory_lexicon` storage tables (migrations 0055/0056) and the `/memory/episodic/*` + `/memory/semantic/.../grammar` read routes.

> Stacks on `0.6.1-rc3`. Both ship together as v057-closeout + v061-foundation.

### New

- **`schema/v2/stdlib/type-definition.schema.json`** — derivation `compose(Entity[type_identity], Policy[memory_governance])`. Required spec fields: `display_name`, `type_kind` (domain/process/event/relation), `applies_to_scope_ref`. Optional: `extends_type_ref`, `type_namespace`, `description`, `memory` block. The `memory` block declares grammar decay, lexicon retention, reinforcement thresholds, concept-stabilization (v063+ field, defaults to disabled). Schema version stays `2.0.1-draft.1` — additive on the v2 surface.
- **`examples/minimal/type-definition.yaml`** — minimal manifest (legal ContractClause).
- **`tests/schema/test_type_definition.py`** — 22 tests covering the optional + bounded fields, derivation metadata sanity, and `additionalProperties` lockdown across root/metadata/spec/memory_block.
- **`schema/v2/powerloom.v2.bundle.json`** — `oneOf` extended; description bumped 9 → 10 stdlib derivations.
- **`tests/schema/test_v2_schemas.py`** — discovery floor lifted 17 → 18.

### Engine companion

The matching engine PR adds:
  - migrations `0055_type_memory_grammar.py` + `0056_type_memory_lexicon.py` (storage tables, fast-decay vs. slow-decay surfaces),
  - `routes/memory_episodic_semantic.py` (`GET /memory/episodic/search`, `GET /memory/episodic/run/{run_id}`, `GET /memory/semantic/types/{ns}/{name}/grammar`),
  - `POST /memory/episodic/promote` returns 501 with `X-Powerloom-Available-In: v062` header (consolidation pipeline that fills the cells lands at v062),
  - `_STDLIB_NAMES += TypeDefinition` so `compose: extends: TypeDefinition` resolves.

### Compat

- Pre-v061 manifests continue to validate. New stdlib kind is purely additive.
- A v0.6.1-rc4 CLI talking to a pre-v061 engine that doesn't have migrations 0055/0056 will see `weave compose lint` accept the manifest, but applying it would 404/500 since the engine doesn't have the storage tables. Forward-only behavior — operators relying on TypeDefinition need both ends.

## v0.6.1-rc3 — 2026-04-25 (CLI, side-branch draft)

**v057 Sprint 1 item 2: scope-driven compose gating (Option D) — authoring surface.** Compose manifests gain `metadata.target_ou_path` so operators can declare which OU a kind is published into. The engine then evaluates `compose:create|update|archive` approval policies against that scope rather than the org root, letting a leaf-OU admin self-approve publishes within their subtree without escalating. JSON-Schema-only change here; the engine companion does the path → UUID resolution and the gate scope swap.

> Stacks on top of `0.6.1-rc2`'s 426/header negotiation work. Both ship together as v057 closeout.

### New

- **`schema/v2/compose.schema.json`** — `metadata.target_ou_path` (optional). Pattern matches `common.schema.json#/$defs/ou_path` (absolute, lowercase, no trailing slash). Description spells out the back-compat semantics (omitted = org-root publish).
- **`tests/schema/test_compose_target_ou.py`** — 12 tests covering the optional + well-formed + malformed paths and that the schema description carries the documentation `weave compose lint` users will read.
- Pydantic regenerated cleanly via `scripts/generate_schema_package.py`.

### Engine companion

Engine companion on the same session branch adds:
  - migration `0054_kind_registry_target_ou.py` (column + FK + partial index — first slot in the memory + schema arc reservation `0054+`),
  - `services/compose.py` resolves the path to a UUID at create/update time,
  - `routes/kind_registry.py` derives `scope_ou_id` for the gate from the manifest's `target_ou_path` (or org root if omitted),
  - `ComposedKindOut` exposes `target_ou_id` on read.

### Compat

- Pre-v057 manifests (no `target_ou_path`) continue to validate + apply unchanged. Existing rows in `kind_registry` keep `target_ou_id IS NULL` and gate against org root, exactly as before.
- A v0.6.1-rc3 CLI talking to a pre-v057 engine that doesn't have migration 0054 will see the manifest accepted but the engine will silently ignore the field (new column doesn't exist; the field never gets read into a model attribute). Forward-only behavior — operators relying on Option D need both ends.

## v0.6.1-rc2 — 2026-04-25 (CLI, side-branch draft)

**v057 Sprint 1 item 1: schema-version negotiation (426 Upgrade Required handling).** Closes the version-handshake loop that landed in v056 (`GET /schema-versions`). The CLI now sends `X-Powerloom-Schema-Version: <SCHEMA_VERSION>` on every API call and renders engine 426s into a clear "engine rejected version X; supported: [...]" message instead of a generic 4xx.

> Side-branch release on `session/v057-closeout-20260425`. Stacks on top of `0.6.1-rc1`'s `FailureRecoveryFrame` ship.

### New

- **`X-Powerloom-Schema-Version` header** — `loomcli/client.py` injects the header on every request, sourced from `loomcli.schema.SCHEMA_VERSION` (auto-generated by `scripts/generate_schema_package.py` from `schema/v2/VERSION`). No flag or env var; always on. Travels alongside the existing `Authorization` and optional `X-Approval-Justification` headers.
- **426 response formatter** — `_format_version_mismatch()` in `loomcli/client.py` recognizes the engine's canonical 426 body shape (`error.detail.{supported_versions, client_sent}`) and emits an upgrade-hint message; non-canonical 426s (proxies, gateways) fall back to the default detail extractor.
- **`tests/test_schema_version_header.py`** — 18 tests covering header injection (always-on, including unauthenticated routes), formatter shape (canonical + fallback paths), and end-to-end via mocked httpx.

### Engine companion

Engine pin stays at `loomcli>=0.6.1rc1,<0.7.0` — no bump needed since the CLI change is forward-compatible. The matching engine PR adds `core/schema_version_check.py` + a FastAPI middleware that 426s requests carrying an unsupported major (with `/schema-versions`, `/healthz`, `/docs` bypassing the gate so a wildly out-of-date CLI can still discover what's supported).

### Compat

- Engines pre-dating v057 don't read the header — they ignore it. Zero impact on rollback.
- 426s from non-engine sources (proxies returning 426 for unrelated reasons) still surface readable detail via the default extractor.
- `SCHEMA_VERSION` floor on the engine side is enforced by major (any 2.x against a v2 engine), so a CLI on `2.0.5-draft.7` validates against a `2.0.1-draft.1` engine without coordination.

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
