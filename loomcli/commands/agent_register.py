"""``weave register`` — pair this host with an agent deployment.

Agent Lifecycle UX P3 (loomcli side; platform side is Powerloom PR
#248). Operator workflow:

  1. UI: ``/agents/<id>`` → Deployments tab → Add deployment → mint a
     registration token (``pat-deploy-...``). Token shown ONCE.
  2. Operator copies the ``weave register --token=...`` command from
     the UI modal, SSHes to the operator host, runs it.
  3. ``weave register`` POSTs to ``/deployments/register``, gets back
     a long-lived deployment_token (``dep-...``) + agent_id + initial
     runtime_config, writes it all to ``/etc/powerloom/deployment.json``.
  4. ``weave agent run`` (no args) reads the credential, drives the
     right agent against the right control plane, heartbeats, and
     long-polls runtime_config for operator-side updates.

Pre-P3 the operator workflow was: mint a PAT, edit a .env file, drop
it on the box, edit a docker-compose, restart systemd, hope. The 7
papercuts caught during the 2026-04-29 EC2 reconciler bring-up are
why this command exists.

The registration token is one-shot and 24h-expiry by default
(server-controlled). If the operator drops it on a box that fails
to register before 24h elapses, they mint a fresh token from the UI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import httpx
import typer
from rich.console import Console

from loomcli import config

app = typer.Typer(
    help=(
        "Register this host as an agent deployment. Trades a "
        "registration token (pat-deploy-...) for a long-lived, "
        "host-bound deployment credential."
    ),
    invoke_without_command=True,
)
_console = Console()


_DEFAULT_API_BASE_URL = "https://api.powerloom.org"


@app.callback(invoke_without_command=True)
def register(
    token: Annotated[
        str,
        typer.Option(
            "--token",
            help=(
                "Registration token from the UI (pat-deploy-...). One-shot; "
                "expires 24h after mint."
            ),
            prompt=False,
        ),
    ],
    api_url: Annotated[
        Optional[str],
        typer.Option(
            "--api-url",
            help=(
                "Override the control-plane base URL. Falls back to the "
                "POWERLOOM_API_BASE_URL env var or "
                "https://api.powerloom.org. Set this when registering "
                "against a self-hosted control plane."
            ),
        ),
    ] = None,
    output: Annotated[
        Optional[str],
        typer.Option(
            "--output",
            "-o",
            help=(
                "Override credential output path. Defaults to "
                "/etc/powerloom/deployment.json on Linux when /etc is "
                "writable, otherwise the per-user XDG config dir."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Overwrite an existing deployment credential. Without "
                "--force, register refuses to clobber a credential file "
                "in case the operator typed the wrong host."
            ),
        ),
    ] = False,
) -> None:
    """Register this host as an agent deployment.

    Mints a long-lived deployment credential bound to this host. The
    daemon (``weave agent run``) reads the credential to drive the
    right agent against the right control plane.
    """
    base = (
        api_url
        or _env_api_base_url()
        or _DEFAULT_API_BASE_URL
    ).rstrip("/")

    # The final credential path depends on the server's
    # ``credential_scope`` + ``credential_kind`` response (M2-P1).
    # We can't know pre-call whether this'll be a host/default
    # registration (would clobber an existing reconciler) or a
    # user/<kind> registration (independent path).
    #
    # Tradeoff: we make the server call first, then refuse-to-clobber
    # against the SPECIFIC resolved path. This means a refused
    # registration burns one registration token (the server marks it
    # redeemed even though we didn't write the credential). The
    # operator can archive the orphaned deployment via the UI. This is
    # acceptable because:
    #   * False positives in the pre-call check were worse — they
    #     blocked legitimate IDE registrations on hosts with a
    #     reconciler credential.
    #   * Tokens are cheap; deployments are not. The post-call check
    #     gives precise per-path safety.
    #
    # ``--output`` still gets a pre-call check for the explicit path
    # since we know it ahead of time.
    cred_path: Path | None = None
    if output:
        cred_path = _Path(output)
        if cred_path.exists() and not force:
            _refuse_clobber(cred_path)

    # POST /deployments/register. The token is the auth — no bearer
    # header needed for this route.
    payload = {"registration_token": token}
    try:
        with httpx.Client(base_url=base, timeout=30.0) as client:
            response = client.post("/deployments/register", json=payload)
    except httpx.HTTPError as e:
        _console.print(
            f"[red]Network error reaching {base}:[/red] {e}\n"
            f"Check the API URL is reachable from this host and that "
            f"DNS resolves."
        )
        raise typer.Exit(1) from e

    if response.status_code in (401, 404):
        _console.print(
            "[red]Registration token invalid, expired, or already "
            "redeemed.[/red]\n"
            "Mint a fresh token from the UI: "
            "[bold]/agents/<id>[/bold] → Deployments tab → "
            "[bold]Add deployment[/bold]."
        )
        raise typer.Exit(1)
    if response.status_code != 200:
        _console.print(
            f"[red]Unexpected response from {base}/deployments/register:[/red] "
            f"HTTP {response.status_code}\n"
            f"  {_safe_text(response)[:300]}"
        )
        raise typer.Exit(1)

    try:
        body = response.json()
    except ValueError as e:
        _console.print(
            f"[red]Server returned non-JSON for /deployments/register:[/red] {e}\n"
            f"  {_safe_text(response)[:300]}"
        )
        raise typer.Exit(1) from e

    # Server returns a flat envelope per powerloom_api/schemas/agent_deployment.py:
    # {deployment_id, agent_id, agent_slug, deployment_token, runtime_config, ...}
    # See routes/agent_deployments.py::register_endpoint.
    required_fields = ("deployment_id", "agent_id", "deployment_token")
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        _console.print(
            f"[red]Server response missing required fields:[/red] "
            f"{', '.join(missing)}\n"
            f"  Got: {sorted(body.keys()) if isinstance(body, dict) else type(body).__name__}"
        )
        raise typer.Exit(1)

    credential = {
        "deployment_id": body["deployment_id"],
        "agent_id": body["agent_id"],
        "agent_slug": body.get("agent_slug"),
        "deployment_token": body["deployment_token"],
        "api_base_url": base,
        "runtime_config": body.get("runtime_config") or {},
        # M2-P1 server fields. The plugin auto-register flow (M2-P3)
        # reads `credential_kind` to identify "this is the Claude
        # Code credential" vs Codex etc.
        "credential_scope": body.get("credential_scope") or "host",
        "credential_kind": body.get("credential_kind") or "default",
    }

    # Resolve scope + kind from the server response (M2-P1). Legacy
    # servers without these fields default to host/default which is
    # the M1 reconciler shape — no behavior change for existing flows.
    scope = body.get("credential_scope") or "host"
    kind = body.get("credential_kind") or "default"
    if scope not in ("host", "user"):
        # Server bug or malformed response. Treat unknown scope as
        # 'host' to match v0.7.12 behavior.
        scope = "host"

    # Now resolve the actual write path (unless --output was passed).
    if not output:
        cred_path = config.deployment_credential_path(scope=scope, kind=kind)
        if cred_path.exists() and not force:
            _refuse_clobber(cred_path)

    try:
        if output:
            # User-specified path — write directly without going
            # through config.write_deployment_credential which uses
            # the resolved scope/kind path.
            assert cred_path is not None
            cred_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            cred_path.write_text(_json.dumps(credential, indent=2), encoding="utf-8")
            try:
                import os as _os
                _os.chmod(cred_path, 0o600)
            except OSError:
                pass
        else:
            cred_path = config.write_deployment_credential(
                credential, scope=scope, kind=kind
            )
    except OSError as e:
        location_hint = (
            "On Linux you usually need [bold]sudo weave register --token=...[/bold] "
            "so the credential lands at /etc/powerloom/deployment.json."
            if scope == "host"
            else "Check that your home directory is writable."
        )
        _console.print(
            f"[red]Failed to write {cred_path}:[/red] {e}\n{location_hint}"
        )
        raise typer.Exit(1) from e

    deployment_id_short = body["deployment_id"].split("-")[0]
    agent_label = body.get("agent_slug") or body["agent_id"][:8]
    next_step = (
        "Start the daemon: [bold]weave agent run[/bold]"
        if scope == "host"
        else (
            "Open your IDE in any working tree — the plugin will "
            "auto-register sessions against this credential."
        )
    )

    _console.print(
        f"[green]✓[/green] Registered as deployment "
        f"[cyan]{deployment_id_short}[/cyan]\n"
        f"  Agent:      [cyan]{agent_label}[/cyan]\n"
        f"  API:        [dim]{base}[/dim]\n"
        f"  Scope:      [dim]{scope}/{kind}[/dim]\n"
        f"  Credential: [dim]{cred_path}[/dim]\n"
        f"\n"
        f"{next_step}"
    )


# ---------------------------------------------------------------------------
# Helpers (small + cheap; module-level for testability)
# ---------------------------------------------------------------------------
def _env_api_base_url() -> str | None:
    """Pull POWERLOOM_API_BASE_URL from the environment if set."""
    import os

    raw = os.environ.get("POWERLOOM_API_BASE_URL")
    return raw.strip() if raw and raw.strip() else None


def _safe_text(response: httpx.Response) -> str:
    """Get response.text without crashing on encoding mishaps."""
    try:
        return response.text
    except Exception:  # noqa: BLE001
        return f"<{len(response.content)} bytes of response body>"


def _refuse_clobber(cred_path: Path) -> None:
    """Print the actionable refuse-to-clobber message and exit.

    Used by both the --output pre-check (before the register call)
    and the post-server-response path resolution. Same message body
    in both cases — the shape of the warning is identical regardless
    of how we discovered the existing credential.
    """
    existing = config.read_deployment_credential() or {}
    existing_dep_id = existing.get("deployment_id", "?")
    existing_agent = existing.get("agent_slug", "?")
    _console.print(
        f"[red]A deployment credential already exists at {cred_path}.[/red]\n"
        f"  Existing deployment: [cyan]{existing_dep_id}[/cyan] "
        f"(agent [cyan]{existing_agent}[/cyan])\n"
        f"\n"
        f"Re-running [bold]weave register[/bold] would orphan that "
        f"deployment server-side (its token would still be valid, but "
        f"this host would point at a different one).\n"
        f"\n"
        f"  • To pair this host with the new token, archive the "
        f"existing deployment in the UI first, then re-run with "
        f"[bold]--force[/bold].\n"
        f"  • To leave the existing pairing alone, drop this token "
        f"and mint a fresh one for the host you actually meant."
    )
    raise typer.Exit(1)


# Lazy alias retained for tests that monkeypatch this. Plain Path call.
def _Path(s: str) -> Path:
    return Path(s)
