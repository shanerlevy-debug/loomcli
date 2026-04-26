---
description: Ask a Powerloom agent through `weave ask`. Streams the answer from the control plane using the agent's configured runtime/model.
---

# /powerloom-home:weave-ask

The user wants to ask a Powerloom agent a single question from Claude Code.

Use `weave ask $ARGUMENTS`.

Expected argument shape:

```bash
weave ask <agent-uuid-or-/ou/path/name> "prompt text"
```

Provider-agnostic rule: do not call Anthropic, OpenAI, Gemini, or any other model API directly. `weave ask` calls `POST /agents/{id}/invoke`; the Powerloom control plane uses the target agent's configured `runtime_type`, `model`, and the user/org's stored runtime credential.

Before running it:

1. Run `weave whoami`.
2. If not signed in, tell the user to run `weave login`.
3. If the agent argument is a bare name and lookup is ambiguous, rerun with `--ou <ou-path>` or use the full `/ou/path/agent-name`.

If streaming fails, report the error and suggest retrying with `weave ask <agent> "prompt" --raw-events` for debugging.
