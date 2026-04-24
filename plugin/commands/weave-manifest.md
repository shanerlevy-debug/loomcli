---
description: Author a new Powerloom manifest from a natural-language description. Picks the right kind, fills required fields, validates against the bundled schema before returning.
---

# /powerloom-home:weave-manifest

The user wants you to write a new manifest.

**Clarify if missing:**
- What kind? (Agent / Skill / WorkflowType / MemoryPolicy / OU / Scope / Compose / etc.)
- Target OU path?
- Owner principal (for agents)?
- Model (for agents)?
- Any v1.2.0 extensions needed (system skills with auto_attach_to, coordinator agents, etc.)?

**Authoring rules:**

1. **Start from the schema.** Read `loomcli/schema/v1/kinds/<kind>.schema.json` (or `loomcli/schema/v2/stdlib/<kind>.schema.json` if the user is on schema 2.0.0) to know exact field shapes.
2. **Use the `weave-interpreter` skill** for the authoritative CLI semantics (apply, plan, idempotency behavior, etc.)
3. **apiVersion = powerloom.app/v1** unless the user explicitly requests v2 (v2 requires loomcli 0.6.0+ + engine v056+).
4. **No comments in the YAML body** — comments at the top of the file are fine for human readability, but keep the manifest itself clean.
5. **Sensible defaults** — don't ask for every optional field. Use reasonable defaults for:
   - `runtime_type: cma` (agents)
   - `agent_kind: user` (agents)
   - `skill_type: archive` (skills)
   - `current_version_id: null` (skills — upload happens after apply)

**Output shape:**

```yaml
# <one-line comment explaining this manifest>
apiVersion: powerloom.app/v1
kind: <Kind>
metadata:
  name: <slug>
  ou_path: <path>
spec:
  <fields>
```

**After producing the manifest:** offer to validate + apply it via `/powerloom-home:weave-plan` first.

**If the user asks for a custom kind via compose** (v2 only), use the `compose.schema.json` definition and include `spec.compose` with explicit slot specifications.
