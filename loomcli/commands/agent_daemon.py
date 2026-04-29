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

Auth modes (v0.7.12+):

    1. **Deployment-bound credentials** (preferred). When
       ``/etc/powerloom/deployment.json`` is present (or its per-user
       fallback), the daemon reads it and:
         * Uses the host-bound ``deployment_token`` (``dep-...``) for
           auth — separate identity from the operator's PAT.
         * Resolves agent_id + api_base_url + initial runtime_config
           from the credential — no CLI args needed.
         * Per tick: GETs runtime_config (with If-None-Match etag) so
           operator-side patches via the UI propagate to the daemon
           on the next tick. POSTs heartbeat so the UI can show online/
           degraded/offline status.
         * If a heartbeat returns 401, the deployment was archived
           server-side — daemon exits cleanly with code 3.

    2. **PAT** (legacy / dev). When no deployment credential exists,
       the daemon falls back to ``POWERLOOM_ACCESS_TOKEN`` /
       ``weave login`` PATs. CLI args provide agent + tuning.

Surface:
    weave register --token=pat-deploy-...       pair this host
    weave agent run                             daemon, deployment mode
    weave agent run <agent>                     daemon, PAT mode
    weave agent run <agent> --once              single tick + exit
    weave agent run <agent> --dry-run           decide but don't act
    weave agent run <agent> --interval 30       poll cadence in seconds

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
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Optional

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
    """Per-run configuration. Built from CLI flags + defaults.

    In deployment-bound mode (v0.7.12+), the runtime fields
    (interval / dry_run / confidence / model) are sourced from the
    server-side ``runtime_config`` and refreshed on every tick.
    CLI flags become defaults that the operator-pushed config
    overrides as soon as the first tick completes.
    """

    interval_seconds: int = 10
    once: bool = False
    dry_run: bool = False
    limit: int = 10
    # Confidence threshold for taking destructive actions (rebase /
    # merge). Decisions below this confidence become log-only.
    action_confidence_threshold: float = 0.8
    # Optional override model (v0.7.12). When the runtime_config
    # carries `model`, skills that respect it use this value over the
    # agent's default. None = use the skill / agent default.
    model: str | None = None


@dataclass
class DeploymentBinding:
    """State carried for a deployment-bound daemon run.

    Built from the credential file at startup; mutated each tick as
    the daemon learns about runtime_config changes (etag advances)
    and exits cleanly when the deployment_token is revoked (heartbeat
    401).
    """

    deployment_id: str
    deployment_token: str
    agent_id: str
    agent_slug: str | None
    api_base_url: str
    runtime_config: dict[str, Any] = field(default_factory=dict)
    runtime_config_etag: str | None = None
    revoked: bool = False


