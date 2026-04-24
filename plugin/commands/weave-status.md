---
description: Summarize the current Powerloom control-plane state — signed-in identity, OUs, skills, agents. Quick at-a-glance inventory.
---

# /powerloom-home:weave-status

The user wants a snapshot of Powerloom state.

**Gather and report:**

1. **Identity** — run `weave auth whoami`. If not signed in, stop here and recommend `/powerloom-home:weave-login`.
2. **OUs** — run `weave get ou`. Count + show first 5. If >5, note "and N more."
3. **Skills** — run `weave get skill`. Count + show first 5.
4. **Agents** — run `weave get agent`. Count + show first 5.
5. **Recent activity (optional)** — if the user asked for detail, also run `weave get workflow` and summarize any pending approval requests from `weave auth pat list` or similar.

**Format the report:**

```
Powerloom status — <api_base_url>
Signed in as: <email> (<user-id>) @ org <org-id>

OUs:    <n>
  - /<path-1>
  - /<path-2>
  ...

Skills: <n>
  - /<ou>/<name>  (type: archive/tool_definition, version: <short-id or null>)
  ...

Agents: <n>
  - /<ou>/<name>  (model: <model>, coordinator: yes/no)
  ...
```

**Use the right mode:** if the user's context suggests home edition (the MCP server is available), prefer the `powerloom_list_*` tools over shelling out to `weave` — they return structured JSON directly. Otherwise shell out.

**If anything errors:** surface the error verbatim + suggest `weave-diagnose <error>` for deep interpretation.
