---
description: Interpret a weave CLI or Powerloom API error. Drops the error into the weave-interpreter skill for structured diagnosis.
---

# /powerloom-home:weave-diagnose

The user hit a weave or Powerloom API error and wants help interpreting.

**Arguments:** the error text (paste, file path, or last command output).

**Workflow:**

1. **Read the `weave-interpreter` skill** if not already in your context — it has the authoritative reference for weave command behavior, address syntax, approval gates, schema versions, and common error shapes.
2. **Classify the error:**
   - **Connection refused / 0** → API URL wrong or server down
   - **HTTP 401** → token missing/expired
   - **HTTP 403** → permission gap, check RBAC
   - **HTTP 404** → resource/OU doesn't exist
   - **HTTP 409 `justification_required`** → approval policy, supply `--justification`
   - **HTTP 409 `approval_required`** (pending 202) → needs human approval
   - **HTTP 500** → server bug, capture full body + traceback for escalation
   - **Schema validation error** → manifest doesn't match bundled schema
3. **Produce a diagnosis:**
   - What the error means in plain English
   - Whether it's user-fixable, config-fixable, or needs escalation
   - Concrete next-step command(s) if fixable

**Example:**

> ERROR: HTTP 409 POST /skills: {"code": "justification_required", "message": "this policy requires a justification..."}

Diagnosis: the user's org has an approval policy that demands a justification string for this mutation. Fix: rerun with `weave --justification "your reason" apply ...`.

**For anything that looks like an engine bug (500s, unexpected crashes):**

- Ask the user for the server-side traceback (from app.powerloom.org logs or the browser devtools Network tab).
- Suggest filing at `github.com/shanerlevy-debug/Powerloom/issues` with the error body + what they were trying to do.
- Don't try to work around engine bugs client-side; they need fixing at source.
