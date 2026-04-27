"""`weave agent-session register / end / ls / get` — Phase 14 Foundation coordination commands.

Replaces hand-edited COWORK.md §6 Active-sessions workflow. A Claude
Code session calls `weave agent-session register` at task start to
check in with its scope; when its PR merges, it calls `weave
agent-session end --outcome merged`.

Named `agent-session` to disambiguate from `weave get session` which
lists CMA agent-runtime sessions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import load_runtime_config

# Pattern for the project's branch naming convention: session/<scope>-<yyyymmdd>
_BRANCH_RE = re.compile(r"^session/(?P<scope>.+)-(?P<date>\d{8})$")
_CLIENT_TO_ACTOR = {
    "claude": "claude_code",
    "claude_code": "claude_code",
    "codex": "codex_cli",
    "codex_cli": "codex_cli",
    "gemini": "gemini_cli",
    "gemini_cli": "gemini_cli",
    "antigravity": "antigravity",
}
_ACTOR_TO_TOKEN = {
    "claude_code": "claude",
    "codex_cli": "codex",
    "gemini_cli": "gemini",
    "antigravity": "antigravity",
}


_console = Console()
_TERMINAL_STATUSES = {"merged", "abandoned", "yielded"}

_TERMINAL_STATUSES = {"merged", "abandoned", "yielded"}

app = typer.Typer(
    help="Agent-session coordination (Phase 14 Foundation).",
    no_args_is_help=True,
)


def _client() -> PowerloomClient:
    cfg = load_runtime_config()
    if cfg.access_token is None:
        _console.print(
            "[yellow]Not signed in. Run `weave auth login --dev-as <email>` first.[/yellow]"
        )
        raise typer.Exit(1)
    return PowerloomClient(cfg)


def _current_git_branch() -> Optional[str]:
    """Return the current git branch name, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _parse_branch(branch_name: str) -> tuple[str, str]:
    """Extract (scope, branch_name) from a branch.

    If it matches session/<scope>-<yyyymmdd>, uses that scope.
    Otherwise, slugifies the branch name and appends today's date.
    """
    m = _BRANCH_RE.match(branch_name)
    if m:
        scope = f"{m.group('scope')}-{m.group('date')}"
        return scope, branch_name

    # Fallback: slugify and append date
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    clean_name = _slugify(branch_name.replace("session/", ""))
    return f"{clean_name}-{today}", branch_name


def _ensure_subprincipal(
    client: PowerloomClient,
    *,
    scope: str,
    actor_kind: str,
) -> Optional[str]:
    """Find-or-create a sub-principal for this (actor_kind, scope) and
    cache its UUID in `<config_dir>/active-subprincipal-<scope>.txt`.

    The auto-stamp path in `loomcli.commands.thread_cmd` reads
    `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` from env first; the per-scope
    file is the fallback for the common case where the SessionStart
    hook can't propagate env to the parent shell. Together they make
    every `weave thread create / reply / pluck` carry attribution
    without any human action.

    Naming: `<actor_kind>:<scope>` — e.g. `claude_code:phase23-service-accounts-20260425`.
    Stable across re-runs. The find-by-name pass is a linear GET /me/agents
    scan; fine for the small per-user sub-principal counts expected.

    Returns the sub-principal UUID on success, None on best-effort
    failure (network, permissions). Failures are non-fatal — the calling
    `register` command should continue normally; attribution stamping
    just stays disabled for this session.
    """
    from loomcli.config import active_subprincipal_file

    desired_name = f"{actor_kind}:{scope}"
    try:
        existing = client.get("/me/agents")
    except PowerloomApiError:
        return None
    items = existing if isinstance(existing, list) else (existing.get("items") or [])
    sub_id: Optional[str] = None
    for sp in items:
        if isinstance(sp, dict) and sp.get("name") == desired_name:
            sub_id = sp.get("id")
            break
    if sub_id is None:
        try:
            created = client.post(
                "/me/agents",
                {
                    "name": desired_name,
                    "client_kind": actor_kind,
                    "description": (
                        f"Auto-registered by `weave agent-session register` for "
                        f"branch session/{scope}. Used by thread_cmd to stamp "
                        f"session_attribution on tracker actions."
                    ),
                },
            )
        except PowerloomApiError:
            return None
        sub_id = created.get("id") or (created.get("subprincipal") or {}).get("id")
    if sub_id is None:
        return None

    # Write the per-scope cache file. Best-effort — if the dir doesn't
    # exist, create it; if the write fails (read-only fs, etc.),
    # silently degrade so the env-var path still works.
    try:
        path = active_subprincipal_file(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(sub_id), encoding="utf-8")
    except OSError:
        pass
    return sub_id


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "session"