async def _run_loop(
    client: PowerloomClient,
    *,
    agent_id: str,
    options: DaemonOptions,
    binding: DeploymentBinding | None = None,
) -> int:
    """Tick forever (or once, if ``options.once``).

    On each tick (deployment mode):
      1. GET /deployments/{id}/runtime-config (with If-None-Match) —
         304 cheap, 200 → refresh local config + apply.
      2. GET /agents/{agent_id}/work-queue — pull up to ``limit`` items.
      3. For each item, dispatch to the matching skill handler.
      4. POST /deployments/{id}/heartbeat — confirm we're alive.
         401 → deployment was archived; exit code 3.
      5. Sleep ``interval_seconds`` (skipped on the last tick of
         ``--once``).

    Returns the daemon's exit code: 0 normal, 3 if a deployment-token
    revocation forced an exit.
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

    exit_code = 0
    tick_n = 0
    while not stop_signal.is_set():
        tick_n += 1

        # 1. Refresh runtime_config from the server (deployment mode only).
        if binding is not None:
            _refresh_runtime_config(client, binding=binding, options=options)

        # 2. Pull work.
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

        # 3. Dispatch.
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

        # 4. Heartbeat (deployment mode only).
        if binding is not None:
            revoked = _heartbeat(client, binding=binding)
            if revoked:
                _console.print(
                    "[red]Deployment token revoked server-side. "
                    "The deployment was archived; exiting cleanly.[/red]\n"
                    "  Re-pair this host with [bold]weave register --token=...[/bold] "
                    "after minting a fresh registration token in the UI."
                )
                exit_code = 3
                break

        if options.once or stop_signal.is_set():
            break

        try:
            await asyncio.wait_for(
                stop_signal.wait(),
                timeout=options.interval_seconds,
            )
        except asyncio.TimeoutError:
            pass

    return exit_code


def _refresh_runtime_config(
    client: PowerloomClient,
    *,
    binding: DeploymentBinding,
    options: DaemonOptions,
) -> None:
    """Long-poll-style GET /deployments/{id}/runtime-config.

    On 200: replace ``binding.runtime_config`` + ``binding.runtime_config_etag``,
    apply scalar fields to ``options`` so the next tick's behavior reflects
    the operator-pushed change.

    On 304: nothing to do (ETag matched).

    On any other failure: log + continue with the current cached config.
    Operator-side patches will land on a later tick when the network
    cooperates.
    """
    headers: dict[str, str] = {}
    if binding.runtime_config_etag:
        headers["If-None-Match"] = binding.runtime_config_etag

    path = f"/deployments/{binding.deployment_id}/runtime-config"
    try:
        # PowerloomClient doesn't expose raw response objects; we do
        # a low-level call so we can read the ETag header + 304 status.
        response = client._http.request("GET", path, headers=headers)  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        _console.print(f"  [dim]config refresh failed: {e}[/dim]")
        return

    if response.status_code == 304:
        return  # No change; current config is still right.

    if response.status_code == 401:
        # Token revoked or server lost track — let the heartbeat path
        # handle the actual exit so we don't double-message.
        binding.revoked = True
        return

    if response.status_code != 200:
        _console.print(
            f"  [yellow]config refresh: HTTP {response.status_code}[/yellow]"
        )
        return

    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return

    # Body shape per /deployments/{id}/runtime-config:
    #   {"runtime_config": {...}, "config_etag": "..."}
    new_config = body.get("runtime_config") or {}
    if not isinstance(new_config, dict):
        return

    binding.runtime_config = new_config
    # Prefer the body's config_etag, fall back to the ETag response header.
    binding.runtime_config_etag = (
        body.get("config_etag") or response.headers.get("ETag") or binding.runtime_config_etag
    )

    # Apply to live options. Only fields the daemon respects today.
    interval = new_config.get("interval_seconds")
    if isinstance(interval, int) and 2 <= interval <= 3600:
        options.interval_seconds = interval

    confidence = new_config.get("confidence_threshold")
    if isinstance(confidence, (int, float)) and 0.0 <= confidence <= 1.0:
        options.action_confidence_threshold = float(confidence)

    dry_run = new_config.get("dry_run")
    if isinstance(dry_run, bool):
        options.dry_run = dry_run

    model = new_config.get("model")
    if model is None or isinstance(model, str):
        options.model = model


def _heartbeat(
    client: PowerloomClient,
    *,
    binding: DeploymentBinding,
) -> bool:
    """POST /deployments/{id}/heartbeat. Returns True iff the
    deployment_token was rejected (401 → server archived this
    deployment; daemon should exit cleanly).
    """
    path = f"/deployments/{binding.deployment_id}/heartbeat"
    try:
        response = client._http.request("POST", path, json={})  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        # Network blip — log + carry on. The platform's freshness
        # rules tolerate up to 3× interval before flipping to
        # 'degraded'; we only report a hard exit on a definitive 401.
        _console.print(f"  [dim]heartbeat failed: {e}[/dim]")
        return False

    if response.status_code == 401:
        return True

    if response.status_code >= 400:
        _console.print(
            f"  [yellow]heartbeat: HTTP {response.status_code}[/yellow]"
        )
    return False


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
    from loomcli import config as cfg_mod
    from loomcli.commands.agent_cmd import (
        AgentResolutionError,
        _require_config,
        _resolve_agent,
    )

    # v0.7.12 — deployment-bound credential takes precedence. When
    # /etc/powerloom/deployment.json (or the per-user fallback) is
    # present, use the deployment_token + the credential's agent_id
    # rather than asking the operator to pass --agent + having the
    # daemon authenticate as their own user.
    deployment_payload = cfg_mod.read_deployment_credential()
    binding: DeploymentBinding | None = None
    cfg: RuntimeConfig

    if deployment_payload and not agent:
        # Deployment-bound mode — the credential is the source of truth.
        try:
            binding = _binding_from_credential(deployment_payload)
        except ValueError as e:
            _console.print(
                f"[red]Deployment credential at "
                f"{cfg_mod.deployment_credential_path()} is malformed:[/red] {e}\n"
                f"Re-pair this host with [bold]weave register --token=... --force[/bold] "
                f"after minting a fresh registration token."
            )
            raise typer.Exit(1) from e

        cfg = RuntimeConfig(
            api_base_url=binding.api_base_url,
            access_token=binding.deployment_token,
            request_timeout_seconds=30.0,
        )
    else:
        # Legacy / dev path — operator's PAT + explicit --agent.
        cfg = _require_config()

    client = PowerloomClient(cfg)
    try:
        if binding is not None:
            # Deployment mode — agent_id comes from the credential.
            agent_id = binding.agent_id
            agent_label = binding.agent_slug or binding.agent_id[:8]
            # Don't try to resolve via /agents — the deployment token
            # might not have agent:read on the agent's OU (the platform
            # gates work-queue + reconciler endpoints by deployment
            # ownership, not by classical RBAC).
            options = DaemonOptions(
                # Defaults: cold start uses the credential's runtime_config
                # if present, falls back to CLI flags, then library default.
                interval_seconds=_int_from_config(
                    binding.runtime_config, "interval_seconds", interval
                ),
                once=once,
                dry_run=_bool_from_config(
                    binding.runtime_config, "dry_run", dry_run
                ),
                limit=limit,
                action_confidence_threshold=_float_from_config(
                    binding.runtime_config, "confidence_threshold", confidence
                ),
                model=binding.runtime_config.get("model")
                if isinstance(binding.runtime_config.get("model"), str)
                else None,
            )

            _console.print(
                f"[bold]deployment[/bold] {binding.deployment_id} "
                f"([dim]{cfg.api_base_url}[/dim])\n"
                f"[bold]agent[/bold] {agent_label} ([dim]{agent_id}[/dim])\n"
                f"[bold]interval[/bold] {options.interval_seconds}s "
                f"[bold]limit[/bold] {options.limit} "
                f"[bold]dry-run[/bold] {options.dry_run} "
                f"[bold]once[/bold] {options.once}\n"
                f"[dim]Runtime config refreshes from server every tick. "
                f"Ctrl+C to stop.[/dim]"
            )
        else:
            # Legacy PAT mode — resolve agent + sanity-check task_kinds.
            if not agent:
                _console.print(
                    "[red]AGENT argument is required.[/red]\n"
                    "Either pass an agent slug (PAT mode) or pair this "
                    "host first with [bold]weave register --token=...[/bold] "
                    "(deployment mode)."
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

            agent_id = target.id
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
            exit_code = asyncio.run(
                _run_loop(
                    client,
                    agent_id=agent_id,
                    options=options,
                    binding=binding,
                )
            )
        except KeyboardInterrupt:
            _console.print("\n[yellow]interrupted; exiting.[/yellow]")
            exit_code = 0

        if exit_code:
            raise typer.Exit(exit_code)

    except (AgentResolutionError, PowerloomApiError) as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Deployment-credential helpers
# ---------------------------------------------------------------------------
def _binding_from_credential(payload: dict[str, Any]) -> DeploymentBinding:
    """Validate + project a credential dict into a DeploymentBinding.

    The shape mirrors what ``weave register`` writes:
        {deployment_id, agent_id, agent_slug, deployment_token,
         api_base_url, runtime_config}
    """
    required = ("deployment_id", "agent_id", "deployment_token", "api_base_url")
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValueError(f"missing fields {missing!r}")
    return DeploymentBinding(
        deployment_id=str(payload["deployment_id"]),
        deployment_token=str(payload["deployment_token"]),
        agent_id=str(payload["agent_id"]),
        agent_slug=(
            str(payload["agent_slug"])
            if payload.get("agent_slug")
            else None
        ),
        api_base_url=str(payload["api_base_url"]).rstrip("/"),
        runtime_config=(
            payload.get("runtime_config")
            if isinstance(payload.get("runtime_config"), dict)
            else {}
        ),
    )


def _int_from_config(d: dict[str, Any], key: str, default: int) -> int:
    v = d.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _bool_from_config(d: dict[str, Any], key: str, default: bool) -> bool:
    v = d.get(key)
    return v if isinstance(v, bool) else default


def _float_from_config(d: dict[str, Any], key: str, default: float) -> float:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
