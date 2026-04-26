---
description: Tail durable Powerloom session events through `weave session tail`.
---

# /powerloom-home:weave-session-tail

The user wants to see what happened or is happening inside a Powerloom session.

Use `weave session tail $ARGUMENTS`.

Expected argument shape:

```bash
weave session tail <session-id>
```

Useful variants:

```bash
weave session events <session-id>
weave session tail <session-id> --raw-events
weave session tail <session-id> --after-seq <n>
```

This reads durable session events from Powerloom. It does not need the one-time WebSocket ticket returned by `weave ask`.
