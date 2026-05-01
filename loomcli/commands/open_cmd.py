"""``weave open <token>`` — bootstrap an agent session from a launch token.

The CLI half of the Weave Open Launch UX flow. A user clicks "Open in
agent" in the Powerloom web UI, gets a ``weave open lt_…`` command,
pastes it on any machine. After this command finishes the user is
sitting in a fully-contextualised agent session — repo cloned, skills
installed, MCP server pre-authed, agent-session registered — without
ever typing ``weave login`` or filling out flags.

Sprint: ``cli-weave-open-20260430`` (loomcli) under milestone
"Weave Open Launch UX 20260430" (loomcli/8567b658).

This thread (``c78ead6d``) covers the skeleton: parse args, resolve
API base, redeem the token, print the preview, exit. Subsequent
threads in the sprint layer worktree prep + skill install + MCP wiring
+ session register + exec runtime on top:

  * ``864c55a4`` — bare-clone + git worktree add
  * ``5fab82ed`` — register session + write .powerloom-session.env
  * ``53573d73`` — exec runtime handoff
  * ``5790b2d6`` — flags polish (--reuse / --resume / worktree-root)
  * ``53fddf29`` — apply rules_sync directives

For now the future-thread flags (``--reuse``, ``--resume``, ``--root``)
are accepted but only logged as "TODO" alongside the preview. ``--dry-run``
is fully wired (redeem, print, exit).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Annotated, Optional

import typer
from rich.console import Console

from loomcli._open.git_ops import (
    CloneAuthError,
    GitOpError,
    WeaveOpenPaths,
    WorktreePathInvalidError,
    assert_git_available,
    create_worktree,
    ensure_bare_clone,
    path_length_warning,
    short_id_from_launch_id,
)
from loomcli._open.runtime_exec import (
    ANTIGRAVITY_RUNTIME,
    RuntimeBinaryError,
    assert_runtime_available,
    exec_runtime,
)
from loomcli._open.session_reg import (
    SessionRegisterError,
    ensure_gitignore_entry,
    register_agent_session,
    write_session_env_file,
)
from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import is_json_output, load_runtime_config
from loomcli.schema.launch_spec import LaunchSpec


_console = Console()
_err = Console(stderr=True)


_LAUNCHES_HELP_URL = "https://app.powerloom.org/launches/recent"


def _print_preview(spec: LaunchSpec) -> None:
    """Render the spec for human eyes. Mirrors the modal's preview text."""
    skills_blurb = (
        ", ".join(s.slug for s in spec.skills) if spec.skills else "(none)"
    )
    caps_blurb = (
        ", ".join(spec.capabilities) if spec.capabilities else "(none)"
    )
    rules_lines = []
    for d in spec.rules_sync:
        rules_lines.append(
            f"    scope `{d.scope}` → "
            + ", ".join(d.runtimes)
        )
    rules_section = (
        "\n".join(rules_lines) if rules_lines else "    (no rules sync configured)"
    )
    body = (
        f"[bold]Launch[/bold]  {spec.launch_id}\n"
        f"  project    [cyan]{spec.project.slug}[/cyan]  "
        f"({spec.project.repo_url})\n"
        f"  scope      [cyan]{spec.scope.slug}[/cyan]  "
        f"on branch [cyan]{spec.scope.branch_name}[/cyan] "
        f"(base: {spec.scope.branch_base})\n"
        f"  runtime    {spec.runtime}\n"
        f"  skills     {skills_blurb}\n"
        f"  caps       {caps_blurb}\n"
        f"  clone_auth mode={spec.clone_auth.mode}\n"
        f"  rules_sync\n{rules_section}\n"
        f"  expires    {spec.expires_at.isoformat()}"
    )
    _console.print(body)


def _emit_error(message: str, *, hint: Optional[str] = None) -> None:
    """Pretty-print an error to stderr in the standard CLI shape."""
    _err.print(f"[red]error:[/red] {message}")
    if hint:
        _err.print(f"  [dim]hint:[/dim] {hint}")


