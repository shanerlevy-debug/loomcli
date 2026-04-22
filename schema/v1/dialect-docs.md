# Powerloom Dialect Extensions (`x-powerloom-*`)

The Powerloom schema sits on top of **JSON Schema Draft 2020-12** but adds ten annotation keywords that carry control-plane semantics. This document explains each one, when to use it, and what tooling does with it.

> **Tooling contract.** Tools that understand the dialect (the Powerloom CLI, the server admission layer, the meta-agent) honor these keywords. Tools that don't (VSCode, `yaml-language-server`, generic validators) treat them as inert annotations — the schema still parses and the other Draft 2020-12 keywords still work.

---

## `x-powerloom-ref`

**Purpose.** Declares that a string field is a reference to another Powerloom resource.

**Shape.**

```json
"owner_principal_ref": {
  "type": "string",
  "x-powerloom-ref": {
    "kind": "Principal",
    "resolve_by": "name",
    "scope": "organization",
    "on_missing": "error"
  }
}
```

**What honors it.**

- **CLI planner** resolves `resolve_by: name` refs to IDs at apply time, fails planning if `on_missing: error` and target is absent.
- **Server admission** re-validates the target exists at admit time and rejects with `RefNotFound` + a specific error message.
- **Meta-agent + LLM sessions** use `kind` to know which catalog to search when filling in a field.

**Why not `$ref`?** Vanilla `$ref` points within a schema. `x-powerloom-ref` points at a *live resource* in the control plane.

---

## `x-powerloom-server-field`

**Purpose.** Marks fields populated by the server — IDs, timestamps, hash chains, reconciler state, audit row pointers.

**Shape.**

```json
"id": {
  "type": "string",
  "format": "uuid",
  "x-powerloom-server-field": true
}
```

**What honors it.**

- **CLI `plan`** strips server fields from user manifests before diffing against server state.
- **Server admission** rejects input that sets a server field with `ServerFieldRejected: field 'id' is server-assigned`.
- **Pydantic codegen** marks the field `Optional[...] = None` on input models and required on output models.

---

## `x-powerloom-immutable`

**Purpose.** Fields that can be set on create but never changed afterward (e.g. `kind`, `organization_id`, `created_at`, audit primary keys).

**Shape.**

```json
"organization_id": {
  "type": "string",
  "format": "uuid",
  "x-powerloom-immutable": true
}
```

**What honors it.**

- **Server admission on PATCH/PUT** compares the proposed value to the stored value; if they differ, rejects with `ImmutableFieldChanged: cannot change 'organization_id' after create`.
- **CLI `plan`** surfaces attempts to change immutable fields as hard planner errors, not drift.

---

## `x-powerloom-reconciler-hint`

**Purpose.** Tells the control plane which reconciler cares about a field and how expensive a change is.

**Shape.**

```json
"spec.system_prompt": {
  "type": "string",
  "x-powerloom-reconciler-hint": {
    "reconcilers": ["agent"],
    "on_change": "full-reconcile",
    "cost": "expensive"
  }
}
```

**What honors it.**

- **Reconciler scheduling:** cheap drift-check fields run on a fast cadence; expensive full-reconcile fields only run on explicit change.
- **CLI `diff`** color-codes changes by cost so operators see what will incur a provider call.

---

## `x-powerloom-secret-ref`

**Purpose.** Declares that a field, resolved, yields a secret. Manifests never contain secret values — they reference credentials.

**Shape.**

```yaml
spec:
  auth:
    api_key:
      credential_ref: prod-slack-bot  # ← x-powerloom-secret-ref applies here
```

Schema:

```json
"credential_ref": {
  "type": "string",
  "x-powerloom-ref": { "kind": "Credential" },
  "x-powerloom-secret-ref": {
    "credential_kind": "api_key",
    "rotation": "manual"
  }
}
```

**What honors it.**

