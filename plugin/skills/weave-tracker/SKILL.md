---
name: weave-tracker
description: Authoritative guide for AI agents (Claude Code, Codex, Gemini, Antigravity) on using the Powerloom tracker via the `weave` CLI to register session work as threads, pluck threads to claim work, post replies as decisions/blockers come up, and mark threads done at PR merge. Required reading per CLAUDE.md / GEMINI.md / AGENTS.md §4.10. Use whenever an agent needs to interact with project tracking — creating work items, claiming items off a backlog, finding what an agent should work on next, or closing out completed work.
---

# Weave Tracker — agent thread workflow

You are an expert on the Powerloom tracker workflow as it applies to AI agent sessions. This skill teaches agents how to register their own work as durable thread records via `weave`, how to find work to do, and how to leave a clean audit trail.

## Why this exists (the context every session needs)

Powerloom maintains a **tracker** subsystem (`tracker_projects → tracker_milestones → tracker_threads → tracker_replies`) that serves as the canonical "what's being worked on right now" surface. Every session — Claude Code, Codex CLI, Gemini CLI, Antigravity, or human — registers its work as a thread per CLAUDE.md / GEMINI.md / AGENTS.md §4.10.

The tracker is the **shared work queue**. When you, as an agent, see a thread's status is `in_progress` and someone else is `plucked_by`, you don't pick up that work. When a thread is `open` and unassigned, it's available. When you finish work, you mark the thread `done` and the next session sees it move off the active board.

**Threads are durable across sessions.** Your conversation context disappears when the session ends or compacts. The thread doesn't.

## The four-step thread lifecycle

Every meaningful unit of agent work follows this flow:

```
┌─────────┐    ┌──────────────┐    ┌───────────┐    ┌──────┐
│ create  │ →  │ pluck (claim)│ →  │ reply (×N)│ →  │ done │
└─────────┘    └──────────────┘    └───────────┘    └──────┘
   open           in_progress         in_progress      done
```

### 1. Create — at session start (or as soon as scope is clear)

```bash
weave thread create \
  --project powerloom \
  --title "<short imperative phrase>" \
  --priority <critical|high|medium|low> \
  --description "<context, repro, definition-of-done>"
```

The `--title` is an imperative phrase: "Fix Alfred WS connection", "Add right-click menu to threads", "Investigate v064 migration drift". Avoid vague titles ("Look into X"); be specific.

The `--description` should follow this shape:

```markdown
**Reported:** YYYY-MM-DD by <person/session>

<one paragraph of context — why are we doing this, what triggered it>

## Repro / current state

<if a bug: numbered repro steps. if a feature: what exists today + what's missing.>

## Definition of done

<bulleted list of concrete outcomes. specific enough that another agent can verify completion without you in the room.>

## Out of scope for this thread

<bulleted list of things you considered but decided NOT to do. helps prevent scope creep + tells reviewers what NOT to ask about.>
```

Save the returned `id` — you'll need it for every subsequent step.

### 2. Pluck — claim the thread for your session

```bash
weave thread pluck <thread_id>
```

This sets `status=in_progress` and stamps `plucked_by` to the current authenticated principal. **Pluck is one-shot** — once a thread is plucked, re-plucking returns 409. If you need to take over a thread someone else plucked, use `weave thread update <id> --assigned-to <principal>` (explicit `weave thread reassign` may land as a future enhancement).

After pluck, the thread shows up in `weave thread list --mine` (your work queue). Other sessions can `weave thread list --status in_progress` to see what's actively being worked on.

### 3. Reply — leave breadcrumbs as you work

```bash
weave thread reply <thread_id> "message body"
weave thread reply <thread_id> --from-stdin < notes.md
```

Use replies for: significant decisions you make, blockers you hit, scope changes, dependencies you discover, links to related PRs/threads/docs. The reply timeline is the audit trail when someone (you, in a future session, or another agent) asks "why did this take three days?" or "what was the actual decision here?"

Don't use replies for: progress narration ("now doing X, now doing Y"). Replies are for moments that future-you would want to see — not a play-by-play.

### 4. Done — close out at PR merge

```bash
weave thread done <thread_id>     # status=done, normal completion
weave thread close <thread_id>    # status=closed, scope abandoned
weave thread wont-do <thread_id>  # status=wont_do, decided not to ship
```

Pick the right verb. `done` is "the work shipped." `closed` is "the thread itself is closed but the underlying scope might still be relevant." `wont-do` is "decided this isn't worth the cost."

The merge commit message for the PR that closes the thread should include the thread URL — `https://app.powerloom.org/projects/powerloom/threads/<id>` — so the cross-link is bidirectional.

## Finding work to do

### Your active queue

```bash
weave thread list --mine                  # everything plucked by you
weave thread list --mine --status in_progress  # only active work
```

### What's open and unclaimed

```bash
weave thread list --project powerloom --status open
weave thread list --project powerloom --status open --priority critical,high
```

### What other sessions are working on

