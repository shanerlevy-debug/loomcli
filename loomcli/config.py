"""Filesystem paths + runtime config.

Cross-platform config dir via platformdirs:
  - Linux/macOS:  ~/.config/powerloom/
  - Windows:      %APPDATA%/powerloom/powerloom/

Single `config.toml` and a single `credentials` file — keeping the
surface minimal. POWERLOOM_HOME env var overrides the whole thing,
useful for tests and for admins running multiple environments (dev /
staging / prod) side by side.

All paths are computed **lazily on each access** via the `config_dir()`,
`credentials_file()`, and `config_file()` functions. Prior to v0.5.1
these were module-level constants evaluated at import time — which
meant setting POWERLOOM_HOME after importing loomcli.config had no
effect. That's fixed: every read of the credentials file re-checks
the env var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs


def _base_dir() -> Path:
    override = os.environ.get("POWERLOOM_HOME")
    if override:
        return Path(override)
    return Path(PlatformDirs("powerloom", "powerloom").user_config_dir)


def config_dir() -> Path:
    """Return the active config directory (re-read each call)."""
    return _base_dir()


def credentials_file() -> Path:
    """Return the path to the credentials file (re-read each call)."""
    return _base_dir() / "credentials"


def config_file() -> Path:
    """Return the path to the optional config.toml (re-read each call)."""
    return _base_dir() / "config.toml"


@dataclass
class RuntimeConfig:
    """What the CLI reads at runtime. A thin wrapper over env vars
    + the credentials file; no TOML parsing in v009 (config.toml is
    reserved for Phase 6+)."""

    api_base_url: str
    """Base URL of the Powerloom control plane. Defaults to
    localhost:8000 (Docker Compose) but typically overridden via
    POWERLOOM_API_BASE_URL for shared deployments."""

    access_token: str | None
    """Bearer token loaded from the credentials file. None if the
    admin hasn't `weave login`ed yet."""

    approval_justification: str | None = None
    """Optional justification string for approval-gated operations.
    When set, the client sends X-Approval-Justification on every
    request. Can be set via `--justification "..."` on the weave
    command line or POWERLOOM_APPROVAL_JUSTIFICATION env var.
    Added v0.5.3."""

    request_timeout_seconds: float = 30.0


def load_runtime_config() -> RuntimeConfig:
    api_url = os.environ.get("POWERLOOM_API_BASE_URL", "http://localhost:8000")
    token = _read_credentials_file()
    # v0.5.3 — approval-gate justification, via env var (or set at runtime
    # by the CLI root callback from the --justification flag).
    justification = os.environ.get("POWERLOOM_APPROVAL_JUSTIFICATION") or None
    return RuntimeConfig(
        api_base_url=api_url.rstrip("/"),
        access_token=token,
        approval_justification=justification,
    )


def _read_credentials_file() -> str | None:
    path = credentials_file()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_credentials(token: str) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = credentials_file()
    path.write_text(token, encoding="utf-8")
    # Best-effort restrict permissions on POSIX. On Windows this is
    # a no-op; ACL-based lockdown is a Phase 6 concern.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    try:
        credentials_file().unlink(missing_ok=True)
    except OSError:
        pass
