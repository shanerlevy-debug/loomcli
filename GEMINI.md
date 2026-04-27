# GEMINI.md — loomcli project conventions

Working agreement between Shane and Gemini for this project.

## 1. Tracker thread workflow (CLAUDE.md / GEMINI.md / AGENTS.md §4.10)

Per the project's working agreement, every agent session — including this Gemini CLI session — registers its work as a tracker thread in the relevant Powerloom project. Use the `weave thread` subcommands:

```bash
# At session start, file a thread for the work
weave thread create --project powerloom --title "<imperative phrase>" --priority high --description "<context, repro, definition-of-done>"

# Claim it (sets status=in_progress)
weave thread pluck <thread_id>

# As decisions/blockers come up, post replies
weave thread reply <thread_id> "decision: chose option A because..."

# At PR merge, mark done
weave thread done <thread_id>
```

## 2. Actor Kind and Identification

When using `weave agent-session register`, always specify `--actor-kind gemini_cli` unless it is automatically detected. This session is identified by the `GEMINI_CLI=1` environment variable.

## 3. Tool and Token Efficiency

- Prefer `grep_search` to find code patterns over reading many files.
- Minimize redundant reads of large files; use `read_file` with `start_line` and `end_line` for surgical reads.
- When proposing changes, provide the exact `replace` or `write_file` content to minimize back-and-forth.

## 4. Branching and Merging

- Always work on a feature branch (e.g., `gemini-exploration`).
- Do not commit directly to `main`.
- Use `weave agent-session end --outcome merged` after your PR is merged.
