---
description: Capture the current Claude Code conversation as a Powerloom coordination handoff.
---

# /powerloom-home:weave-pluck-thread

The user wants to "pluck this thread" into Powerloom.

Treat this as a coordination handoff for the current conversation:

1. Summarize the thread into a short title, safe scope slug, one-line summary, key decisions, touched files/commands, and next actions.
2. If the user wants it registered and `weave whoami` succeeds, run:

```bash
weave agent-session register --scope "<slug>" --summary "<one-line>" --branch "<branch>" --capabilities "<comma,tags>"
```

3. Use `weave doctor` to confirm advertised actor kinds. If supported, use `--actor-kind codex_cli` for Codex, `--actor-kind gemini_cli` for Gemini, or `--actor-kind antigravity` for Antigravity. Do not use legacy short values such as `codex` or `gemini`.
4. If the user is not signed in or the API is unavailable, return the handoff summary and the exact registration command they can run later.

Arguments from the user:

`$ARGUMENTS`
