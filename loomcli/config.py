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
import tomllib
from dataclasses import dataclass, field
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


def active_subprincipal_file(scope: str) -> Path:
    """Return the path to the per-scope active-sub-principal cache file.

    Written by `weave agent-session register`, read by
    `loomcli.commands.thread_cmd._build_session_attribution` as a
    fallback when `POWERLOOM_ACTIVE_SUBPRINCIPAL_ID` env var is unset.

    Per-scope (not per-branch) so the same sub-principal stays the same
    across `cd`s into multiple worktrees of the same scope. The scope
    string follows the `session/<scope>-<yyyymmdd>` branch convention
    minus the `session/` prefix.

    File contains a single line: the sub-principal UUID. Anything else
    (whitespace, comments) is tolerated by the reader (it strips +
    treats invalid UUID as "no cached sub-principal").
    """
    safe_scope = scope.replace("/", "_").replace("\\", "_")
    return _base_dir() / f"active-subprincipal-{safe_scope}.txt"


def config_file() -> Path:
    """Return the path to the optional config.toml (re-read each call)."""
    return _base_dir() / "config.toml"


@dataclass
class ProfileConfig:
    api_base_url: str | None = None
    default_org: str | None = None
    default_ou: str | None = None
    default_agent: str | None = None
    default_project: str | None = None
    default_runtime: str | None = None
    default_model: str | None = None
    output: str | None = None


@dataclass
class CliConfig:
    active_profile: str = "default"
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    """What the CLI reads at runtime: env vars, credentials, and profile defaults."""

    api_base_url: str
    """Base URL of the Powerloom control plane. Defaults to
    https://api.powerloom.org (the hosted production cluster).
    Override via POWERLOOM_API_BASE_URL or --api-url for local
    dev against docker-compose (set to http://localhost:8000)."""

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

    active_profile: str = "default"
    default_org: str | None = None
    default_ou: str | None = None
    default_agent: str | None = None
    default_project: str | None = None
    default_runtime: str | None = None
    default_model: str | None = None
    default_output: str | None = None


def load_runtime_config() -> RuntimeConfig:
    cli_cfg = load_cli_config()
    profile = cli_cfg.profiles.get(cli_cfg.active_profile, ProfileConfig())
    # Default to the hosted production cluster. Local dev users point
    # this at docker-compose via POWERLOOM_API_BASE_URL=http://localhost:8000
    # or --api-url. Changed in v0.6.0rc2 after pip-installed `weave login`
    # on production kept trying to hit localhost:8000 out of the box.
    api_url = (
        os.environ.get("POWERLOOM_API_BASE_URL")
        or profile.api_base_url
        or "https://api.powerloom.org"
    )
    token = _read_credentials_file()
    # v0.5.3 — approval-gate justification, via env var (or set at runtime
    # by the CLI root callback from the --justification flag).
    justification = os.environ.get("POWERLOOM_APPROVAL_JUSTIFICATION") or None
    return RuntimeConfig(
        api_base_url=api_url.rstrip("/"),
        access_token=token,
        approval_justification=justification,
        active_profile=cli_cfg.active_profile,
        default_org=profile.default_org,
        default_ou=profile.default_ou,
        default_agent=profile.default_agent,
        default_project=profile.default_project,
        default_runtime=profile.default_runtime,
        default_model=profile.default_model,
        default_output=profile.output,
    )


PROFILE_FIELDS = (
    "api_base_url",
    "default_org",
    "default_ou",
    "default_agent",
    "default_project",
    "default_runtime",
    "default_model",
    "output",
)


def load_cli_config() -> CliConfig:
    path = config_file()
    if not path.exists():
        return CliConfig(profiles={"default": ProfileConfig()})
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return CliConfig(profiles={"default": ProfileConfig()})

    active_profile = str(data.get("active_profile") or "default")
    raw_profiles = data.get("profiles") or {}
    profiles: dict[str, ProfileConfig] = {}
    if isinstance(raw_profiles, dict):
        for name, raw in raw_profiles.items():
            if not isinstance(raw, dict):
                continue
            values = {
                key: str(raw[key])
                for key in PROFILE_FIELDS
                if raw.get(key) not in (None, "")
            }
            profiles[str(name)] = ProfileConfig(**values)
    profiles.setdefault("default", ProfileConfig())
    profiles.setdefault(active_profile, ProfileConfig())
    return CliConfig(active_profile=active_profile, profiles=profiles)


def save_cli_config(cfg: CliConfig) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_file().write_text(_render_config_toml(cfg), encoding="utf-8")


def update_profile(
    profile_name: str,
    values: dict[str, str | None],
    *,
    activate: bool = True,
) -> CliConfig:
    cfg = load_cli_config()
    if activate:
        cfg.active_profile = profile_name
    profile = cfg.profiles.setdefault(profile_name, ProfileConfig())
    for key, value in values.items():
        if key not in PROFILE_FIELDS:
            continue
        if value is not None:
            setattr(profile, key, value)
    save_cli_config(cfg)
    return cfg


def clear_profile_values(
    profile_name: str,
    fields_to_clear: list[str],
    *,
    activate: bool = True,
) -> CliConfig:
    cfg = load_cli_config()
    if activate:
        cfg.active_profile = profile_name
    profile = cfg.profiles.setdefault(profile_name, ProfileConfig())
    for key in fields_to_clear:
        if key in PROFILE_FIELDS:
            setattr(profile, key, None)
    save_cli_config(cfg)
    return cfg


def _render_config_toml(cfg: CliConfig) -> str:
    lines = [f"active_profile = {_toml_str(cfg.active_profile)}", ""]
    for name in sorted(cfg.profiles):
        profile = cfg.profiles[name]
        lines.append(f"[profiles.{_toml_key(name)}]")
        wrote = False
        for key in PROFILE_FIELDS:
            value = getattr(profile, key)
            if value is None:
                continue
            lines.append(f"{key} = {_toml_str(value)}")
            wrote = True
        if not wrote:
            lines.append("# no defaults set")
        lines.append("")
    return "\n".join(lines)


def _toml_key(value: str) -> str:
    if value.replace("_", "-").replace("-", "").isalnum():
        return value
    return _toml_str(value)


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
