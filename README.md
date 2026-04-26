# loomcli

**Schema language + CLI for [Powerloom](https://github.com/shanerlevy-debug/Powerloom)** — the Terraform-like declarative framework for Claude Managed Agents.

This repo is the **source of truth for the Powerloom manifest schema** (`apiVersion: powerloom.app/v1`) and the `weave` command-line tool that authors + applies manifests against a Powerloom control plane.

- PyPI: [`loomcli`](https://pypi.org/project/loomcli/)
- Binary: `weave` (console script installed by pip)
- Repo: [`github.com/shanerlevy-debug/loomcli`](https://github.com/shanerlevy-debug/loomcli)
- Platform it orchestrates: [`github.com/shanerlevy-debug/Powerloom`](https://github.com/shanerlevy-debug/Powerloom)

## Install

```bash
pip install loomcli
weave --version
```

For other install paths (single binary, from source), see the Powerloom monorepo's [`docs/install-weave.md`](https://github.com/shanerlevy-debug/Powerloom/blob/main/docs/install-weave.md).

## Why `loom` + `weave` (naming)

The platform is **Powerloom**. The CLI tool is **Loom** (the PyPI wheel + Python module + git repo name), and its console script is **`weave`** (what Loom does). Mirrors the `kubectl` / Kubernetes pattern: platform name distinct from tool name, tool name distinct from command you type.

## Layout

```
.
├── loomcli/             # Python package (Typer CLI)
│   ├── cli.py           # app root
│   ├── auth.py          # login + token persistence
│   ├── client.py        # HTTP client against Powerloom REST
│   ├── commands/        # subcommands: apply, plan, get, workflow, agent-session, ...
│   └── manifest/        # parser, planner, applier, jsonschema validator
├── schema/
│   └── v1/              # authoritative JSON Schema
│       ├── powerloom-dialect.schema.json   # meta-schema defining x-powerloom-* keywords
│       ├── dialect-docs.md                 # narrative docs for each dialect extension
│       ├── powerloom.v1.bundle.json        # assembled multi-kind bundle
│       └── kinds/                          # one JSON Schema per manifest kind
├── examples/
│   └── minimal/         # one minimal valid manifest per kind
├── tests/
│   ├── test_*.py        # CLI tests (47+)
│   └── schema/          # schema tests — parse + validate + cross-refs (5)
├── pyproject.toml
├── loomcli.spec         # PyInstaller spec (single-binary build)
├── build-binary.sh
├── LICENSE              # Elastic License v2 (ELv2)
├── CHANGELOG.md
└── .github/workflows/
    └── publish.yml      # PyPI OIDC trusted publish (tag triggered)
```

## Quickstart — author + apply a manifest

```bash
weave auth login --dev-as admin@dev.local   # local dev

cat > agent.yaml <<'EOF'
apiVersion: powerloom/v1
kind: OU
metadata:
  name: engineering
  parent_ou_path: /dev-org
spec:
  display_name: Engineering
---
apiVersion: powerloom/v1
kind: Agent
metadata:
  ou_path: /dev-org/engineering
  name: code-reviewer
spec:
  display_name: Code Reviewer
  model: claude-sonnet-4-6
  system_prompt: "Review Python code for bugs and style."
  owner_principal_ref: user:admin@dev.local
  skills: [python-lint]
EOF

weave plan agent.yaml       # show what apply would do (Terraform-style diff)
weave apply agent.yaml      # apply; prompts for confirmation
weave get agents --ou /dev-org/engineering
```

### Supported `kind:` values

| Kind | Metadata | Notes |
|---|---|---|
| `OU` | `name`, `parent_ou_path` | Root OUs omit `parent_ou_path`. |
| `Group` | `name`, `ou_path` | |
| `GroupMembership` | `group_path`, `member_ref` | `member_ref: user:<email>` or `group:<path>` |
| `RoleBinding` | `principal_ref`, `role`, `scope_ou_path`, `decision_type` | `decision_type` is `allow` or `deny` |
| `Skill` | `name`, `ou_path` | `current_version_id` on spec must be pre-uploaded via REST. |
| `MCPServerRegistration` | `name`, `ou_path` | BYO — pre-existing URL. |
| `MCPDeployment` | `name`, `ou_path` | 14+ templates (files / postgres / slack / github / notion / jira / ...). |
| `Agent` | `name`, `ou_path` | `skills` and `mcp_servers` on spec expand to AgentSkill/AgentMCPServer resources. |
| `AgentSkill` | `agent_path`, `skill_path` | Usually implicit via Agent.skills. |
| `AgentMCPServer` | `agent_path`, `mcp_registration_path` | Usually implicit via Agent.mcp_servers. |
| `Credential` | `agent_path`, `mcp_registration_path` | Bearer minted server-side; no secret material in YAML. |
| `SkillAccessGrant` | `skill_path`, `principal_ref` | Additive allowlist beyond OU-scoped RBAC. |
| `Workflow` | `name`, `ou_path` | Phase-14 feature. |

## Commands

```bash
weave plan manifest.yaml              # show what apply would do
weave apply manifest.yaml             # apply; prompts for confirmation
weave apply -y ./manifests/           # directory + auto-approve
weave destroy manifest.yaml           # reverse-order teardown
weave get agents --ou /dev-org/engineering
weave get mcp-deployments -o json
weave describe agent /dev-org/engineering/code-reviewer
weave import agent /dev-org/engineering/code-reviewer > agent.yaml
weave auth whoami
weave ask /dev-org/alfred "What should I work on next?"
weave chat /dev-org/alfred
weave agent status /dev-org/alfred
weave agent config /dev-org/alfred
weave agent set-model /dev-org/alfred --model gpt-5.5
weave session tail <session-id>
weave profile set --default-agent /dev-org/alfred --default-runtime openai --default-model gpt-5.5
weave commands --json
weave approval wait <approval-id>
weave workflow apply workflow.yaml
weave workflow run my-workflow --inputs scope=example
weave workflow status <run-id>
weave agent-session register --scope "<slug>" --summary "<one-line>"
weave agent-session ls --status active
weave agent-session watch <agent-session-id> --interval 3
weave thread my-work --watch --interval 5
```

Global flags:

```
--api-url URL       Override POWERLOOM_API_BASE_URL
--config-dir PATH   Override POWERLOOM_HOME
--version           Print CLI version and exit
```

## Agentic CLI

`weave ask` and `weave chat` make the terminal experience closer to Claude Code, Gemini CLI, and Codex CLI while keeping Powerloom provider-agnostic:

```bash
weave ask /dev-org/alfred "Summarize my open work."
weave chat /dev-org/alfred
```

The CLI invokes a Powerloom Agent through the control plane. It does not read model-provider API keys locally; the backend uses the Agent's configured runtime/model and the user/org runtime credential stored in Powerloom.

Agent identifiers can be UUIDs, full `/ou/path/agent-name` addresses, or bare names with `--ou`.

### Agent/session observability

These commands inspect runtime state. They do not change manifest-backed Agent configuration:

```bash
weave agent status /dev-org/alfred
weave agent sessions /dev-org/alfred
weave agent watch /dev-org/alfred --interval 3
weave session events <session-id>
weave session tail <session-id>
weave agent-session status <agent-session-id>
weave agent-session watch <agent-session-id> --interval 3
weave thread my-work
weave thread my-work --watch --interval 5
```

Use them to answer "what is this agent doing?", see the latest runtime session status, tail durable event traces after a WebSocket ticket has expired, and keep a live view of coordination sessions plus tracker threads plucked for your account.

### Agent config and CLI profiles

Provider/model selection stays schema-safe:

```bash
weave agent config /dev-org/alfred
weave agent set-model /dev-org/alfred --model gpt-5.5
weave profile set --default-runtime openai --default-model gpt-5.5
weave profile show
```

`weave agent set-model` updates the Agent row through the control plane. Runtime/provider changes still belong in manifests (`weave apply`) until Powerloom exposes a safe runtime patch endpoint. Profiles store local defaults in `config.toml`; they do not change remote resources by themselves.

### Command discovery and approvals

```bash
weave commands --json
weave approval wait <approval-id>
```

`weave commands` exports command metadata for autocomplete, mobile clients, and plugin docs. `weave approval wait` polls a pending approval until it is approved, rejected, cancelled, expired, or times out.

## Schema as source of truth

Manifests are validated at CLI runtime against `schema/v1/`. The same schema is consumed by:

1. **Powerloom server-side Pydantic** — reads the schema and generates typed request/response models (planned upstream adoption; today hand-maintained with a drift detector).
2. **IDE `yaml-language-server`** — \`# yaml-language-server: $schema=...\` headers on example manifests under `examples/` enable inline validation in VSCode / JetBrains / vim-lsp.
3. **LLM-authored manifests** — any Claude session handed the schema + dialect docs can produce correct manifests first-try.

Using a single source for all four paths makes drift impossible by construction.

Industry-standard pattern: kubectl, Helm, Argo, and Flux all validate user-submitted manifests against a published JSON Schema (or CRD) at runtime. Pydantic / struct-tag validation is an implementation detail downstream of the wire-format schema.

## Dialect extensions

Beyond vanilla JSON Schema Draft 2020-12, Powerloom defines `x-powerloom-*` keywords that carry control-plane semantics:

- `x-powerloom-server-populated` — field is populated by the server, not the author.
- `x-powerloom-immutable` — field set at create, immutable thereafter.
- `x-powerloom-reconciler` — reconciler behavior hints.
- `x-powerloom-ref` — cross-kind reference (e.g. Agent → Skill).
- `x-powerloom-tier-gate` — minimum tier required for the feature.

Full reference: [`schema/v1/dialect-docs.md`](schema/v1/dialect-docs.md).

## Versioning

- **Schema:** semver git tags `schema-v1.x.y`. Breaking changes bump the major; additive changes bump the minor; docs-only or wording-only changes bump the patch.
- **CLI (PyPI):** semver git tags `vX.Y.Z` on this repo. Trigger PyPI publish. Track CLI version with `weave --version`.
- **Per-kind:** optional `kind: Agent/v2` qualifier lets individual kinds evolve independently inside a major.

The Powerloom monorepo pins this repo's schema to a specific version via `pip install loomcli==<version>` in its dev dependencies (no git submodule).

## Development

```bash
git clone https://github.com/shanerlevy-debug/loomcli.git
cd loomcli
pip install -e ".[dev]"
pytest                    # 47 CLI + 5 schema tests
weave --version
```

### Build the single binary

```bash
./build-binary.sh         # -> dist/weave (or weave.exe on Windows)
./dist/weave --version
```

PyInstaller produces a platform-native single-file binary with the JSON Schema bundled inside — no Python required on the target machine. Cross-platform matrix (Linux / macOS / Windows / arm64) is tracked in the Powerloom monorepo's [`docs/loomcli-overhaul.md`](https://github.com/shanerlevy-debug/Powerloom/blob/main/docs/loomcli-overhaul.md) M1.b.

## Release workflow

Publish to PyPI on tag push:

```bash
# 1. Bump version in pyproject.toml (X.Y.Z semver).
# 2. Commit + push.
# 3. Tag and push:
git tag v0.4.0
git push origin v0.4.0
```

The `.github/workflows/publish.yml` workflow runs preflight (tag matches pyproject version, schema bundle present), builds wheel + sdist, smoke-tests the wheel (install + `weave --version` + `--help`), verifies the schema is inside the wheel, then publishes to PyPI via OIDC Trusted Publishing (no API token).

Optional TestPyPI dry-run:

```bash
gh workflow run publish.yml -f target=testpypi
```

## License

Elastic License v2 (ELv2). See [`LICENSE`](LICENSE).

## Intentional gaps

- No `weave edit` (in-place kubectl-style editor). Post-Phase-13.
- No `weave logs <session-id> --follow` (WebSocket tail). Post-Phase-13.
- No bulk export (`weave export --all`). Per-resource `import` only.
- Local skill-archive upload via manifest (`local_archive: ./skill.tar.gz`) not supported — upload via REST first, reference the resulting `current_version_id` by UUID.
- OIDC device-code login is stubbed; dev-mode impersonation is the working login path until Phase 9 ships in Powerloom.
- Cross-platform binary matrix not yet wired — build on your host OS for now.
- Server-side Pydantic codegen from this schema not yet adopted in the Powerloom monorepo (drift-detector covers the hand-maintained case).