```bash
weave thread list --status in_progress    # all in-flight work
weave thread list --assignee <principal>  # specific session/agent's queue
```

### A specific thread

```bash
weave thread show <thread_id>             # full detail + reply history
```

## When NOT to file a thread

Per CLAUDE.md/GEMINI.md/AGENTS.md §4.10, the threshold is meaningful work — not every conversational exchange. Skip the thread for:

- **Conversational answers** — "what does X do?", "explain this code".
- **Single-line clarifications** — "did you mean A or B?".
- **Reading + summarizing existing code** without changing it.
- **The thread-creation gesture itself** — don't recurse.

Do file a thread for:

- Anything that touches code or docs (every PR has a thread).
- Investigations / spikes that take >30 minutes.
- Bug fixes (every fix is a thread, even if it's a one-liner).
- Doc-only updates that span multiple files (per §4.2's 4-file threshold — same threshold).

When in doubt: file the thread. Cheap to create, easy to mark `wont_do` if it turns out to be nothing.

## Multi-session coordination

If you discover that a thread you want to work on is already plucked by another session:

1. **Don't pick up the work.** Two sessions on the same thread is a coordination bug.
2. **Look at the thread's `plucked_by` and `assigned_to`** — if it's a sub-principal of the same user (e.g. another Claude Code session of yours), you can reach out via the relevant channel.
3. **If the other session is stuck or stale,** post a reply: `weave thread reply <id> "Other session: are you still on this? If not I'll take over."` — wait a reasonable interval before reassigning.
4. **If you're delegating to another agent** (e.g. you orchestrate a Codex session to do part of the work), file a child thread or post a reply linking the delegated work, don't assign your thread to the agent.

For full coordination protocol, see `docs/coordination.md`.

## Sub-principal attribution (Phase 23 M2 + tracker integration, in flight)

When acting on behalf of a user (not as the user directly), agent sessions are first-class **sub-principals** — registered via `POST /me/agents` against the parent user's auth. Sub-principal attribution is the right pattern for "Claude Code session X did this work for Shane."

**Today's state:** the `tracker_threads` schema doesn't yet have first-class sub-principal columns (Phase 15 pre-dates Phase 23 M2 — see the engine integration thread filed 2026-04-26). Interim: stamp attribution into `metadata_json.session_attribution`:

```bash
weave thread update <id> --metadata '{"session_attribution":{"subprincipal_id":"<sp-id>","subprincipal_name":"<name>","client_kind":"claude-code","stamped_at":"<iso>"}}'
```

Once the engine ships first-class columns, this metadata fallback retires and the tracker auto-stamps from the request's auth principal.

## Common error modes

| Error | Cause | Fix |
|---|---|---|
| 409 "Thread already plucked" | Tried to pluck a thread that's already in_progress | Use `weave thread show <id>` to see who has it; coordinate before reassigning |
| 422 on create | Missing required `title` or `priority` | Both are required; check `weave thread create --help` for the full schema |
| 404 on operations | Wrong thread_id or no permission to view (cross-org) | Verify the ID; sessions can only operate on threads in their own org |
| 403 on actions | Trying to pluck/update someone else's thread without reassignment | Today: tracker is org-open per Phase 15; if you see 403 here it's a bug — file a thread |
| "Not signed in" | No active credentials | Run `weave login` first |

## Idempotency notes

- **Create is NOT idempotent.** Re-running creates duplicate threads. If you're not sure whether you already filed something, search first: `weave thread list --search "<title fragment>"`.
- **Pluck is NOT idempotent.** Once a thread is in_progress, re-plucking returns 409. Use `weave thread show` to confirm state before retry.
- **Reply IS idempotent in effect** but each call creates a new reply row. Don't loop.
- **Done IS idempotent** — repeating it on an already-`done` thread is a no-op (same goes for `closed` / `wont_do`).

## Quick reference card

```bash
# Lifecycle
weave thread create --project <slug> --title "..." --priority high --description "..."
weave thread pluck <id>
weave thread reply <id> "..."
weave thread done <id>

# Queries
weave thread list --mine
weave thread list --project <slug> --status open --priority high,critical
weave thread show <id>

# Maintenance
weave thread update <id> --status review  # advance state without closing
weave thread update <id> --assigned-to <principal_id>
weave thread update <id> --metadata '{"key":"value"}'
```


## Reference: the canonical example threads

The first six dogfood threads filed against the powerloom project on 2026-04-26 are the reference for "how should a thread description read":

- `8d2c7502` — Alfred never connects
- `c41a8294` — No good way to mark a thread complete
- `3671bfaf` — Right-click context menu on threads
- `9210c2c2` — Design + ship workflow for assigning tasks to agents
- `e011a581` — Sprints feature
- `2be84503` — loomcli: ship `weave thread …` subcommands
- `a0a715dc` — Tracker: first-class sub-principal attribution

Read any of them with `weave thread show <id>` to see the canonical structure: context-up-front, repro/current-state, definition-of-done, out-of-scope. Match that pattern when you file your own threads.
