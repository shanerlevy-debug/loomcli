"""Private helpers for ``weave open`` bootstrap.

Thin modules per sprint-2 thread:
    git_ops       — bare-clone + worktree add (864c55a4, this thread)
    (planned)
    session_reg   — agent-session registration + .powerloom-session.env (5fab82ed)
    runtime_exec  — final exec handoff to claude / codex / gemini (53573d73)
    rules_sync    — apply rules_sync directives (53fddf29)

``open_cmd.py`` orchestrates the modules; the modules know nothing about
typer / Console / argv. Tests exercise the modules directly when the
public CLI surface would be over-mocked.
"""
