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

v0.7.12 — deployment-bound credentials (Agent Lifecycle UX P3).
``deployment_credential_path()`` and ``read_deployment_credential()``
add a second auth path: ``/etc/powerloom/deployment.json`` (Linux
host-wide, written by ``weave register``) carries a deployment_token
plus the agent_id + api_base_url + initial runtime_config. The
daemon prefers this over POWERLOOM_ACCESS_TOKEN when available, so
operator-host deployments don't need to mint a long-lived PAT.
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Deployment-bound credentials (v0.7.12 — Agent Lifecycle UX P3)
# ---------------------------------------------------------------------------
#
# Operators running a self-hosted agent (the reconciler today, anything
# with runtime_type='self_hosted' tomorrow) pair their host with a
# deployment row on the platform via ``weave register --token=...``.
# The register command writes the resulting credential to
# ``/etc/powerloom/deployment.json`` (host-wide on Linux) so:
#
#   1. Multiple processes on the same host (e.g. a future sidecar +
#      the daemon itself) share one credential without re-registering.
#   2. Container restarts via the systemd unit + bind-mount survive
#      without touching the host-side bind path.
#   3. ``weave agent run`` resolves the agent_id + api_base_url from
#      the credential — no positional argument or env-var dance needed.
#
# Fallbacks:
#   * On hosts where /etc/powerloom isn't writable (laptops, dev
#     machines, containers without root), fall back to the per-user
#     XDG config_dir() / "deployment.json".
#   * When neither file is present, daemon falls back to the legacy
#     PAT path (POWERLOOM_ACCESS_TOKEN env var or credentials file).

DEPLOYMENT_CREDENTIAL_FILENAME = "deployment.json"
HOST_DEPLOYMENT_CREDENTIAL_PATH = Path("/etc/powerloom") / DEPLOYMENT_CREDENTIAL_FILENAME


def deployment_credential_path() -> Path:
    """Resolve the path where ``weave register`` writes (and the daemon
    reads) the deployment credential.

    Returns ``/etc/powerloom/deployment.json`` when ``/etc/powerloom``
    is writable (Linux host with sudo) — that's the single host-wide
    location operators expect. Falls back to the per-user
    ``config_dir()/deployment.json`` otherwise (dev hosts, mac laptops,
    containers without /etc write access).

    Re-evaluated on every call so a process that gains write access
    mid-run (rare, but explicit) picks up the new path.
    """
    primary = HOST_DEPLOYMENT_CREDENTIAL_PATH
    try:
        # If the directory exists and is writable, prefer it.
        if primary.parent.exists() and os.access(primary.parent, os.W_OK):
            return primary
        # If the directory doesn't exist but we can create it (i.e. we
        # have write on /etc), prefer it.
        if not primary.parent.exists() and os.access(primary.parent.parent, os.W_OK):
            return primary
    except OSError:
        pass
    return _base_dir() / DEPLOYMENT_CREDENTIAL_FILENAME


def read_deployment_credential() -> dict | None:
    """Return the parsed deployment credential, or None if absent.

    Looks at both the host-wide path (``/etc/powerloom/deployment.json``)
    and the per-user path (``config_dir()/deployment.json``). Returns
    the first one that parses as JSON. Daemon prefers this over the
    PAT path when available.

    Returns None on:
      * Neither file exists.
      * File exists but isn't readable (permission error).
      * File exists but isn't valid JSON.
    """
    seen: set[Path] = set()
    for path in (deployment_credential_path(), HOST_DEPLOYMENT_CREDENTIAL_PATH):
        if path in seen:
            continue
        seen.add(path)
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
        return payload
    return None


def write_deployment_credential(payload: dict) -> Path:
    """Write the deployment credential to the resolved path.

    Returns the path it was written to so the caller can surface it
    to the operator. The file gets 0600 perms on POSIX (best-effort —
    no-op on Windows where ACLs would be a separate concern).

    Raises OSError if the directory can't be created or the file
    can't be written. Caller (``weave register``) translates to a
    user-visible error.
    """
    path = deployment_credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Best-effort; Windows hits this every time and we don't care.
        pass
    return path


def clear_deployment_credential() -> None:
    """Remove every deployment credential we know about.

    Used by ``weave register --revoke`` (future) and by the daemon
    when it detects its own credential has been archived server-side
    (heartbeat returns 401 → daemon deletes the local file so the
    next ``weave register`` starts clean).
    """
    for path in (deployment_credential_path(), HOST_DEPLOYMENT_CREDENTIAL_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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
    """Resolve the access token used by ``weave`` requests.

    Resolution order:

      1. ``POWERLOOM_ACCESS_TOKEN`` env var. The 12-factor / containerized
         shape — operators running ``weave agent run`` in Docker /
         systemd / Kubernetes set the token via env injection without
         needing to manage a config-dir bind-mount.
      2. ``<POWERLOOM_HOME>/credentials`` file (the legacy desktop shape
         written by ``weave login`` on TTY hosts).

    Anything else returns ``None`` and the caller surfaces a "Not signed
    in" error.
    """
    env_token = os.environ.get("POWERLOOM_ACCESS_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
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


def is_json_output() -> bool:
    """Return True if the user or environment has requested JSON output."""
    return os.environ.get("POWERLOOM_FORMAT") == "json"


def is_agent_mode() -> bool:
    """Check if the CLI is running in AI Agent mode (env detection)."""
    from loomcli.cli import is_agent_mode as _is_agent_mode

    return _is_agent_mode()
