"""Apply launch-spec ``rules_sync`` directives to the worktree.

Iterates over ``spec.rules_sync`` after worktree creation and, for
each directive, invokes ``weave conventions sync`` once per runtime
in the directive. The conventions sync command writes the engine's
composed view (org → OU → project → group hierarchy) into the
worktree's ``CLAUDE.md`` / ``AGENTS.md`` / ``GEMINI.md`` so the
launched agent boots with the right rules for *now*, not whatever
was committed at the cloned branch.

Failure-mode policy (per thread DoD):
  * One runtime within a directive failing → warn, keep going on
    the other runtimes for that directive. Partial rules are better
    than no rules.
  * All runtimes within a directive failing → directive recorded
    as failed; caller decides whether to abort or continue.
  * Empty ``spec.rules_sync`` → no-op, no log line.

We never *abort* the launch on rules-sync failure — at this point
the worktree is already on disk and the session is registered, so
the agent can still work; the user can re-sync manually.

Sprint cli-weave-open-20260430, thread 53fddf29.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from loomcli.schema.launch_spec import LaunchSpec, RulesSyncDirective


# ``sync_fn`` signature mirrors the kwargs ``loomcli.commands.conventions_cmd.sync``
# accepts. We call it via kwargs so tests can substitute a no-op without
# fishing for the typer-decorated callable's underlying impl.
SyncFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass
class DirectiveResult:
    scope: str
    succeeded_runtimes: list[str] = field(default_factory=list)
    failed_runtimes: list[tuple[str, str]] = field(default_factory=list)
    """``(runtime, error_message)`` per failed runtime in this directive."""

    @property
    def fully_succeeded(self) -> bool:
        return bool(self.succeeded_runtimes) and not self.failed_runtimes

    @property
    def fully_failed(self) -> bool:
        return bool(self.failed_runtimes) and not self.succeeded_runtimes


# ---------------------------------------------------------------------------
# Default sync_fn — imports the real command at call time
# ---------------------------------------------------------------------------


def _default_sync_fn() -> SyncFn:
    from loomcli.commands.conventions_cmd import sync as _sync
    return _sync


def apply_directives(
    spec: LaunchSpec,
    worktree: Path,
    *,
    sync_fn: Optional[SyncFn] = None,
) -> list[DirectiveResult]:
    """Apply each ``RulesSyncDirective`` to ``worktree``. Returns per-directive
    results; an empty ``spec.rules_sync`` returns ``[]``.

    ``sync_fn`` defaults to the real ``conventions_cmd.sync`` callable;
    tests inject a stub.
    """
    if not spec.rules_sync:
        return []

    fn = sync_fn or _default_sync_fn()
    results: list[DirectiveResult] = []
    for directive in spec.rules_sync:
        result = _apply_single(directive, worktree, fn)
        results.append(result)
    return results


def _apply_single(
    directive: RulesSyncDirective,
    worktree: Path,
    sync_fn: SyncFn,
) -> DirectiveResult:
    out = DirectiveResult(scope=directive.scope)
    for runtime in directive.runtimes:
        try:
            sync_fn(
                scope=directive.scope,
                runtime=runtime,
                workdir=worktree,
                dry_run=False,
                quiet=True,
            )
            out.succeeded_runtimes.append(runtime)
        except typer.Exit as exc:
            # conventions_cmd.sync raises typer.Exit(2) on scope-resolution
            # failure; treat as a per-runtime failure and continue.
            out.failed_runtimes.append((runtime, f"typer.Exit({exc.exit_code})"))
        except Exception as exc:  # noqa: BLE001 — broad catch is intentional
            out.failed_runtimes.append((runtime, str(exc) or type(exc).__name__))
    return out
