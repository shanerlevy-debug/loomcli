"""Shared credential-file loader for the platform bridge.

Mirrors the resolution logic in ``powerloom_home.auto_register`` so the
two MCP servers can be installed/loaded independently — one might run
without the other present. Duplication is intentional; the credential
shape is small + stable, and a shared base import would create a
fan-out dependency that bites at packaging time.

Resolution order (first match wins):

  1. ``$POWERLOOM_HOME/deployment-claude_code.json`` (operator override)
  2. Per-OS user config dir:
     - Windows: ``%APPDATA%\\powerloom\\powerloom\\deployment-claude_code.json``
     - POSIX with ``$XDG_CONFIG_HOME``: ``$XDG_CONFIG_HOME/powerloom/deployment-claude_code.json``
     - POSIX fallback: ``~/.config/powerloom/deployment-claude_code.json``
  3. Legacy ``deployment.json`` at the same locations (pre-M2 v0.7.12 shape)
  4. ``/etc/powerloom/deployment-claude_code.json`` (system-wide)
  5. ``/etc/powerloom/deployment.json`` (system-wide legacy)

A credential is considered valid iff:

  * The file is JSON-parseable into a dict.
  * It has both ``deployment_token`` AND ``api_base_url``.
  * ``credential_kind`` is one of ``None`` / ``"default"`` / ``"claude_code"``
    (other kinds belong to other tooling and shouldn't be hijacked).

When no valid credential is found, ``read_deployment_credential()``
returns ``None`` — callers treat that as "operator hasn't paired this
host yet; expose zero tools and stay quiet."
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """Per-user config directory, matching loomcli's POWERLOOM_HOME convention."""
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


def _credential_paths() -> list[Path]:
    """Plausible locations for the Claude Code deployment credential."""
    base = _config_dir()
    return [
        base / "deployment-claude_code.json",
        base / "deployment.json",  # legacy shape
        Path("/etc/powerloom/deployment-claude_code.json"),
        Path("/etc/powerloom/deployment.json"),
    ]


def read_deployment_credential() -> dict[str, Any] | None:
    """Try each candidate path; return the first valid credential dict.

    A valid credential MUST have ``deployment_token`` AND
    ``api_base_url`` — those are the two fields the bridge needs to
    proxy. Returns None on no-match / unreadable / malformed / missing-
    fields. The bridge treats None as "fall through to empty tools."

    Why the explicit ``api_base_url`` check (the home server only
    requires ``agent_id``): the home server uses ``api_base_url`` for
    *session minting* (single endpoint, hardcoded path), so a missing
    URL falls back to ``https://api.powerloom.org``. The bridge is
    different — it forwards arbitrary MCP traffic, and the URL is part
    of the per-deployment scope. We refuse to assume a default platform
    here; if the credential doesn't carry the URL, we don't proxy.
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
        kind = payload.get("credential_kind")
        if kind not in (None, "default", "claude_code"):
            continue
        token = payload.get("deployment_token")
        api_base_url = payload.get("api_base_url")
        if not token or not api_base_url:
            continue
        return payload
    return None
