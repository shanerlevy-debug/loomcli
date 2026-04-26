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
weave session tail <session-id>
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
```

These commands are read-only runtime inspection. They do not modify manifest-backed Agent state.

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

## Debugging

Use `weave ask --raw-events` only when normal streaming output is missing or malformed. For manifest issues, use `weave plan` first and show the full error body, not just the first table column.
