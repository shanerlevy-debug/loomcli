---
description: Drive Powerloom tracker thread workflow — create, pluck, reply, done. Auto-loads the weave-tracker skill for full context.
---

# /powerloom-home:weave-thread

The user wants to interact with Powerloom tracker threads (create, pluck, reply, mark done, list, show). Per CLAUDE.md §4.10, this is the canonical interface for registering session work.

**Your job:**

1. **Auto-load the `weave-tracker` skill first.** It contains the full thread lifecycle, description-shape conventions, error mode reference, and quick reference card. Don't duplicate that content here — read it.

2. **Figure out which sub-action the user wants.** Common forms:
   - "Create a thread for X" → run `weave thread create --project powerloom --title "..." --priority ... --description "..."`. Use the description-shape from the skill (Reported / Repro / Definition of done / Out of scope).
   - "Pluck thread `<id>`" → run `weave thread pluck <id>`.
   - "Reply on thread `<id>` saying Y" → run `weave thread reply <id> "Y"`.
   - "Mark thread `<id>` done" → run `weave thread done <id>` (or `close` / `wont-do` per the skill's verb-selection guidance).
   - "What am I working on?" → run `weave thread list --mine --status in_progress`.
   - "What's open and high-priority?" → run `weave thread list --project powerloom --status open --priority critical,high`.
   - "Show me thread `<id>`" → run `weave thread show <id>`.

3. **Push back on vague titles when creating.** Titles should be imperative phrases ("Fix Alfred WS connection"), not vague ones ("Look into X"). If the user gave a vague title, ask them to refine it before running the create command.

4. **Capture the returned thread `id` after a create** — show it back so the user can use it for follow-up commands.

5. **Surface pluck collisions clearly.** If `weave thread pluck` returns 409 "Thread already plucked", run `weave thread show <id>` and show the user who has it (`plucked_by` + `plucked_by_subprincipal_name` if present in metadata) before suggesting reassignment.

**If the user is not signed in:** run `weave auth whoami` to confirm and direct them to `/powerloom-home:weave-login` first.

**If `weave thread …` subcommands are not yet available** (the CLI subcommand family is in flight at the time of writing — tracked as thread `2be84503`): fall back to direct API calls via curl or the helper script pattern at `scratch/create_dogfood_threads.py` in the Powerloom repo. The skill covers this fallback in detail.

Use the `weave-tracker` skill for anything non-obvious.
