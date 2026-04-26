# Powerloom Weave Extension

Use `weave`, the CLI from the `loomcli` package, to interact with Powerloom.

## Provider-Agnostic Agent Sessions

Do not call Anthropic, OpenAI, Gemini, or any provider SDK directly for a Powerloom agent session. Use:

```bash
weave ask <agent-uuid-or-/ou/path/name> "prompt"
weave chat <agent-uuid-or-/ou/path/name>
```

The Powerloom backend uses the target Agent's `runtime_type`, `model`, and the user/org runtime credential configured in Powerloom.

## Common Commands

```bash
weave login
weave whoami
weave ask /dev-org/alfred "What should I work on next?"
weave chat /dev-org/alfred
weave plan manifest.yaml
weave apply manifest.yaml
weave get agents --ou /dev-org
weave describe agent /dev-org/alfred
```

If a mutation is approval-gated, use:

```bash
weave --justification "reason for this change" apply manifest.yaml
```

Never print, store, or commit raw PATs.
