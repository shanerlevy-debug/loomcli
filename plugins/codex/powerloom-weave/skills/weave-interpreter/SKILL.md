---
name: weave-interpreter
description: Use when Codex needs to operate the Powerloom `weave` CLI, ask or chat with a Powerloom agent, write or validate manifests, handle auth, troubleshoot apply errors, or explain loomcli behavior.
---

# Weave Interpreter

You are an expert operator for `weave`, the CLI shipped by the `loomcli` package for Powerloom.

## Core Rule

Do not call Anthropic, OpenAI, Gemini, or other model-provider APIs directly for Powerloom agent sessions. Use Powerloom as the control plane:

```bash
weave ask <agent-uuid-or-/ou/path/name> "prompt"
weave chat <agent-uuid-or-/ou/path/name>
```

The target Powerloom Agent decides provider/model through its `runtime_type` and `model` fields. The backend uses the user/org runtime credential configured in Powerloom.

## Common Commands

```bash
weave login
weave whoami
weave ask /dev-org/alfred "What should I work on next?"
weave chat /dev-org/alfred
weave agent status /dev-org/alfred
weave agent config /dev-org/alfred
weave agent set-model /dev-org/alfred --model gpt-5.5
weave session tail <session-id>
weave profile set --default-agent /dev-org/alfred --default-runtime openai --default-model gpt-5.5
weave commands --json
weave plan manifest.yaml
weave apply manifest.yaml
weave apply -y ./manifests/
weave get agents --ou /dev-org
weave describe agent /dev-org/alfred
weave skill upload-and-activate /dev-org/skill-name ./skill.zip
```

## Agent Addressing

Prefer full agent paths when available:

```bash
weave ask /org/ou/agent-name "prompt"
```

If the user gives a bare name, add `--ou /org/ou` when needed:

```bash
weave ask alfred "prompt" --ou /dev-org
```

UUIDs also work.

## Agent/Session Observability

Use these when the user asks what an agent is doing:

```bash
weave agent status /org/ou/agent-name
weave agent sessions /org/ou/agent-name
weave agent watch /org/ou/agent-name --interval 3
weave session events <session-id>
weave session tail <session-id>
weave agent-session status <agent-session-id>
weave agent-session watch <agent-session-id> --interval 3
weave thread my-work --watch --interval 5
```

These commands are read-only runtime and coordination inspection. They do not modify manifest-backed Agent state.

## Thread Plucking

If the user asks to "pluck this thread", treat it as a Powerloom handoff/coordination capture for the current conversation, not as a model-provider call.

1. Summarize the active thread into a short title, a safe scope slug, a one-line summary, key decisions, touched files/commands, and next actions.
2. If the user wants it registered and `weave whoami` succeeds, use the current coordination-session schema:

```bash
weave agent-session register --scope "<slug>" --summary "<one-line>" --branch "<branch>" --capabilities "<comma,tags>" --actor-kind codex_cli
```

3. Use supported actor kinds such as `codex_cli`, `gemini_cli`, `antigravity`, `claude_code`, `cma`, or `human`. If an older control plane rejects the value, omit `--actor-kind` and mention the compatibility fallback.
4. If the user is not signed in or no API is available, output the handoff summary plus the exact `weave agent-session register ...` command they can run later.

## Provider/Model Configuration

Use `weave agent config <agent>` to inspect runtime/model and `weave agent set-model <agent> --model <model>` to update the model through Powerloom. Do not pass provider/model flags to `weave ask` or `weave chat`; those commands use the target Agent's configured runtime/model. Runtime/provider changes are manifest-owned until the API exposes a safe runtime patch endpoint.

## Auth

If any command reports "Not signed in", run:

```bash
weave login
weave whoami
```

For local dev:

```bash
weave --api-url http://localhost:8000 login --dev-as admin@dev.local
```

Never print, commit, or echo raw PATs.

## Approval Gates

If mutations fail with `justification_required`, rerun with:

```bash
weave --justification "reason for the change" apply manifest.yaml
```

or set `POWERLOOM_APPROVAL_JUSTIFICATION`.

If an operation creates a pending approval, poll it with:

```bash
weave approval wait <approval-id>
```

## Debugging

Use `weave ask --raw-events` only when normal streaming output is missing or malformed. For manifest issues, use `weave plan` first and show the full error body, not just the first table column.
