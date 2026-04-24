---
description: Apply a Powerloom manifest file — validates, previews the plan, then applies. Handles approval-gated policies via --justification. Streams full error bodies.
---

# /powerloom-home:weave-apply

The user wants to apply a manifest file to Powerloom.

**Arguments:** one or more paths (files or directories). If the user didn't specify a path, ask.

**Flow:**

1. **Pre-check** — verify the user is signed in (`weave auth whoami`). If not, recommend `/powerloom-home:weave-login` first.
2. **Plan first** — always run `weave plan <paths>` before `apply` unless the user explicitly said "just apply." Show the plan output. If the plan shows a significant delete or one-way change (policy archive, OU destroy, etc.), stop and confirm with the user.
3. **Apply** — run `weave apply -y <paths>`. The `-y` / `--auto-approve` flag is required in this workflow because we're non-interactive.
4. **On approval-gate 409 (`justification_required`):** re-run with `weave --justification "<reason>" apply -y <paths>`. Generate a reasonable justification from the manifest contents (e.g. "Deploying <kind> <name> for <ou-path>") unless the user supplied one.
5. **On any other error:** surface the full error body (not the table-truncated summary). `weave apply` v0.5.3+ prints full errors below the table for failed rows.

**Outcomes to report:**

- **All created/updated** → summary + path to what was created
- **Partial failure** → which resources landed, which didn't, and why
- **Pending approval (202)** → tell the user approval is queued, give them the approval ID, suggest they check `/powerloom-home:weave-status` or mint-a-PAT-and-approve flow
- **Full failure** → error verbatim + diagnostic suggestions (schema mismatch? auth? cross-ref to missing resource?)

Use `weave-interpreter` skill when error messages are non-obvious.

**Idempotency:** weave apply is safe to re-run. Already-applied resources no-op. If the user's command failed mid-batch, running again just resumes.
