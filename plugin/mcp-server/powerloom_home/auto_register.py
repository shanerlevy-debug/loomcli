"""Auto-register a coordination session on Claude Code MCP startup.

Agent Lifecycle UX M2-P3 (loomcli plugins side; pairs with the
M2-P1 platform changes + M2-P2 loomcli changes shipped earlier
2026-04-30).

When the powerloom_home MCP server boots, it checks for a Claude
Code deployment credential at ``~/.config/powerloom/deployment-claude_code.json``
(written by ``weave register --token=...`` against an agent created
from the ``claude_code_session`` template). If found, it auto-mints
an ``agent_session`` row tied to the deployment, scoped to the
current working directory.

This is **Option B** from the M2 design: plugins drop the
requirement to call ``weave agent-session register`` explicitly.
The session opens automatically; actions are attributed to a
``claude_code:<scope>`` sub-principal; the UI's Deployments tab
shows "active session" for the host.

When no credential exists, this module is a no-op — the legacy PAT
flow (``weave agent-session register``) still works and is the only
way to register a session on dev / no-deployment hosts.

The end-of-session POST is best-effort. If the operator kills
Claude Code with SIGKILL, the session row's last-heartbeat staleness
flips its computed status to 'offline' eventually (same compute_status
freshness rules used for deployments).
"""
from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

import httpx


log = logging.getLogger("powerloom_home.auto_register")


# Reads from where loomcli's M2-P2 ``weave register`` writes for
# Claude Code deployments. Mirrors loomcli/loomcli/config.py
# (deployment_credential_path scope='user', kind='claude_code'). We
# read the file directly here rather than importing loomcli because
# the MCP server can be installed without loomcli on PATH.
def _credential_paths() -> list[Path]:
    """Plausible locations for the Claude Code deployment credential.

    Mirrors loomcli's ``deployment_credential_path(scope='user',
    kind='claude_code')`` resolution + the legacy ``deployment.json``
    fallback. Returns a list to try in order.
    """
    base = _config_dir()
    return [
        base / "deployment-claude_code.json",
        # Legacy v0.7.12 single-file shape — still honored for
        # operators who registered before M2 landed.
        base / "deployment.json",
        Path("/etc/powerloom/deployment-claude_code.json"),
        Path("/etc/powerloom/deployment.json"),
    ]


def _config_dir() -> Path:
    """Per-user config directory, matching platformdirs/loomcli's
    POWERLOOM_HOME convention."""
    override = os.environ.get("POWERLOOM_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "powerloom" / "powerloom"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "powerloom"
    return Path.home() / ".config" / "powerloom"


def read_deployment_credential() -> dict[str, Any] | None:
    """Try each candidate path; return the first parseable credential.

    Returns None when no credential exists (the operator hasn't run
    ``weave register`` for a Claude Code deployment, or the file is
    unreadable / malformed). Caller treats None as "no auto-register;
    fall through to the legacy PAT flow."
    """
    for path in _credential_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        # If it's the legacy single-file or wrong-kind, only accept
        # when its credential_kind matches Claude Code (or is unset
        # for back-compat).
        kind = payload.get("credential_kind")
        if kind not in (None, "default", "claude_code"):
            continue
        if not payload.get("deployment_token") or not payload.get("agent_id"):
            continue
        return payload
    return None


def _scope_from_cwd() -> str:
    """Derive a scope slug from the current working directory.

    Defaults to the leaf directory name. Operators can override via
    the ``POWERLOOM_SESSION_SCOPE`` env var when the cwd doesn't
    capture intent (e.g. running Claude Code on a multi-repo
    monorepo where the session is project-bounded, not repo-bounded).
    """
    override = os.environ.get("POWERLOOM_SESSION_SCOPE")
    if override and override.strip():
        return override.strip()
    cwd = Path.cwd()
    return cwd.name or "claude-code"


def _hostname() -> str:
    return socket.gethostname() or "unknown-host"


def open_session(
    credential: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Mint an agent_session against the platform.

    Returns the session row dict on success (so caller can stash
    session_id for later /end). Returns None on any failure — auto-
    register is best-effort and a network blip shouldn't crash the
    MCP server.
    """
    api_base = (credential.get("api_base_url") or "https://api.powerloom.org").rstrip("/")
    deployment_token = credential["deployment_token"]
    agent_id = credential["agent_id"]
    scope = _scope_from_cwd()
    body = {
        "agent_id": agent_id,
        "scope": scope,
        "actor_kind": "claude_code",
        "summary": f"Claude Code session in {scope} on {_hostname()}",
        "deployment_id": credential.get("deployment_id"),
    }
    try:
        with httpx.Client(base_url=api_base, timeout=timeout) as client:
            response = client.post(
                "/agent-sessions",
                json=body,
                headers={"Authorization": f"Bearer {deployment_token}"},
            )
    except httpx.HTTPError as e:
        log.warning("auto-register: network error contacting %s: %s", api_base, e)
        return None

    if response.status_code == 401:
        log.warning(
            "auto-register: deployment_token rejected (401). The deployment may "
            "have been archived; falling back to PAT flow. Re-pair this host "
            "with `weave register --token=...` if you want auto-register back."
        )
        return None
    if response.status_code >= 400:
        log.warning(
            "auto-register: HTTP %s from %s: %s",
            response.status_code,
            api_base,
            response.text[:200],
        )
        return None
    try:
        return response.json()
    except ValueError:
        log.warning("auto-register: non-JSON response from %s", api_base)
        return None


def close_session(
    credential: dict[str, Any],
    session_id: str,
    *,
    timeout: float = 5.0,
) -> None:
    """Best-effort POST /agent-sessions/{id}/end.

    Failures are swallowed — if the MCP server is shutting down, we
    don't want a network error to mask the actual exit. The session
    will eventually flip to 'offline' via heartbeat-staleness.
    """
    api_base = (credential.get("api_base_url") or "https://api.powerloom.org").rstrip("/")
    deployment_token = credential["deployment_token"]
    try:
        with httpx.Client(base_url=api_base, timeout=timeout) as client:
            client.post(
                f"/agent-sessions/{session_id}/end",
                headers={"Authorization": f"Bearer {deployment_token}"},
            )
    except httpx.HTTPError as e:
        log.debug("close-session: network error (ignored): %s", e)
