"""Filesystem paths + runtime config.

Cross-platform config dir via platformdirs:
  - Linux/macOS:  ~/.config/powerloom/
  - Windows:      %APPDATA%/Powerloom/

Single `config.toml` and a single `credentials` file — keeping the
surface minimal. POWERLOOM_HOME env var overrides the whole thing,
useful for tests and for admins running multiple environments (dev /
staging / prod) side by side.
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


CONFIG_DIR: Path = _base_dir()
CREDENTIALS_FILE: Path = CONFIG_DIR / "credentials"
CONFIG_FILE: Path = CONFIG_DIR / "config.toml"


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
    admin hasn't `weave auth login`ed yet."""

    request_timeout_seconds: float = 30.0


def load_runtime_config() -> RuntimeConfig:
    api_url = os.environ.get("POWERLOOM_API_BASE_URL", "http://localhost:8000")
    token = _read_credentials_file()
    return RuntimeConfig(api_base_url=api_url.rstrip("/"), access_token=token)


def _read_credentials_file() -> str | None:
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return CREDENTIALS_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_credentials(token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(token, encoding="utf-8")
    # Best-effort restrict permissions on POSIX. On Windows this is
    # a no-op; ACL-based lockdown is a Phase 6 concern.
    try:
        os.chmod(CREDENTIALS_FILE, 0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    try:
        CREDENTIALS_FILE.unlink(missing_ok=True)
    except OSError:
        pass
