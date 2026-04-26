---
description: Inspect Powerloom tracker threads through `weave thread my-work`.
---

# /powerloom-home:weave-my-work

The user wants to inspect their Powerloom coordination work queue.

Use `weave thread my-work $ARGUMENTS`.

Useful variants:

```bash
weave thread my-work
weave thread my-work --watch --interval 5
weave thread my-work --status open
weave agent-session watch <agent-session-id> --interval 3
```

This is read-only coordination observability. Do not pluck, close, or mutate threads unless the user separately asks for that.
