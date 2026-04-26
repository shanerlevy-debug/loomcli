---
description: Inspect what a Powerloom agent is doing through `weave agent status`.
---

# /powerloom-home:weave-agent-status

The user wants to inspect a Powerloom agent's runtime state.

Use `weave agent status $ARGUMENTS`.

Expected argument shape:

```bash
weave agent status <agent-uuid-or-/ou/path/name>
```

Useful follow-ups:

```bash
weave agent sessions <agent-uuid-or-/ou/path/name>
weave agent watch <agent-uuid-or-/ou/path/name> --interval 3
```

This is read-only observability. Do not change manifests, provider, model, credentials, or runtime settings unless the user separately asks for a mutation.
