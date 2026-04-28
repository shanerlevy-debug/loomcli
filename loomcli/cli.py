"""Typer app root — wires subcommands + global options.

Subcommands live in loomcli.commands.*. Each one registers its
own Typer sub-app here.

Global options:
    --api-url      override POWERLOOM_API_BASE_URL
    --config-dir   override POWERLOOM_HOME (credentials + config location)
"""
from __future__ import annotations

import os
import sys
from typing import Annotated, Optional

import typer

from loomcli import __version__
from loomcli.commands import agent_cmd
from loomcli.commands import agent_session_cmd
from loomcli.commands import batch_cmd
from loomcli.commands import apply as apply_cmd
from loomcli.commands import auth_cmd
from loomcli.commands import describe as describe_cmd
from loomcli.commands import destroy as destroy_cmd
from loomcli.commands import get as get_cmd
from loomcli.commands import import_ as import_cmd
from loomcli.commands import import_project_cmd
from loomcli.commands import plan as plan_cmd
from loomcli.commands import project_cmd
from loomcli.commands import workflow_cmd
from loomcli.commands import antigravity_worker_cmd
from loomcli.commands import skill_cmd
from loomcli.commands import audit_cmd
from loomcli.commands import approval_cmd
from loomcli.commands import commands_cmd
from loomcli.commands import compose_cmd
from loomcli.commands import conventions_cmd
from loomcli.commands import doctor_cmd
from loomcli.commands import migrate_cmd
from loomcli.commands import plugin_cmd
from loomcli.commands import thread_cmd
from loomcli.commands import profile_cmd
from loomcli.commands import session_cmd
from loomcli.commands import setup_cmd
from loomcli.commands import sprint_cmd


