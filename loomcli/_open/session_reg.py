"""Agent-session registration + ``.powerloom-session.env`` writer.

After ``git worktree add`` lands the branch, this module:

  1. POSTs ``/agent-sessions`` to register the worktree with Powerloom
     coordination so other agents see this session as ``in_progress``
     on its scope.
  2. Writes ``<worktree>/.powerloom-session.env`` with the session id,
     scope, project, redeemed_at, runtime, branch — env vars the
     runtime + downstream weave commands read for context.
  3. Adds ``.powerloom-session.env`` to ``<worktree>/.gitignore`` so
     the file never gets committed to the session branch.

Sprint cli-weave-open-20260430, thread 5fab82ed.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.schema.launch_spec import LaunchSpec


SESSION_ENV_FILENAME = ".powerloom-session.env"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SessionRegisterError(RuntimeError):
    """``POST /agent-sessions`` failed in a way the CLI should surface.

    The wrapped PowerloomApiError carries the engine's status_code +
    body so the CLI can render an actionable hint based on the
    failure mode (overlap warning vs. validation vs. auth).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: Optional[dict] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


# ---------------------------------------------------------------------------
# /agent-sessions registration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredSession:
    """Slim view of the engine's AgentSessionRegisterOut.

    Only the fields ``weave open`` needs downstream — full response
    body is available on ``raw`` if a future thread needs it.
    """

    session_id: str
    session_slug: str
    work_chain_event_hash: Optional[str]
    overlap_warnings: list[dict]
    raw: dict


def _scope_summary_from_spec(spec: LaunchSpec) -> str:
    """Default scope_summary text when the modal didn't supply a friendly name."""
    if spec.scope.friendly_name:
        return spec.scope.friendly_name
    return f"weave open: {spec.scope.slug} ({spec.runtime})"


def register_agent_session(
    client: PowerloomClient,
    spec: LaunchSpec,
) -> RegisteredSession:
    """POST ``/agent-sessions`` for this launch's scope+branch.

    Pulls scope_summary, capabilities, runtime, branch_name from the
    spec. ``actor_id`` is left null so the engine resolves it from the
    authenticated principal (avoids us needing to also know the user
    email at this layer).

    Raises ``SessionRegisterError`` on any non-2xx; caller decides
    whether the bootstrap can proceed or must abort.
    """
    body = {
        "session_slug": spec.scope.slug,
        "scope_summary": _scope_summary_from_spec(spec),
        "branch_name": spec.scope.branch_name,
        "capabilities": list(spec.capabilities),
        "actor_kind": spec.runtime,
        "friendly_name": spec.scope.friendly_name,
    }
    try:
        resp = client.post("/agent-sessions", body)
    except PowerloomApiError as exc:
        raise SessionRegisterError(
            str(exc),
            status_code=exc.status_code,
            body=exc.body if isinstance(exc.body, dict) else None,
        ) from exc

    session = resp.get("session") or {}
    return RegisteredSession(
        session_id=session.get("id", ""),
        session_slug=session.get("session_slug", spec.scope.slug),
        work_chain_event_hash=resp.get("work_chain_event_hash"),
        overlap_warnings=list(resp.get("overlap_warnings") or []),
        raw=resp,
    )


# ---------------------------------------------------------------------------
# .powerloom-session.env writer
# ---------------------------------------------------------------------------


def _format_iso(value) -> str:
    """Stable ISO-8601 representation; tolerates str / datetime / None."""
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    return str(value)


def write_session_env_file(
    worktree: Path,
    session: RegisteredSession,
    spec: LaunchSpec,
) -> Path:
    """Write the ``.powerloom-session.env`` env file at the worktree root.

    Format is a flat ``KEY=VALUE`` env file (no quoting / escaping —
    values are well-known shapes from Powerloom: UUIDs, slugs,
    ISO-8601 timestamps, runtime enums).

    Idempotent: overwrites any prior file (resume-on-interrupt
    refreshes session_id / redeemed_at to the freshest values from
    the cache hit).
    """
    target = worktree / SESSION_ENV_FILENAME
    lines = [
        f"POWERLOOM_SESSION_ID={session.session_id}",
        f"POWERLOOM_SCOPE={spec.scope.slug}",
        f"POWERLOOM_PROJECT_ID={spec.project.id}",
        f"POWERLOOM_LAUNCH_TOKEN_REDEEMED_AT={_format_iso(spec.redeemed_at)}",
        f"POWERLOOM_RUNTIME={spec.runtime}",
        f"POWERLOOM_BRANCH={spec.scope.branch_name}",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def ensure_gitignore_entry(worktree: Path) -> bool:
    """Append ``.powerloom-session.env`` to ``<worktree>/.gitignore`` if missing.

    Returns True iff the file was modified (or created). Idempotent —
    re-running on an already-ignored worktree is a no-op.

    The cloned repo will usually already have a ``.gitignore``; this
    only appends the single line, never replaces or reorders.
    """
    gitignore = worktree / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8").splitlines()
        if SESSION_ENV_FILENAME in existing:
            return False
        # Preserve the existing file's trailing newline shape.
        sep = "" if existing and existing[-1] == "" else "\n"
        gitignore.write_text(
            gitignore.read_text(encoding="utf-8") + sep + SESSION_ENV_FILENAME + "\n",
            encoding="utf-8",
        )
        return True
    gitignore.write_text(SESSION_ENV_FILENAME + "\n", encoding="utf-8")
    return True