def run(
    token: Annotated[
        str,
        typer.Argument(
            help=(
                "Launch token from the web UI's 'Open in agent' modal "
                "(format: lt_…). Single-use by default; expires 15min "
                "after mint."
            ),
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help=(
                "Redeem the token and print the launch spec, but do not "
                "create a worktree, install skills, or register a "
                "session. Useful for inspecting what the token will do "
                "before committing to it."
            ),
        ),
    ] = False,
    reuse: Annotated[
        Optional[str],
        typer.Option(
            "--reuse",
            help=(
                "Reuse the most recent worktree for this scope instead of "
                "creating a new one. Wired in thread 5790b2d6 — accepted "
                "but no-op in this thread."
            ),
        ),
    ] = None,
    resume: Annotated[
        Optional[str],
        typer.Option(
            "--resume",
            help=(
                "Resume a previously-launched session by id; skips clone "
                "and register, just exec's the runtime in the existing "
                "worktree. Wired in thread 5790b2d6 — accepted but no-op "
                "in this thread."
            ),
        ),
    ] = None,
    root: Annotated[
        Optional[str],
        typer.Option(
            "--root",
            help=(
                "Override the default worktree root "
                "(~/.powerloom/worktrees/). Wired in thread 5790b2d6 — "
                "accepted but no-op in this thread."
            ),
        ),
    ] = None,
) -> None:
    """Redeem a launch token and (eventually) hand off to the runtime."""

    # ---- redeem ------------------------------------------------------------
    cfg = load_runtime_config()
    client = PowerloomClient(cfg)
    try:
        spec_json = client.get(f"/launches/{token}")
    except PowerloomApiError as exc:
        if exc.status_code == 404:
            _emit_error(
                "Launch token not found or expired.",
                hint=(
                    "Tokens are 15min single-use by default. "
                    f"Mint a fresh one at {_LAUNCHES_HELP_URL}."
                ),
            )
            raise typer.Exit(1) from None
        if exc.status_code == 410:
            _emit_error(
                "Launch token already redeemed (single-use).",
                hint=(
                    "Within 5min of first redeem, retrying returns the "
                    "same response (resume-on-interrupt). Past that, the "
                    "token is consumed — mint a fresh one at "
                    f"{_LAUNCHES_HELP_URL}."
                ),
            )
            raise typer.Exit(1) from None
        if exc.status_code == 401:
            _emit_error(
                "Not authorised to redeem this token.",
                hint=(
                    "Tokens are bound to the user who minted them. "
                    "Sign in as that user, or mint a new token from "
                    f"{_LAUNCHES_HELP_URL}."
                ),
            )
            raise typer.Exit(1) from None
        _emit_error(f"Redeem failed: {exc}")
        raise typer.Exit(1) from None

    try:
        spec = LaunchSpec.model_validate(spec_json)
    except Exception as exc:  # pragma: no cover — defensive against schema drift
        _emit_error(
            f"Launch spec failed validation: {exc}",
            hint=(
                "Engine returned a shape this loomcli version doesn't "
                "understand. Update with `pip install -U loomcli` or "
                "report at the loomcli tracker."
            ),
        )
        raise typer.Exit(1) from None

    # ---- output ------------------------------------------------------------
    if is_json_output():
        # JSON consumers get the raw spec — preserves any forward-compat
        # fields this loomcli version chose to ignore.
        json.dump(spec_json, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_preview(spec)

    if dry_run:
        if not is_json_output():
            _console.print("\n[dim]--dry-run: redeem succeeded; not creating worktree.[/dim]")
        raise typer.Exit(0)

    # ---- bootstrap (pre-flights + clone + worktree) -----------------------
    # Pre-flights run before clone so users learn about missing tooling
    # in seconds, not 30 seconds into a slow clone. Sprint
    # clone-auth-policy-20260430 adds the broader checks (~/.powerloom
    # writable, local creds when clone_auth.mode=local_credentials).
    try:
        assert_git_available()
    except GitOpError as exc:
        _emit_error(
            str(exc),
            hint="Install git and re-run. https://git-scm.com/downloads",
        )
        raise typer.Exit(1) from None
    try:
        assert_runtime_available(spec.runtime)
    except RuntimeBinaryError as exc:
        _emit_error(
            str(exc),
            hint=(
                "Install the runtime first. The launch token will still "
                "be valid (5min single-use cache) — re-run after install."
            ),
        )
        raise typer.Exit(1) from None

    # ``--root`` overrides the worktree root; threads not yet wired use
    # the default. Sprint flags-polish (5790b2d6) will let users persist
    # the override via ``weave config set worktree-root``.
    paths = (
        WeaveOpenPaths.with_worktree_root(__import__("pathlib").Path(root))
        if root
        else WeaveOpenPaths.default()
    )

    short_id = short_id_from_launch_id(spec.launch_id.hex)

    try:
        bare_clone = ensure_bare_clone(
            paths,
            project_slug=spec.project.slug,
            repo_url=str(spec.project.repo_url),
            clone_auth_token=spec.clone_auth.token,
        )
    except CloneAuthError as exc:
        hint = (
            "Token-based clone auth (sprint clone-auth-policy-20260430) "
            "isn't yet wired; the engine returned no clone token. Configure "
            "git credentials for the host (e.g. `gh auth login`) and retry."
            if spec.clone_auth.token is None
            else (
                "Server-minted clone token rejected by the host. "
                "Re-mint the launch token to get a fresh clone token."
            )
        )
        _emit_error(f"Clone failed: {exc}", hint=hint)
        raise typer.Exit(1) from None
    except GitOpError as exc:
        _emit_error(f"Clone failed: {exc}")
        raise typer.Exit(1) from None

    if not is_json_output():
        _console.print(f"  [green]✓[/green] Repo: {bare_clone}")

    try:
        worktree = create_worktree(
            paths,
            bare_clone=bare_clone,
            scope_slug=spec.scope.slug,
            branch_base=spec.scope.branch_base,
            branch_name=spec.scope.branch_name,
            short_id=short_id,
        )
    except WorktreePathInvalidError as exc:
        _emit_error(
            str(exc),
            hint="Remove the conflicting directory and retry.",
        )
        raise typer.Exit(1) from None
    except GitOpError as exc:
        _emit_error(f"Worktree creation failed: {exc}")
        raise typer.Exit(1) from None

    if not is_json_output():
        _console.print(f"  [green]✓[/green] Worktree: {worktree}")
        warn = path_length_warning(worktree)
        if warn:
            _console.print(f"  [yellow]warn:[/yellow] {warn}")

    # ---- session register + env file --------------------------------------
    try:
        registered = register_agent_session(client, spec)
    except SessionRegisterError as exc:
        # Worktree is already on disk — leave it and let the user retry
        # without re-redeeming. The 5-min cache covers the retry window.
        _emit_error(
            f"Session registration failed (HTTP {exc.status_code}): {exc}",
            hint=(
                "The worktree at "
                f"{worktree} is intact; re-running `weave open <token>` "
                "within 5min retries via the redeem cache."
            ),
        )
        raise typer.Exit(1) from None

    env_file = write_session_env_file(worktree, registered, spec)
    ensure_gitignore_entry(worktree)

    if not is_json_output():
        _console.print(
            f"  [green]✓[/green] Session registered: "
            f"{registered.session_slug} ({registered.session_id[:8]}…)"
        )
        if registered.overlap_warnings:
            for w in registered.overlap_warnings:
                msg = w.get("message") if isinstance(w, dict) else str(w)
                _console.print(f"  [yellow]overlap:[/yellow] {msg}")
        _console.print(f"  [green]✓[/green] Wrote {env_file.name}")

    # ---- runtime hand-off -------------------------------------------------
    # Skill install / MCP wiring / rules_sync still pending in
    # subsequent threads of this sprint; surface a brief TODO so smoke
    # testers know the worktree is technically usable but missing the
    # conveniences before they exec into it.
    if not is_json_output():
        unused = []
        if reuse:
            unused.append(f"--reuse {reuse}")
        if resume:
            unused.append(f"--resume {resume}")
        unused_blurb = (
            f" Flags accepted but not yet wired: {', '.join(unused)}."
            if unused
            else ""
        )
        _console.print(
            "\n[dim]TODO (sprint cli-weave-open-20260430): skill install, "
            "MCP wiring, rules-sync still pending — agent session works "
            "but starts without those pre-loaded."
            + unused_blurb
            + "[/dim]"
        )

        if spec.runtime == ANTIGRAVITY_RUNTIME:
            _console.print(
                "\n[green]✓[/green] Antigravity launch ready. "
                "Start the local worker with [cyan]weave antigravity-worker "
                "start[/cyan] — it picks up the registered session by "
                "polling /agent-sessions."
            )
        else:
            _console.print(f"\n[bold]→[/bold] Launching {spec.runtime} in {worktree}…")

    # Antigravity returns; everything else replaces the process.
    if spec.runtime == ANTIGRAVITY_RUNTIME:
        return

    try:
        exec_runtime(worktree, spec.runtime)
    except RuntimeBinaryError as exc:
        _emit_error(f"Runtime exec failed: {exc}")
        raise typer.Exit(1) from None