def _normalize_actor_kind(client_kind: str) -> str:
    if client_kind == "auto":
        if os.environ.get("CODEX_SANDBOX") or shutil.which("codex"):
            return "codex_cli"
        if os.environ.get("GEMINI_CLI") or shutil.which("gemini"):
            return "gemini_cli"
        if shutil.which("claude"):
            return "claude_code"
        return "codex_cli"
    actor = _CLIENT_TO_ACTOR.get(client_kind)
    if not actor:
        _console.print(
            "[red]Unknown client.[/red] Use auto, codex, codex_cli, "
            "claude, claude_code, gemini, gemini_cli, or antigravity."
        )
        raise typer.Exit(2)
    return actor


def _format_template(template: str, context: dict[str, str]) -> str:
    try:
        return template.format(**context)
    except KeyError as e:
        _console.print(f"[red]Unknown template field:[/red] {e}")
        raise typer.Exit(2) from None


def _repo_dir_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    if dry_run:
        _console.print(f"[dim]dry-run:[/dim] {' '.join(args)}")
        return None
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as e:
        _console.print(f"[red]Command not found:[/red] {args[0]}")
        raise typer.Exit(1) from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        _console.print(f"[red]Command failed:[/red] {' '.join(args)}")
        if detail:
            _console.print(detail)
        raise typer.Exit(result.returncode)
    return result


def _fetch_bootstrap_config(client: PowerloomClient, project: str) -> dict[str, Any] | None:
    try:
        return client.get(f"/projects/{project}/bootstrap")
    except PowerloomApiError as e:
        if e.status_code == 404:
            _console.print(
                f"[yellow]No bootstrap config found for project {project!r}.[/yellow] "
                "Continuing with CLI flags."
            )
            return None
        _console.print(f"[red]Could not fetch bootstrap config:[/red] {e}")
        raise typer.Exit(1) from None


def _load_codex_plugin_state() -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
        return {"config_path": None, "enabled": False, "marketplace_source": None}

    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return {"config_path": str(path), "enabled": False, "marketplace_source": None}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {"config_path": str(path), "enabled": False, "marketplace_source": None}
    marketplaces = data.get("marketplaces") or {}
    plugins = data.get("plugins") or {}
    powerloom_marketplace = marketplaces.get("powerloom") or {}
    plugin = plugins.get("powerloom-weave@powerloom") or {}
    return {
        "config_path": str(path),
        "enabled": bool(plugin.get("enabled")),
        "marketplace_source": powerloom_marketplace.get("source"),
    }


def _check_client_plugin(actor_kind: str) -> None:
    if actor_kind != "codex_cli":
        binary = {
            "claude_code": "claude",
            "gemini_cli": "gemini",
            "antigravity": None,
        }.get(actor_kind)
        if binary and not shutil.which(binary):
            _console.print(f"[yellow]Warning:[/yellow] {binary} is not on PATH.")
        return

    state = _load_codex_plugin_state()
    if state["enabled"] and state["marketplace_source"]:
        _console.print(
            f"[green]Codex plugin enabled.[/green] "
            f"marketplace={state['marketplace_source']}"
        )
        return
    _console.print("[yellow]Codex plugin is not fully enabled.[/yellow]")
    if state.get("config_path"):
        _console.print(f"  config: {state['config_path']}")
    _console.print(
        "  Run: codex plugin marketplace add C:\\path\\to\\loomcli\\plugins\\codex "
        "and enable powerloom-weave@powerloom."
    )