def _configure_stdio() -> None:
    """Force UTF-8 on stdio streams so Windows users don't hit cp1252 +
    surrogateescape crashes when piping a UTF-8 markdown file into a
    `--*-from-stdin` flag (or when Rich prints non-ASCII help text).

    stdin uses errors="strict" — we'd rather crash visibly with a friendly
    remedy printed by main() than silently swap a curly quote for a
    replacement char and ship corrupt content to the API.

    stdout/stderr use errors="replace" — we never want to crash mid-output
    just because a code-page-confused terminal can't render a glyph.
    """
    if os.name != "nt":
        return
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "replace"), (sys.stderr, "replace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            pass


_configure_stdio()


app = typer.Typer(
    help=(
        "Weave: the Powerloom CLI. Declarative manifests + resource "
        "inspection against the control plane."
    ),
    no_args_is_help=True,
    add_completion=True,
)


_AGENT_ENV_VARS = ("CLAUDE_CODE", "GEMINI_CLI", "CODEX_SANDBOX", "AGENT_MODE")


def _detect_auto_json_reason() -> Optional[str]:
    """Return a short human-readable reason if we should default --output=json,
    or None if the user is at an interactive TTY and the table renderer is fine.

    Three triggers, any one of them flips the default:
      1. An agent-marker env var is set (CC, Gemini, Codex, generic AGENT_MODE).
      2. POWERLOOM_ACTIVE_SUBPRINCIPAL_ID is set — the SessionStart hook + the
         CC plugin populate this for any registered agent session, regardless
         of which agent runtime is running. More reliable than (1) since
         hook propagation doesn't always carry env into the parent shell;
         the agent-session register flow writes a per-scope cache file that
         the CLI reads without needing CLAUDE_CODE in the environment.
      3. stdout is not a TTY (output is being piped or captured). Any
         consumer parsing weave's output is going to want JSON; falling back
         to the table renderer in that case produces the one-char-per-row
         column-collapse bug that ate ~6KB of unreadable output during the
         2026-04-27 dogfood pass.

    Pass POWERLOOM_NO_AUTO_JSON=1 to bypass all of the above and force
    interactive defaults (table output) regardless of environment.
    """
    if os.environ.get("POWERLOOM_NO_AUTO_JSON"):
        return None
    for var in _AGENT_ENV_VARS:
        if os.environ.get(var):
            return f"agent env {var}"
    if os.environ.get("POWERLOOM_ACTIVE_SUBPRINCIPAL_ID", "").strip():
        return "registered sub-principal"
    try:
        if not sys.stdout.isatty():
            return "non-TTY stdout"
    except (AttributeError, ValueError):
        # Wrapped streams under pytest capture or odd CI runners can raise.
        # Don't crash here — just leave the default unchanged.
        pass
    return None


def is_agent_mode() -> bool:
    """Check if the CLI is running in AI Agent mode (env detection).

    Kept for any external caller; the auto-JSON path uses
    _detect_auto_json_reason() directly so it can also surface the "why".
    """
    return _detect_auto_json_reason() is not None or os.environ.get("POWERLOOM_FORMAT") == "json"


def _apply_global_options(
    api_url: Optional[str],
    config_dir: Optional[str],
    justification: Optional[str],
    output: Optional[str],
) -> None:
    """Mutate process env before subcommand runs. Typer doesn't have
    first-class "run before subcommand" hooks; setting env here is
    equivalent and means config.load_runtime_config() picks it up."""
    if api_url:
        os.environ["POWERLOOM_API_BASE_URL"] = api_url
    if config_dir:
        os.environ["POWERLOOM_HOME"] = config_dir
    if justification:
        os.environ["POWERLOOM_APPROVAL_JUSTIFICATION"] = justification

    # Agent Mode / Non-TTY Detection: if no explicit format was passed and the
    # env doesn't already pin one, switch to JSON when stdout isn't a TTY or
    # an agent-session marker is present. Surface a stderr note so the human
    # supervising the agent (or anyone running in CI) isn't surprised; suppress
    # it when the auto-detected format is the default the env already wants.
    if not output and not os.environ.get("POWERLOOM_FORMAT"):
        reason = _detect_auto_json_reason()
        if reason:
            output = "json"
            quiet = os.environ.get("POWERLOOM_QUIET_AUTO_JSON", "").strip()
            if not quiet:
                typer.echo(
                    f"weave: output format auto-set to json ({reason}). "
                    f"Pass -o table or set POWERLOOM_FORMAT=table to override; "
                    f"set POWERLOOM_QUIET_AUTO_JSON=1 to silence this notice.",
                    err=True,
                )

    if output:
        os.environ["POWERLOOM_FORMAT"] = output


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    api_url: Annotated[
        Optional[str],
        typer.Option(
            "--api-url",
            envvar="POWERLOOM_API_BASE_URL",
            help="Base URL of the control plane. Default: https://api.powerloom.org (use http://localhost:8000 for local docker-compose dev).",
        ),
    ] = None,
    config_dir: Annotated[
        Optional[str],
        typer.Option(
            "--config-dir",
            envvar="POWERLOOM_HOME",
            help="Override the CLI config directory (token + settings).",
        ),
    ] = None,
    justification: Annotated[
        Optional[str],
        typer.Option(
            "--justification",
            envvar="POWERLOOM_APPROVAL_JUSTIFICATION",
            help=(
                "Justification for approval-gated operations. Sent as "
                "X-Approval-Justification header on every request. "
                "Required when your org has a policy that demands it "
                "(otherwise create/update calls return HTTP 409 with "
                "code=justification_required)."
            ),
        ),
    ] = None,
    output: Annotated[
        Optional[str],
        typer.Option(
            "-o",
            "--output",
            envvar="POWERLOOM_FORMAT",
            help="Output format: 'table' or 'json'.",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", help="Print version and exit."),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    _apply_global_options(api_url, config_dir, justification, output)
    if ctx.invoked_subcommand is None:
        # No subcommand given and no --version: show help + exit non-zero.
        # Matches `no_args_is_help=True` behavior we want for everything
        # except the --version path.
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


# Register subcommands.
app.add_typer(auth_cmd.app, name="auth", help="Login / logout / whoami / PAT management.")
app.add_typer(agent_cmd.app, name="agent", help="Inspect agents and manage identities.")
app.add_typer(agent_session_cmd.app, name="agent-session", help="Phase 14 coordination-session management.")
app.add_typer(session_cmd.app, name="session", help="Inspect session event traces.")
app.add_typer(thread_cmd.app, name="thread", help="Inspect tracker threads.")
app.add_typer(project_cmd.app, name="project", help="Inspect tracker projects (ls / show).")
app.add_typer(workflow_cmd.app, name="workflow", help="Workflow definitions + runs (Phase 14).")
app.add_typer(antigravity_worker_cmd.app, name="antigravity-worker", help="Daemon to dispatch tasks to local Antigravity IDE.")
app.add_typer(skill_cmd.app, name="skill", help="Manage Skill archives (upload + activate versions).")
app.add_typer(audit_cmd.app, name="audit", help="Query the Powerloom audit log.", invoke_without_command=True)
app.add_typer(approval_cmd.app, name="approval", help="Inspect + decide on approval requests (list/get/approve/reject/cancel/bulk-cancel).")
app.add_typer(compose_cmd.app, name="compose", help="Author, lint, and inspect v2.0.0 Compose kinds (scaffold/lint/show).")
app.add_typer(migrate_cmd.app, name="migrate", help="Upgrade manifests between schema versions (v1->v2).")
app.add_typer(plugin_cmd.app, name="plugin", help="Inspect and install Powerloom client plugins.")
app.add_typer(sprint_cmd.app, name="sprint", help="Manage tracker sprints (create / list / show / update / activate / complete / archive / add-thread / remove-thread / threads).")
app.add_typer(conventions_cmd.app, name="conventions", help="Sync OU-scoped Powerloom conventions into CLAUDE.md / AGENTS.md / GEMINI.md (sync / show / list).")
app.add_typer(profile_cmd.app, name="profile", help="Manage local CLI profiles and defaults.")
app.add_typer(setup_cmd.app, name="setup-claude-code", help="Wire the Powerloom MCP plugin into a Claude Code project (idempotent).")
app.command("commands", help="List command metadata for autocomplete and clients.")(commands_cmd.commands_command)
app.command("doctor", help="Check local auth, server capabilities, and plugin prerequisites.")(doctor_cmd.doctor_command)
app.command("ask", help="Ask a Powerloom agent and stream the answer.")(agent_cmd.ask_command)
app.command("chat", help="Start an interactive terminal chat with a Powerloom agent.")(agent_cmd.chat_command)
app.command("batch", help="Run multiple weave commands sequentially.")(batch_cmd.batch_command)
app.command("apply", help="Apply a manifest (create/update resources).")(apply_cmd.apply_command)
app.command("plan", help="Show what apply would do, without making changes.")(plan_cmd.plan_command)
app.command("destroy", help="Delete the resources in a manifest.")(destroy_cmd.destroy_command)
app.command("get", help="List or show a resource kind.")(get_cmd.get_command)
app.command("describe", help="Show a single resource with full detail.")(describe_cmd.describe_command)
app.command("import", help="Adopt an existing resource into a manifest.")(import_cmd.import_command)
app.command(
    "import-project",
    help="Import a Powerloom-shaped repo into the tracker (v059 self-import MVP).",
)(import_project_cmd.import_project_command)


# Top-level aliases — `weave login`, `weave logout`, `weave whoami` —
# call through to the same functions registered under `weave auth`.
# Matches gh / aws / gcloud CLI muscle memory where login is top-level.
app.command("login", help="Sign in to Powerloom (alias for `weave auth login`).")(
    auth_cmd.login
)
app.command("logout", help="Clear credentials (alias for `weave auth logout`).")(
    auth_cmd.logout
)
app.command("whoami", help="Show signed-in user (alias for `weave auth whoami`).")(
    auth_cmd.whoami
)


def main() -> None:
    try:
        app()
    except UnicodeDecodeError as e:
        # Hit when stdin is decoded as cp1252 / UTF-16 instead of UTF-8.
        # Common on Windows when PYTHONUTF8 isn't set and a UTF-8 markdown
        # file is piped into `--description-from-stdin`. _configure_stdio()
        # forces stdin to UTF-8 above; this catches the residual case where
        # the input bytes themselves aren't valid UTF-8 (e.g. a UTF-16 BOM).
        typer.echo(
            "weave: stdin is not valid UTF-8. Re-export with PYTHONUTF8=1 "
            "or pipe the content through 'iconv -t utf-8' first.\n"
            f"  decoder detail: {e!s}",
            err=True,
        )
        raise typer.Exit(1) from e
    except UnicodeEncodeError as e:
        # Hit when a string the CLI built contains lone surrogate codepoints
        # (e.g. the platform stdin reader inserted them via surrogateescape
        # before _configure_stdio() ran). Same root cause as UnicodeDecodeError
        # for the user; the remedy is identical.
        msg = str(e)
        looks_like_stdin = "surrogates not allowed" in msg or "\\udc" in msg
        if looks_like_stdin:
            typer.echo(
                "weave: input contains lone surrogate codepoints, which usually "
                "means stdin was decoded as cp1252 / UTF-16 instead of UTF-8.\n"
                "  Fix: re-export with PYTHONUTF8=1 (and PYTHONIOENCODING=utf-8 "
                "for older Pythons), or pipe input through 'iconv -t utf-8'.\n"
                f"  encoder detail: {msg}",
                err=True,
            )
        else:
            detail = msg.encode("ascii", "backslashreplace").decode("ascii")
            typer.echo(f"Output encoding error: {detail}", err=True)
        raise typer.Exit(1) from e


if __name__ == "__main__":
    main()
