# Changelog

All notable changes to the Powerloom schema and CLI are documented here. This repo uses two independent version streams:

- **Schema:** `schema-vX.Y.Z` git tags. Semver — breaking changes bump major, additive bump minor, docs-only bump patch.
- **CLI:** `vX.Y.Z` git tags on this repo. Trigger PyPI publish via `.github/workflows/publish.yml`.

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
