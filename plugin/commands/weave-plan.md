---
description: Preview what weave apply would do — no writes. Use before any destructive operation.
---

# /powerloom-home:weave-plan

Preview-only mode.

1. Verify signed in (`weave auth whoami`).
2. Run `weave plan <paths>` with whatever paths the user supplied.
3. Format the output:
   - **Creates** (new resources) — list with + prefix, colorize green
   - **Updates** (diff existing) — list with ~ prefix, show field-level diff if available
   - **Deletes** — list with - prefix, colorize red, WARN if the count is >0
   - **No changes** — resources that match current state

4. If the plan includes a delete on an OU, agent, or skill the user isn't expecting, STOP and explicitly ask if they want to proceed. These are usually mistakes from missing manifest fields.

5. If plan errors before producing output (schema validation etc.), show the full error + suggest `/powerloom-home:weave-diagnose <error>`.

No `-y` / `--auto-approve` flag for plan — it's already non-mutating.