def _ensure_repo(
    *,
    repo_url: str,
    default_branch: str,
    workdir: Path,
    session_branch: str,
    create_branch: bool,
    dry_run: bool,
) -> Path:
    repo_path = workdir / _repo_dir_name(repo_url)
    if not repo_path.exists():
        if not dry_run:
            workdir.mkdir(parents=True, exist_ok=True)
        _run(
            ["git", "clone", "--branch", default_branch, repo_url, str(repo_path)],
            dry_run=dry_run,
        )
    elif (repo_path / ".git").exists():
        _run(["git", "fetch", "origin", default_branch], cwd=repo_path, dry_run=dry_run)
        _run(["git", "checkout", default_branch], cwd=repo_path, dry_run=dry_run)
        _run(["git", "merge", "--ff-only", f"origin/{default_branch}"], cwd=repo_path, dry_run=dry_run)
    else:
        _console.print(f"[red]Target exists but is not a git repo:[/red] {repo_path}")
        raise typer.Exit(1)

    if create_branch:
        exists = False
        if not dry_run:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", session_branch],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            exists = result.returncode == 0
        _run(
            ["git", "checkout", session_branch]
            if exists
            else ["git", "checkout", "-b", session_branch],
            cwd=repo_path,
            dry_run=dry_run,
        )
    return repo_path


def get_active_session_for_branch(client: PowerloomClient) -> dict[str, Any] | None:
    """Helper for other commands (ask, chat, status) to find the session
    linked to the current git branch. Returns the full session detail
    dict or None if not in a git repo or no matching session exists.
    """
    branch = _current_git_branch()
    if not branch:
        return None

    try:
        # 1. Look for a session matching the branch name exactly
        resp = client.get("/agent-sessions", status="active", limit=100)
        sessions = resp.get("sessions", [])
        for s in sessions:
            if s.get("branch_name") == branch:
                # Return the full detail (which contains agent/OU info)
                return client.get(f"/agent-sessions/{s['id']}")

        # 2. Fallback: slugify the branch name and try to match the scope
        scope, _ = _parse_branch(branch)
        for s in sessions:
            if s.get("session_slug") == scope:
                return client.get(f"/agent-sessions/{s['id']}")
    except Exception:
        pass
    return None


@app.command("init")
def init_cmd(
    branch: Annotated[Optional[str], typer.Argument(help="Feature branch name to create. If it exists, we just check it out.")] = None,
    scope: Annotated[Optional[str], typer.Option("--scope", help="Session scope slug. Defaults to slugified branch name.")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", help="One-line description.")] = None,
    capabilities: Annotated[Optional[str], typer.Option("--capabilities", help="Comma-separated capability tags.")] = None,
    actor_kind: Annotated[str, typer.Option("--actor-kind", help="auto | gemini_cli | claude_code | etc.")] = "auto",
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of human output")] = False,
) -> None:
    """Create a new feature branch and register it as a coordination session."""
    if not branch:
        _console.print("[red]Branch name is required.[/red]")
        raise typer.Exit(1)

    # 1. Create or checkout the branch
    repo_root = _run(["git", "rev-parse", "--show-toplevel"])
    if not repo_root:
        _console.print("[red]Not in a git repository.[/red]")
        raise typer.Exit(1)

    # Check if branch exists
    exists_res = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        capture_output=True,
        text=True,
    )
    if exists_res.returncode == 0:
        _console.print(f"[dim]Branch {branch!r} already exists. Checking it out...[/dim]")
        _run(["git", "checkout", branch])
    else:
        _console.print(f"[green]Creating branch {branch!r}...[/green]")
        _run(["git", "checkout", "-b", branch])

    # 2. Register the session
    # Reuse register_cmd logic by calling it or refactoring it.
    # For simplicity here, we'll just call the registration logic.
    actor = _normalize_actor_kind(actor_kind)
    inferred_scope, _ = _parse_branch(branch)
    final_scope = scope or inferred_scope
    final_summary = summary or f"Session for {branch}"

    register_cmd(
        scope=final_scope,
        summary=final_summary,
        branch=branch,
        capabilities=capabilities,
        actor_kind=actor,
        json_out=json_out
    )


