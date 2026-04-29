"""``weave agent run`` — universal self-hosted-agent daemon.

Per the universal-self-hosted-daemon reframe in the Powerloom monorepo
(thread aad43ba0 / 2026-04-29): any agent registered with
``runtime_type='self_hosted'`` is meant to be driven by a daemon
running on the operator's host. The daemon polls the platform for
work, dispatches each item to the matching skill handler, and lets
the platform record outcomes. The platform owns destructive actions
(API endpoints) — the daemon is a polling-loop dispatcher, not a
business-logic engine.

This module is the daemon. The reconciler is the v1 reference
implementation (``pr_reconciliation`` skill, defined below). Future
self-hosted agents (legal review, ad optimization, anything) plug in
by registering a new skill handler — no new CLI surface, no new
endpoints.

Surface:
    weave agent run <agent>                   foreground daemon
    weave agent run <agent> --once            single tick + exit
    weave agent run <agent> --dry-run         decide but don't act
    weave agent run <agent> --interval 30     poll cadence in seconds

Auth flows from ``weave login`` — same access token as every other
``weave`` command. The operator running the daemon must have
``agent:read`` on the agent's OU. The daemon resolves the agent by
UUID, ``/ou/path/agent-name``, or unique name (same rules as
``weave agent status``).

Singleton enforcement (option b from thread aad43ba0): the daemon
heartbeats into ``agent_sessions`` on each tick. Two daemons running
the same agent will both heartbeat — they don't fight, but their
operator can see in ``weave agent sessions`` which is which. If we
ever see actual contention in the wild, the platform-side advisory-
lock upgrade (option c) lands as a follow-up.

Non-goals (deferred):
    * Background / daemonized mode. Use systemd, supervisord, or just
      ``nohup weave agent run … &`` if you want to detach.
    * ``weave agent stop`` / ``weave agent daemon-status``. Foreground-
      only v1; Ctrl+C stops the loop cleanly.
    * Per-skill metrics. Logs are stdout-only in v1; pipe to your
      log aggregator of choice.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional

import typer
from rich.console import Console

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import RuntimeConfig

_console = Console()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------
# A skill is the pair (decision endpoint + action endpoints) that the
# daemon should call for items of a given task_kind. Registered by
# import-time side effect, mirroring the server-side projector pattern.
#
# Signature: ``async fn(client, item, *, dry_run) -> SkillResult``.
#
# The handler is responsible for:
#   1. Picking the right decision template for the item's `status`.
#   2. Constructing the decide() inputs from `task_payload`.
#   3. Calling POST /<skill>/decide.
#   4. Inspecting the response, picking the action endpoint (or
#      no-op), respecting --dry-run.
#   5. Returning a SkillResult so the daemon can log outcomes.
# ---------------------------------------------------------------------------
@dataclass
class WorkItem:
    """One row from /agents/{id}/work-queue, parsed into a dataclass."""

    work_item_id: str
    task_kind: str
    agent_id: str
    organization_id: str
    scope_ref: str
    status: str
    last_action_at: str | None
    last_decision_id: str | None
    task_payload: dict[str, Any]
    decision_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_envelope(cls, env: dict[str, Any]) -> "WorkItem":
        return cls(
            work_item_id=env["work_item_id"],
            task_kind=env["task_kind"],
            agent_id=env["agent_id"],
            organization_id=env["organization_id"],
            scope_ref=env["scope_ref"],
            status=env["status"],
            last_action_at=env.get("last_action_at"),
            last_decision_id=env.get("last_decision_id"),
            task_payload=env.get("task_payload") or {},
            decision_history=env.get("decision_history") or [],
        )


@dataclass
class SkillResult:
    """What the skill handler returns. The daemon logs these and uses
    ``ok`` for tick-level metrics."""

    ok: bool
    summary: str
    detail: dict[str, Any] | None = None


SkillHandler = Callable[
    [PowerloomClient, WorkItem, "DaemonOptions"],
    Awaitable[SkillResult],
]


_SKILLS: dict[str, SkillHandler] = {}


def register_skill(task_kind: str, handler: SkillHandler) -> None:
    """Register a skill handler. Called at module import time. Re-
    registration raises — same defense as the server-side registry."""
    if task_kind in _SKILLS:
        raise ValueError(
            f"skill for task_kind={task_kind!r} already registered"
        )
    _SKILLS[task_kind] = handler


def get_registered_task_kinds() -> set[str]:
    """For introspection / tests."""
    return set(_SKILLS.keys())


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------
@dataclass
class DaemonOptions:
    """Per-run configuration. Built from CLI flags + defaults."""

    interval_seconds: int = 10
    once: bool = False
    dry_run: bool = False
    limit: int = 10
    # Confidence threshold for taking destructive actions (rebase /
    # merge). Decisions below this confidence become log-only.
    action_confidence_threshold: float = 0.8


async def _run_loop(
    client: PowerloomClient,
    *,
    agent_id: str,
    options: DaemonOptions,
) -> None:
    """Tick forever (or once, if ``options.once``).

    On each tick:
      1. GET /agents/{agent_id}/work-queue — pull up to ``limit`` items.
      2. For each item, dispatch to the matching skill handler.
      3. Log outcome.
      4. Sleep ``interval_seconds`` (skipped on the last tick of
         ``--once``).
    """
    stop_signal = asyncio.Event()

    def _handle_signal(*_: Any) -> None:
        _console.print("\n[yellow]Stop signal received; finishing tick…[/yellow]")
        stop_signal.set()

    # Install signal handlers if we're on a platform that supports
    # add_signal_handler. Windows doesn't; fall back to default Ctrl+C
    # behavior (KeyboardInterrupt).
    loop = asyncio.get_event_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    tick_n = 0
    while not stop_signal.is_set():
        tick_n += 1
        try:
            items = await _fetch_work_queue(
                client, agent_id=agent_id, limit=options.limit
            )
        except PowerloomApiError as e:
            _console.print(
                f"[red]tick {tick_n}: queue fetch failed:[/red] {e}"
            )
            items = []

        _console.print(
            f"[cyan]tick {tick_n}:[/cyan] "
            f"{len(items)} item(s){' (dry-run)' if options.dry_run else ''}"
        )

        for item in items:
            handler = _SKILLS.get(item.task_kind)
            if handler is None:
                _console.print(
                    f"  [dim]skip[/dim] {item.scope_ref} — "
                    f"no handler for task_kind={item.task_kind!r}"
                )
                continue
            try:
                result = await handler(client, item, options)
                badge = (
                    "[green]ok[/green]" if result.ok else "[yellow]warn[/yellow]"
                )
                _console.print(
                    f"  {badge} {item.scope_ref} — {result.summary}"
                )
            except PowerloomApiError as e:
                _console.print(
                    f"  [red]err[/red] {item.scope_ref} — {e}"
                )
            except Exception as e:  # noqa: BLE001
                # Skill handlers shouldn't bubble unhandled exceptions,
                # but defense-in-depth — one broken item must not stop
                # the loop.
                log.exception(
                    "skill handler crashed item=%s",
                    item.scope_ref,
                )
                _console.print(
                    f"  [red]crash[/red] {item.scope_ref} — {type(e).__name__}: {e}"
                )

        if options.once or stop_signal.is_set():
            break

        try:
            await asyncio.wait_for(
                stop_signal.wait(),
                timeout=options.interval_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def _fetch_work_queue(
    client: PowerloomClient,
    *,
    agent_id: str,
    limit: int,
) -> list[WorkItem]:
    """Wrap the GET into a tidy WorkItem list."""
    body = client.get(f"/agents/{agent_id}/work-queue", limit=limit)
    if not isinstance(body, dict):
        return []
    raw = body.get("items") or []
    return [WorkItem.from_envelope(env) for env in raw if isinstance(env, dict)]


# ---------------------------------------------------------------------------
# pr_reconciliation skill — the v1 reference implementation
# ---------------------------------------------------------------------------
# Maps reconciler_pr_state.status to the decision template the daemon
# should call. Other statuses (terminal: merged, closed_unmerged,
# failed) are filtered out by the server-side projector — daemons
# shouldn't see them. ``awaiting_approval`` is the operator's turn:
# the daemon skips until human approval lands and the row's status
# transitions away.
_PR_RECON_STATUS_TO_TEMPLATE = {
    "watching": "safe_to_rebase_v1",
    "needs_rebase": "safe_to_rebase_v1",
}


async def _pr_reconciliation_handler(
    client: PowerloomClient,
    item: WorkItem,
    options: DaemonOptions,
) -> SkillResult:
    """Decide + (optionally) act on one ``pr_reconciliation`` item.

    Decision flow:
      1. Pick template by status. ``awaiting_approval`` → no-op (skip).
      2. POST /reconciler/decide with template + inputs assembled from
         ``task_payload``.
      3. Inspect the response:
           * decision == "rebase" + confidence ≥ threshold → POST
             /reconciler/rebase (unless --dry-run).
           * decision == "merge_safe" + approval_request_id present
             on the row → POST /reconciler/merge (gated server-side).
           * everything else → log-only.

    The action endpoints update ``reconciler_pr_state.status`` server-
    side; the daemon's next tick sees the new status and continues from
    there. No local persistence needed.
    """
    template = _PR_RECON_STATUS_TO_TEMPLATE.get(item.status)
    if template is None:
        return SkillResult(
            ok=True,
            summary=f"status={item.status} — no template, skipped",
        )

    # Build decide() inputs from the task_payload. The server-side
    # projector populated these for us.
    inputs = {
        "repo_full_name": item.task_payload.get("repo_full_name"),
        "pr_number": item.task_payload.get("pr_number"),
        "conflict_summary": item.task_payload.get("conflict_summary"),
    }

    decision_body = client.post(
        "/reconciler/decide",
        body={"template_name": template, "inputs": inputs},
    )
    decision = decision_body.get("response") or {}
    confidence = decision_body.get("confidence")
    cache_hit = decision_body.get("cache_hit", False)
    decision_kind = decision.get("decision")

    # Log-only path: low confidence or unrecognized decision.
    if confidence is None or confidence < options.action_confidence_threshold:
        return SkillResult(
            ok=True,
            summary=(
                f"decided={decision_kind} conf={confidence} "
                f"(below threshold; log-only)"
            ),
            detail={"decision": decision, "cache_hit": cache_hit},
        )

    if options.dry_run:
        return SkillResult(
            ok=True,
            summary=f"decided={decision_kind} conf={confidence} (dry-run, no action)",
            detail={"decision": decision, "cache_hit": cache_hit},
        )

    # Action dispatch.
    if decision_kind == "rebase":
        try:
            res = client.post(
                "/reconciler/rebase",
                body={
                    "repo_full_name": item.task_payload["repo_full_name"],
                    "pr_number": item.task_payload["pr_number"],
                },
            )
        except PowerloomApiError as e:
            return SkillResult(
                ok=False,
                summary=f"rebase failed: HTTP {e.status_code}",
                detail={"error": str(e)},
            )
        return SkillResult(
            ok=bool(res.get("ok", True)),
            summary=f"rebased → {res.get('new_status', '?')}",
            detail=res,
        )

    if decision_kind == "merge_safe":
        # Merge requires an approval_request_id. If the row carries one,
        # the operator already approved; pass it through. Otherwise
        # log-only — the operator's approval flow hasn't fired yet.
        approval_id = item.task_payload.get("approval_request_id")
        if not approval_id:
            return SkillResult(
                ok=True,
                summary="decision=merge_safe; awaiting operator approval",
                detail={"decision": decision},
            )
        try:
            res = client.post(
                "/reconciler/merge",
                body={
                    "repo_full_name": item.task_payload["repo_full_name"],
                    "pr_number": item.task_payload["pr_number"],
                    "approval_request_id": approval_id,
                },
            )
        except PowerloomApiError as e:
            return SkillResult(
                ok=False,
                summary=f"merge failed: HTTP {e.status_code}",
                detail={"error": str(e)},
            )
        return SkillResult(
            ok=bool(res.get("ok", True)),
            summary=f"merged → {res.get('new_status', '?')}",
            detail=res,
        )

    return SkillResult(
        ok=True,
        summary=f"decided={decision_kind} (no action mapped)",
        detail={"decision": decision},
    )


register_skill("pr_reconciliation", _pr_reconciliation_handler)


# ---------------------------------------------------------------------------
# Typer command — wired into agent_cmd.app from cli.py / agent_cmd.py
# ---------------------------------------------------------------------------
def run_command(
    agent: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Agent UUID, /ou/path/agent-name, or unique name. "
                "Same resolution as `weave agent status`."
            )
        ),
    ] = None,
    ou: Annotated[
        Optional[str],
        typer.Option("--ou", help="OU path used when AGENT is a bare name."),
    ] = None,
    interval: Annotated[
        int,
        typer.Option(
            "--interval",
            min=2,
            max=3600,
            help="Seconds between ticks. Default 10.",
        ),
    ] = 10,
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help=(
                "Process the queue once and exit. Useful for cron + tests."
            ),
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Decide but don't take destructive actions. Cache-warm, "
                "audit, learn the inputs."
            ),
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            min=1,
            max=100,
            help="Max work items to fetch per tick. Default 10.",
        ),
    ] = 10,
    confidence: Annotated[
        float,
        typer.Option(
            "--confidence",
            min=0.0,
            max=1.0,
            help=(
                "Confidence threshold for destructive actions. Decisions "
                "below this are log-only. Default 0.8."
            ),
        ),
    ] = 0.8,
) -> None:
    """Run the self-hosted-agent daemon for AGENT.

    Foreground only — Ctrl+C stops cleanly. For background runs, use
    your OS supervisor of choice (systemd, supervisord, NSSM on
    Windows, or ``nohup … &``).

    The daemon:

      1. Authenticates as your operator account via ``weave login``.
      2. Resolves AGENT (must be ``runtime_type='self_hosted'``).
      3. Polls ``GET /agents/{id}/work-queue`` every ``--interval``
         seconds.
      4. Dispatches each work item to the matching skill handler.

    The reconciler agent is the v1 reference. Future self-hosted
    agents register skills via the import-time skill registry; no
    changes to this command needed to add new task_kinds.
    """
    # Late imports — avoids circular import with agent_cmd's helpers.
    from loomcli.commands.agent_cmd import (
        AgentResolutionError,
        _require_config,
        _resolve_agent,
    )

    cfg: RuntimeConfig = _require_config()
    client = PowerloomClient(cfg)
    try:
        # Resolve the agent identity first so we can fail-fast before
        # spinning up the loop.
        if not agent:
            _console.print(
                "[red]AGENT argument is required.[/red]"
            )
            raise typer.Exit(1)
        target = _resolve_agent(client, agent, ou=ou)

        runtime_type = (target.row or {}).get("runtime_type")
        if runtime_type != "self_hosted":
            _console.print(
                f"[red]agent {target.label!r} has runtime_type="
                f"{runtime_type!r}; this command only runs "
                f"runtime_type='self_hosted' agents.[/red]"
            )
            raise typer.Exit(2)

        # Sanity: agent must advertise at least one task_kind we have a
        # handler for. Otherwise the daemon would spin doing nothing.
        agent_kinds = set((target.row or {}).get("task_kinds") or [])
        registered = get_registered_task_kinds()
        intersection = agent_kinds & registered
        if not intersection:
            _console.print(
                f"[yellow]warning:[/yellow] agent advertises "
                f"task_kinds={sorted(agent_kinds)} but no daemon-side "
                f"handler is registered for any of them. The daemon "
                f"will run but skip every work item. Registered: "
                f"{sorted(registered)}."
            )

        options = DaemonOptions(
            interval_seconds=interval,
            once=once,
            dry_run=dry_run,
            limit=limit,
            action_confidence_threshold=confidence,
        )

        _console.print(
            f"[bold]agent[/bold] {target.label} "
            f"([dim]{target.id}[/dim])\n"
            f"[bold]task_kinds[/bold] active: "
            f"{sorted(intersection)}\n"
            f"[bold]interval[/bold] {options.interval_seconds}s "
            f"[bold]limit[/bold] {options.limit} "
            f"[bold]dry-run[/bold] {options.dry_run} "
            f"[bold]once[/bold] {options.once}\n"
            f"[dim]Ctrl+C to stop.[/dim]"
        )

        try:
            asyncio.run(
                _run_loop(client, agent_id=target.id, options=options)
            )
        except KeyboardInterrupt:
            _console.print("\n[yellow]interrupted; exiting.[/yellow]")

    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()
