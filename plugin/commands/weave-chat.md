---
description: Start an interactive terminal chat with a Powerloom agent through `weave chat`.
---

# /powerloom-home:weave-chat

The user wants an interactive terminal chat with a Powerloom agent.

Use `weave chat $ARGUMENTS`.

Expected argument shape:

```bash
weave chat <agent-uuid-or-/ou/path/name>
```

Optional first-turn prompt:

```bash
weave chat <agent> "first prompt"
```

Provider-agnostic rule: the CLI does not choose or call a model provider. Powerloom invokes the selected agent, and the backend uses that agent's runtime/model plus the user/org's configured provider credential.

Before running it:

1. Run `weave whoami`.
2. If not signed in, tell the user to run `weave login`.
3. Remind the user they can type `/exit` or `/quit` to leave the chat.