@app.command("register")
def register_cmd(
    scope: Annotated[Optional[str], typer.Option("--scope", help="Session scope slug; typically `<name>-<yyyymmdd>`. Required unless --from-branch is set.")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", help="One-line scope description. Required unless --from-branch is set (which uses the branch name as a fallback).")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", help="Feature branch name.")] = None,
    capabilities: Annotated[Optional[str], typer.Option("--capabilities", help="Comma-separated capability tags (e.g. 'ui,docs,python')")] = None,
    cross_cutting: Annotated[bool, typer.Option("--cross-cutting/--no-cross-cutting", help="Does this session touch many files across modules?")] = False,
    migration: Annotated[bool, typer.Option("--migration/--no-migration", help="Does this session add an Alembic migration?")] = False,
    version: Annotated[Optional[str], typer.Option("--version", help="Target version, e.g. v030")] = None,
    actor_kind: Annotated[str, typer.Option("--actor-kind", help="claude_code | codex_cli | gemini_cli | antigravity | cma | human")] = "claude_code",
    actor_id: Annotated[Optional[str], typer.Option("--actor-id", help="Session identifier (defaults to caller email)")] = None,
    from_branch: Annotated[bool, typer.Option("--from-branch", help="Infer --scope and --branch from the current git branch (must match session/<scope>-<yyyymmdd>).")] = False,
    if_not_active: Annotated[bool, typer.Option("--if-not-active", help="No-op (exit 0) if a session with the same scope is already active. Safe for SessionStart hooks.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of human output")] = False,
) -> None:
    """Register a new active agent session. Returns session id, work-chain
    event hash, and any overlap warnings."""

    # --from-branch: resolve scope + branch from git
    if from_branch:
        git_branch = _current_git_branch()
        if not git_branch:
            _console.print(
                "[red]--from-branch: could not determine the current git branch.[/red]"
            )
            raise typer.Exit(1)
        parsed = _parse_branch(git_branch)
        if not parsed:
            _console.print(
                f"[red]--from-branch: branch {git_branch!r} does not match "
                f"session/<scope>-<yyyymmdd> convention.[/red]"
            )
            raise typer.Exit(1)
        inferred_scope, inferred_branch = parsed
        scope = scope or inferred_scope
        branch = branch or inferred_branch
        summary = summary or f"Claude Code session on {inferred_scope}"

    # Validate required fields (may still be None if --from-branch wasn't set)
    if not scope:
        _console.print("[red]--scope is required (or use --from-branch).[/red]")
        raise typer.Exit(1)
    if not summary:
        _console.print("[red]--summary is required (or use --from-branch).[/red]")
        raise typer.Exit(1)

    # --if-not-active: check for an existing active session with this scope
    if if_not_active:
        client = _client()
        try:
            resp = client.get("/agent-sessions", status="active", limit=100)
            existing = resp.get("sessions", [])
            if any(s.get("session_slug") == scope for s in existing):
                _console.print(
                    f"[dim]Session {scope!r} already active — skipping registration.[/dim]"
                )
                return
        except PowerloomApiError:
            pass  # network error — fall through and attempt registration

    caps = [c.strip() for c in (capabilities or "").split(",") if c.strip()]
    body = {
        "session_slug": scope,
        "scope_summary": summary,
        "branch_name": branch,
        "capabilities": caps,
        "cross_cutting": cross_cutting,
        "touches_migration": migration,
        "version_claimed": version,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
    }
    client = _client()
    try:
        resp = client.post("/agent-sessions", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    # Find-or-create the per-session sub-principal AFTER the agent-session
    # POST succeeds (so we don't create orphaned sub-principals when the
    # session POST fails for an unrelated reason). Best-effort — if the
    # /me/agents call fails, registration still succeeds; attribution
    # stamping just stays disabled for this session and can be re-tried
    # on the next register.
    sub_id = _ensure_subprincipal(client, scope=scope, actor_kind=actor_kind)

    if json_out:
        # Surface the sub-principal id in JSON output for tooling
        # (e.g. SessionStart hooks that want to log the binding).
        if sub_id is not None:
            resp = dict(resp)
            resp["subprincipal_id"] = sub_id
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    sess = resp["session"]
    _console.print(f"[green]Registered[/green] session [bold]{sess['session_slug']}[/bold]")
    _console.print(f"  id: {sess['id']}")
    _console.print(f"  work-chain event hash: {resp['work_chain_event_hash']}")
    _console.print(f"  version claimed: {sess.get('version_claimed') or '(none)'}")
    _console.print(f"  capabilities: {sess.get('capabilities') or []}")
    if sub_id is not None:
        _console.print(
            f"  sub-principal: [cyan]{actor_kind}:{scope}[/cyan] "
            f"[dim](id={sub_id[:8]}…, cached for thread_cmd auto-stamp)[/dim]"
        )
    else:
        _console.print(
            "  [yellow]sub-principal: not bound (find-or-create failed; "
            "session_attribution will be empty until the next register).[/yellow]"
        )
    warnings = resp.get("overlap_warnings", [])
    if warnings:
        _console.print("[yellow]Overlap warnings:[/yellow]")
        for w in warnings:
            _console.print(f"  - {w}")
    else:
        _console.print("[dim]No overlaps with other active sessions.[/dim]")


@app.command("bootstrap")
def bootstrap_cmd(
    project: Annotated[str, typer.Option("--project", help="Project slug or UUID to bootstrap from.")] = "powerloom",
    client_kind: Annotated[str, typer.Option("--client", help="auto | codex_cli | claude_code | gemini_cli | antigravity")] = "auto",
    repo_url: Annotated[Optional[str], typer.Option("--repo-url", help="Override the repository URL from project bootstrap config.")] = None,
    workdir: Annotated[Optional[str], typer.Option("--workdir", help="Directory where the repository should be cloned or updated.")] = None,
    default_branch: Annotated[Optional[str], typer.Option("--branch", help="Default upstream branch to clone/update.")] = None,
    scope: Annotated[Optional[str], typer.Option("--scope", help="Override generated session scope.")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", help="Override generated session summary.")] = None,
    capabilities: Annotated[Optional[str], typer.Option("--capabilities", help="Comma-separated capability tags. Overrides project config.")] = None,
    actor_id: Annotated[Optional[str], typer.Option("--actor-id", help="Session identifier (defaults to caller email).")] = None,
    create_branch: Annotated[bool, typer.Option("--create-branch/--no-create-branch", help="Create or reuse the configured session branch after updating main.")] = True,
    register: Annotated[bool, typer.Option("--register/--no-register", help="Register this coordination session after checkout.")] = True,
    if_not_active: Annotated[bool, typer.Option("--if-not-active/--always-register", help="Skip registration when the generated scope is already active.")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print planned git/API actions without mutating local repo or registering.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON result.")] = False,
) -> None:
    """Bootstrap an empty-folder client into a Powerloom project.

    This is the single-prompt entry point for Codex CLI, Claude Code, Gemini
    CLI, and Antigravity sessions. It asks Powerloom for project bootstrap
    metadata, checks out the configured repo from main, verifies the client
    plugin surface, and registers the coordination session.
    """
    actor_kind = _normalize_actor_kind(client_kind)
    client_token = _ACTOR_TO_TOKEN[actor_kind]
    today = date.today().strftime("%Y%m%d")

    client = _client()
    bootstrap = _fetch_bootstrap_config(client, project)
    project_slug = (bootstrap or {}).get("project_slug") or _slugify(project)
    project_name = (bootstrap or {}).get("project_name") or project_slug
    config = dict((bootstrap or {}).get("config") or {})

    selected_repo_url = repo_url or config.get("repo_url")
    if not selected_repo_url:
        _console.print(
            "[red]No repository URL configured.[/red] Set it in the "
            "Powerloom project bootstrap settings or pass --repo-url."
        )
        raise typer.Exit(1)

    selected_branch = default_branch or config.get("default_branch") or "main"
    selected_workdir = Path(
        workdir or config.get("recommended_workdir") or os.getcwd()
    ).expanduser()
    template_context = {
        "client": client_token,
        "actor_kind": actor_kind,
        "project": project_slug,
        "project_name": project_name,
        "date": today,
    }
    selected_scope = scope or _format_template(
        config.get("session_scope_template") or "{client}-{project}-{date}",
        template_context,
    )
    template_context["scope"] = selected_scope
    session_branch = (
        _format_template(
            config.get("branch_template") or "session/{scope}",
            template_context,
        )
        if create_branch
        else selected_branch
    )
    selected_summary = summary or _format_template(
        config.get("summary_template") or "{client} session for {project}",
        template_context,
    )
    caps = (
        [c.strip() for c in capabilities.split(",") if c.strip()]
        if capabilities is not None
        else list(config.get("capabilities") or [])
    )

    repo_path = _ensure_repo(
        repo_url=selected_repo_url,
        default_branch=selected_branch,
        workdir=selected_workdir,
        session_branch=session_branch,
        create_branch=create_branch,
        dry_run=dry_run,
    )
    _check_client_plugin(actor_kind)

    session_resp: dict[str, Any] | None = None
    if register:
        body = {
            "session_slug": selected_scope,
            "scope_summary": selected_summary,
            "branch_name": session_branch,
            "capabilities": caps,
            "cross_cutting": False,
            "touches_migration": False,
            "version_claimed": None,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
        }
        if dry_run:
            _console.print(f"[dim]dry-run:[/dim] POST /agent-sessions {body}")
        else:
            try:
                if if_not_active:
                    active = client.get("/agent-sessions", status="active", limit=100)
                    if any(
                        s.get("session_slug") == selected_scope
                        for s in active.get("sessions", [])
                    ):
                        _console.print(
                            f"[dim]Session {selected_scope!r} already active; "
                            "skipping registration.[/dim]"
                        )
                    else:
                        session_resp = client.post("/agent-sessions", body)
                else:
                    session_resp = client.post("/agent-sessions", body)
            except PowerloomApiError as e:
                _console.print(f"[red]Registration failed:[/red] {e}")
                raise typer.Exit(1) from e

    result = {
        "project": project_slug,
        "repo_path": str(repo_path),
        "default_branch": selected_branch,
        "session_branch": session_branch,
        "scope": selected_scope,
        "actor_kind": actor_kind,
        "capabilities": caps,
        "session": session_resp,
    }
    if json_out:
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    _console.print("[green]Bootstrap complete.[/green]")
    _console.print(f"  project: {project_slug}")
    _console.print(f"  repo: {repo_path}")
    _console.print(f"  branch: {session_branch}")
    _console.print(f"  scope: {selected_scope}")
    if session_resp:
        sess = session_resp.get("session", {})
        _console.print(f"  session id: {sess.get('id')}")
        warnings = session_resp.get("overlap_warnings") or []
        for warning in warnings:
            _console.print(f"  [yellow]overlap:[/yellow] {warning}")


@app.command("update")
def update_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    summary: Annotated[Optional[str], typer.Option("--summary", help="Updated one-line scope description.")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", help="Updated feature branch name.")] = None,
    capabilities: Annotated[Optional[str], typer.Option("--capabilities", help="Updated comma-separated capability tags.")] = None,
    cross_cutting: Annotated[Optional[bool], typer.Option("--cross-cutting/--no-cross-cutting", help="Update cross-cutting status.")] = None,
    migration: Annotated[Optional[bool], typer.Option("--migration/--no-migration", help="Update migration status.")] = None,
    version: Annotated[Optional[str], typer.Option("--version", help="Update target version.")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of human output")] = False,
) -> None:
    """Update metadata for an active agent session."""
    body: dict[str, Any] = {}
    if summary is not None:
        body["scope_summary"] = summary
    if branch is not None:
        body["branch_name"] = branch
    if capabilities is not None:
        body["capabilities"] = [c.strip() for c in capabilities.split(",") if c.strip()]
    if cross_cutting is not None:
        body["cross_cutting"] = cross_cutting
    if migration is not None:
        body["touches_migration"] = migration
    if version is not None:
        body["version_claimed"] = version

    if not body:
        _console.print("[yellow]No updates provided.[/yellow]")
        return

    client = _client()
    try:
        resp = client.patch(f"/agent-sessions/{session_id}", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    sess = resp["session"]
    _console.print(f"[green]Updated[/green] session [bold]{sess['session_slug']}[/bold]")
    _console.print(f"  work-chain event hash: {resp['work_chain_event_hash']}")


@app.command("end")
def end_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    outcome: Annotated[str, typer.Option("--outcome", help="merged | abandoned")] = "merged",
    pr_url: Annotated[Optional[str], typer.Option("--pr-url", help="Merged PR URL")] = None,
    reason: Annotated[Optional[str], typer.Option("--reason", help="Abandonment reason")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Terminate an active session with outcome merged or abandoned."""
    body = {
        "outcome": outcome,
        "pr_url": pr_url,
        "abandoned_reason": reason,
    }
    client = _client()
    try:
        resp = client.post(f"/agent-sessions/{session_id}/end", body)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return
    _console.print(
        f"[green]Session {resp['session_slug']!r} ended "
        f"({resp['status']}).[/green]"
    )


@app.command("ls")
def ls_cmd(
    status_filter: Annotated[Optional[str], typer.Option("--status", help="active | yielded | merged | abandoned")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List sessions."""
    client = _client()
    kw: dict = {"limit": limit}
    if status_filter:
        kw["status"] = status_filter
    try:
        resp = client.get("/agent-sessions", **kw)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    sessions = resp.get("sessions", [])
    if not sessions:
        _console.print("[dim]No sessions.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug")
    table.add_column("Status")
    table.add_column("Actor")
    table.add_column("Version")
    table.add_column("XX")
    table.add_column("Mig")
    table.add_column("Started")
    for s in sessions:
        table.add_row(
            s.get("session_slug", ""),
            s.get("status", ""),
            s.get("actor_kind", ""),
            s.get("version_claimed") or "-",
            "yes" if s.get("cross_cutting") else "-",
            "yes" if s.get("touches_migration") else "-",
            (s.get("started_at") or "")[:19],
        )
    _console.print(table)


@app.command("get")
def get_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fetch one session's full detail."""
    client = _client()
    try:
        resp = client.get(f"/agent-sessions/{session_id}")
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    _console.print(f"[bold]{resp['session_slug']}[/bold]")
    for k in (
        "id",
        "status",
        "actor_kind",
        "actor_id",
        "branch_name",
        "version_claimed",
        "capabilities",
        "cross_cutting",
        "touches_migration",
        "scope_summary",
        "pr_url",
        "started_at",
        "merged_at",
        "abandoned_reason",
    ):
        v = resp.get(k)
        if v is not None and v != "":
            _console.print(f"  {k}: {v}")


@app.command("status")
def status_cmd(
    session_id: Annotated[str, typer.Argument(help="Coordination session ID (UUID)")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one coordination session plus its assigned workflow tasks."""
    client = _client()
    try:
        snapshot = _coordination_session_snapshot(client, session_id)
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(snapshot, indent=2, default=str))
        return
    _print_coordination_session_snapshot(snapshot)


@app.command("watch")
def watch_cmd(
    session_id: Annotated[str, typer.Argument(help="Coordination session ID (UUID)")],
    interval: Annotated[
        float,
        typer.Option("--interval", min=1.0, help="Polling interval in seconds."),
    ] = 3.0,
    once: Annotated[bool, typer.Option("--once", help="Print one snapshot and exit.")] = False,
) -> None:
    """Poll one coordination session until interrupted or terminal."""
    client = _client()
    try:
        while True:
            snapshot = _coordination_session_snapshot(client, session_id)
            _console.print(_coordination_watch_line(snapshot))
            if once or str(snapshot["session"].get("status", "")).lower() in _TERMINAL_STATUSES:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo()
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


def _coordination_session_snapshot(
    client: PowerloomClient, session_id: str
) -> dict[str, Any]:
    session = client.get(f"/agent-sessions/{session_id}")
    try:
        task_resp = client.get(f"/agent-sessions/{session_id}/tasks")
    except PowerloomApiError as e:
        if e.status_code != 404:
            raise
        task_resp = {}
    tasks = []
    if isinstance(task_resp, dict):
        tasks = [t for t in task_resp.get("tasks", []) if isinstance(t, dict)]
    return {"session": session, "tasks": tasks}


def _print_coordination_session_snapshot(snapshot: dict[str, Any]) -> None:
    session = snapshot["session"]
    table = Table(title="Coordination session status", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    for key in (
        "id",
        "session_slug",
        "status",
        "actor_kind",
        "actor_id",
        "branch_name",
        "scope_summary",
        "version_claimed",
        "started_at",
        "last_heartbeat_at",
        "merged_at",
        "abandoned_reason",
    ):
        value = session.get(key)
        if value is not None and value != "":
            table.add_row(key, str(value))
    table.add_row("assigned_tasks", str(len(snapshot["tasks"])))
    _console.print(table)

    if snapshot["tasks"]:
        task_table = Table(title="Assigned workflow tasks", show_header=True)
        for col in ("id", "workflow_name", "node_id", "node_kind", "status"):
            task_table.add_column(col)
        for task in snapshot["tasks"]:
            task_table.add_row(
                str(task.get("id", "")),
                str(task.get("workflow_name") or ""),
                str(task.get("node_id", "")),
                str(task.get("node_kind", "")),
                str(task.get("status", "")),
            )
        _console.print(task_table)


def _coordination_watch_line(snapshot: dict[str, Any]) -> str:
    session = snapshot["session"]
    slug = session.get("session_slug") or session.get("id")
    task_bits = []
    for task in snapshot["tasks"][:3]:
        label = task.get("node_id") or task.get("id")
        task_bits.append(f"{label}:{task.get('status', 'unknown')}")
    tasks = ", ".join(task_bits) if task_bits else "none"
    return (
        f"{slug} | status={session.get('status', 'unknown')} | "
        f"actor={session.get('actor_kind', '')} | tasks={tasks}"
    )


# ---------------------------------------------------------------------------
# Phase 14 Runtime (v031) — task claim / complete for agent-node execution
# ---------------------------------------------------------------------------


@app.command("tasks")
def tasks_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List workflow steps currently assigned to this session.

    A step shows up here when the workflow scheduler matched this
    session's capabilities to an `agent` node's `required_capabilities`
    and transitioned the step to `running`. Act on the step, then call
    `weave agent-session task-complete` to report outputs.
    """
    client = _client()
    try:
        resp = client.get(f"/agent-sessions/{session_id}/tasks")
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    tasks = resp.get("tasks", [])
    if not tasks:
        _console.print("[dim]No assigned tasks.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Step ID")
    table.add_column("Workflow")
    table.add_column("Node")
    table.add_column("Kind")
    table.add_column("Status")
    for t in tasks:
        table.add_row(
            t.get("id", ""),
            t.get("workflow_name", "") or "",
            t.get("node_id", ""),
            t.get("node_kind", ""),
            t.get("status", ""),
        )
    _console.print(table)


@app.command("task-complete")
def task_complete_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID (UUID)")],
    step_id: Annotated[str, typer.Argument(help="Step ID (UUID)")],
    outcome: Annotated[str, typer.Option("--outcome", help="done | failed")] = "done",
    outputs_file: Annotated[Optional[str], typer.Option("--outputs-file", help="Path to JSON/YAML file with outputs")] = None,
    output_kv: Annotated[Optional[list[str]], typer.Option("--output", help="key=value output (repeatable)")] = None,
    error_reason: Annotated[Optional[str], typer.Option("--error-reason")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark an assigned task done or failed. Emits a work-chain event;
    the scheduler advances the workflow on its next tick."""
    import re as _re

    body: dict = {"outcome": outcome}

    outputs: dict = {}
    if outputs_file:
        from pathlib import Path as _P
        p = _P(outputs_file)
        if not p.exists():
            _console.print(f"[red]No such file:[/red] {outputs_file}")
            raise typer.Exit(1)
        text = p.read_text(encoding="utf-8")
        import yaml as _yaml
        if outputs_file.endswith(".json"):
            outputs = json.loads(text)
        else:
            outputs = _yaml.safe_load(text) or {}
    for kv in output_kv or []:
        if "=" not in kv:
            _console.print(f"[red]--output expects key=value; got:[/red] {kv}")
            raise typer.Exit(1)
        k, _, v = kv.partition("=")
        outputs[k] = v
    if outputs:
        body["outputs"] = outputs
    if outcome == "failed":
        body["error"] = {"reason": error_reason or "agent-reported failure"}

    client = _client()
    try:
        resp = client.post(
            f"/agent-sessions/{session_id}/tasks/{step_id}/complete", body
        )
    except PowerloomApiError as e:
        _console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if json_out:
        typer.echo(json.dumps(resp, indent=2, default=str))
        return

    _console.print(
        f"[green]Step {resp.get('node_id')}[/green] marked {resp.get('status')}."
    )