- **CLI** never logs or echoes the resolved secret. Marks the field as `***` in plan output.
- **Server** routes reads through the `CredentialStore` protocol, subject to RBAC.
- **Auditors** see "secret read: credential=prod-slack-bot by agent=xyz" entries, not the value.

---

## `x-powerloom-default-from-server`

**Purpose.** Client MAY set the field, but if they omit it, the server fills in a policy-driven default.

**Shape.**

```json
"spec.model": {
  "type": "string",
  "x-powerloom-default-from-server": {
    "source": "org-policy",
    "policy_key": "agent.default_model"
  }
}
```

**Distinguished from** `x-powerloom-server-field`: that one forbids client-side setting; this one prefers but doesn't require client-side setting.

**What honors it.**

- **Server admission** fills the field if absent, from the policy key, before writing the row.
- **CLI `plan`** surfaces defaulted values so operators see what the server picked.

---

## `x-powerloom-auxiliary`

**Purpose.** Lands in Phase 15.4. Marks a kind as accepting custom attributes injected via an `AuxiliaryClass` manifest (AD auxiliary-class pattern).

**Shape.**

```json
{
  "$id": ".../kinds/agent.schema.json",
  "x-powerloom-auxiliary": {
    "storage": "x_attributes",
    "allowed_classes": ["PersonaExt", "CostCenterExt"]
  }
}
```

**What honors it.**

- Target tables grow a `x_attributes jsonb NOT NULL DEFAULT '{}'` column.
- `AuxiliaryClass` manifests declare additional JSON-Schema `properties` validated against `x_attributes` on write.
- **CLI** merges `x_attributes` into the rendered manifest on `get`.

---

## `x-powerloom-example`

**Purpose.** A minimal, valid example payload for a field or an entire kind. Critical to the luddite-demo target — these are what a Claude session pattern-matches off.

**Shape.**

```json
"spec.system_prompt": {
  "type": "string",
  "x-powerloom-example": "You are a support triage agent. Given a ticket, classify urgency and draft a first response."
}
```

**What honors it.**

- **CLI `powerloom kind example <kind>`** (Phase 15.2) renders a minimal manifest assembled from field-level examples.
- **Meta-agent** seeds prompts with these examples when helping operators author manifests.

---

## `x-powerloom-tier-availability`

**Purpose.** Gates kinds or fields by tier. Enforced by the Phase 15 (Monetization, now Phase 16) middleware.

**Shape.**

```json
{
  "$id": ".../kinds/workflow.schema.json",
  "x-powerloom-tier-availability": {
    "tiers": ["team", "business", "enterprise"],
    "cap_metric": "workflows_per_org"
  }
}
```

**What honors it.**

- **Server admission** rejects manifests for locked kinds on lower tiers with `TierFeatureLocked: kind 'Workflow' requires tier >= team`.
- **UI** hides locked surfaces or shows them with an upgrade prompt.

---

## `x-powerloom-apply-order`

**Purpose.** Tells the CLI planner what order to create/delete resources across kinds. OUs before Groups before Agents before AgentSkill attachments.

**Shape.**

```json
{
  "$id": ".../kinds/agent.schema.json",
  "x-powerloom-apply-order": 80
}
```

**Canonical ordering.**

| Kind | Order |
|---|---|
| OU | 10 |
| Group | 20 |
| RoleBinding | 30 |
| Credential | 40 |
| Skill | 50 |
| MCPServerRegistration | 60 |
| MCPDeployment | 70 |
| Agent | 80 |
| AgentSkill | 90 |
| AgentMCPServer | 95 |
| Workflow | 100 |

Deletes happen in reverse.

---

## What happens when a tool does NOT understand the dialect

Every `x-powerloom-*` keyword is a no-op in vanilla Draft 2020-12. VSCode + `yaml-language-server` will happily validate a Powerloom manifest for syntax, type, required-fields — they just won't enforce ref resolution, server-field rejection, immutability, or tier gates. That's fine: those are all checked on the control plane at apply time.
