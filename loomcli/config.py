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


def auth_file() -> Path:
    """Path to the machine-credential JSON file (sprint auth-bootstrap-20260430).

    Written by ``weave open`` after the launch_token is exchanged for a
    90d machine credential via POST /auth/machine-credentials/exchange.
    Read by every authed weave call (see ``_read_credentials_file``)
    when no env-token + no PAT credential is present.

    Linux/macOS: ``<XDG>/powerloom/auth.json``
    Windows: ``%LOCALAPPDATA%\\powerloom\\auth.json``
    (Both via ``_base_dir()`` → PlatformDirs.)
    """
    return _base_dir() / "auth.json"


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
# Deployment-bound credentials (v0.7.12 — M1-P3 / v0.7.13 — M2-P2)
# ---------------------------------------------------------------------------
#
# Operators pair a host with a deployment row on the platform via
# ``weave register --token=...``. The register command writes the
# resulting credential to one of two locations, decided by the server:
#
#   * **host scope** (default for daemon-style agents like the
#     reconciler) — ``/etc/powerloom/deployment.json``. Single
#     host-wide credential, shared across processes.
#   * **user scope** (default for IDE-style agents like Claude Code
#     / Gemini CLI / Codex CLI as of v0.7.13 / M2) —
#     ``<XDG>/powerloom/deployment-<kind>.json``. Per-user, per-IDE
#     credential. Multiple IDE deployments coexist on one machine
#     without colliding (one file per IDE kind).
#
# The server returns ``credential_scope`` + ``credential_kind`` on the
# register response (see ``RegisterResponse`` in the platform's
# ``schemas/agent_deployment.py``). Loomcli reads those fields and
# writes to the resolved path. ``deployment_credential_path(scope,
# kind)`` is the canonical resolver.
#
# Backwards compat: when a host has only the legacy single
# ``/etc/powerloom/deployment.json`` (M1 reconciler shape), the
# read path still finds it without a kind argument. New M2 hosts
# may have multiple per-kind files; ``read_deployment_credential(kind=...)``
# disambiguates.

DEPLOYMENT_CREDENTIAL_FILENAME = "deployment.json"
HOST_DEPLOYMENT_CREDENTIAL_PATH = Path("/etc/powerloom") / DEPLOYMENT_CREDENTIAL_FILENAME

# Recognized credential scopes. Stays in sync with the platform's
# ``derive_credential_scope`` (services/agent_deployments.py).
_VALID_SCOPES = ("host", "user")


def _user_credential_filename(kind: str) -> str:
    """The per-user credential filename for a given kind.

    Examples:
        kind="default" or "" -> "deployment.json" (back-compat)
        kind="claude_code"   -> "deployment-claude_code.json"
        kind="gemini_cli"    -> "deployment-gemini_cli.json"

    The "default" kind maps to the bare filename so a v0.7.12 host
    (which knew nothing about kinds) can be upgraded to v0.7.13 and
    still find its existing credential.
    """
    if not kind or kind == "default":
        return DEPLOYMENT_CREDENTIAL_FILENAME
    # Sanitize: kind comes from the server but defense-in-depth so a
    # malformed value can't traverse outside the config dir.
    safe = kind.replace("/", "_").replace("\\", "_").replace("..", "_")
    return f"deployment-{safe}.json"


def deployment_credential_path(
    scope: str = "host", kind: str = "default"
) -> Path:
    """Resolve where ``weave register`` writes (and clients read) the
    deployment credential.

    Args:
        scope: ``"host"`` (write /etc/powerloom/deployment.json) or
            ``"user"`` (write per-user XDG path). Server returns this
            on ``RegisterResponse.credential_scope``.
        kind: filename suffix for ``user`` scope. ``"default"`` means
            the bare ``deployment.json`` filename (M1 back-compat);
            ``"claude_code"`` etc. produces ``deployment-claude_code.json``.
            Server returns this on ``RegisterResponse.credential_kind``.

    Behavior:
        * ``scope="host"``: prefer /etc/powerloom/deployment.json when
          the directory is writable; fall back to the user XDG path
          otherwise. The kind argument is IGNORED for host scope (the
          host-wide location is single-tenant by design).
        * ``scope="user"``: always returns the user XDG path with the
          kind-suffixed filename. Doesn't touch /etc.

    Re-evaluated on every call so a process that gains write access
    mid-run picks up the new path.
    """
    if scope == "user":
        return _base_dir() / _user_credential_filename(kind)

    # scope=="host" path. Same as the v0.7.12 logic, kind ignored.
    primary = HOST_DEPLOYMENT_CREDENTIAL_PATH
    try:
        if primary.parent.exists() and os.access(primary.parent, os.W_OK):
            return primary
        if not primary.parent.exists() and os.access(primary.parent.parent, os.W_OK):
            return primary
    except OSError:
        pass
    return _base_dir() / DEPLOYMENT_CREDENTIAL_FILENAME


def list_deployment_credentials() -> dict[str, dict]:
    """Discover every readable deployment credential on this host.

    Returns a ``{kind: payload}`` dict. The "default" kind covers both
    the legacy host-wide ``/etc/powerloom/deployment.json`` AND the
    per-user fallback ``<XDG>/deployment.json`` (de-duplicated; if
    both exist the host-wide one wins).

    Used by:
      * The daemon (``weave agent run``) to find ITS credential
        when the host might have multiple (e.g. Claude Code +
        reconciler on the same laptop).
      * Plugin auto-register flows (M2-P3) to find their kind's
        credential.
      * ``weave register --force`` to surface a list of existing
        credentials before clobbering.

    Returns ``{}`` when no credentials exist.
    """
    found: dict[str, dict] = {}
    base = _base_dir()

    # 1) Host-wide credential (M1 reconciler shape).
    for path in (HOST_DEPLOYMENT_CREDENTIAL_PATH, base / DEPLOYMENT_CREDENTIAL_FILENAME):
        if "default" in found:
            break
        payload = _read_credential_file(path)
        if payload is not None:
            found["default"] = payload

    # 2) Per-kind user credentials (M2 IDE shape).
    if base.exists():
        try:
            for entry in base.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                if not name.startswith("deployment-") or not name.endswith(".json"):
                    continue
                kind = name[len("deployment-"):-len(".json")]
                if not kind or kind == "default" or kind in found:
                    continue
                payload = _read_credential_file(entry)
                if payload is not None:
                    found[kind] = payload
        except OSError:
            pass

    return found


def read_deployment_credential(kind: str | None = None) -> dict | None:
    """Return one parsed deployment credential.

    Args:
        kind: when provided, look ONLY for the credential with this
            exact kind (e.g. "claude_code"). Use this from plugin
            auto-register flows that know which IDE they are.
            When None (default), return the first credential found
            via the historical lookup order — host-wide first, then
            per-user default, then any per-kind file. Maintains
            backwards-compat with the v0.7.12 daemon.

    Returns None on:
      * Neither file exists.
      * File exists but isn't readable (permission error).
      * File exists but isn't valid JSON.
    """
    if kind is not None:
        # Exact match — for plugin auto-register flows.
        # Try the host-default name first if kind="default", else the
        # per-kind user file.
        if kind == "default":
            for path in (
                HOST_DEPLOYMENT_CREDENTIAL_PATH,
                _base_dir() / DEPLOYMENT_CREDENTIAL_FILENAME,
            ):
                payload = _read_credential_file(path)
                if payload is not None:
                    return payload
            return None
        path = _base_dir() / _user_credential_filename(kind)
        return _read_credential_file(path)

    # Legacy lookup (v0.7.12 daemon behavior). Host-default first,
    # then user-default, then any per-kind credential alphabetically.
    seen: set[Path] = set()
    for path in (deployment_credential_path(scope="host"), HOST_DEPLOYMENT_CREDENTIAL_PATH):
        if path in seen:
            continue
        seen.add(path)
        payload = _read_credential_file(path)
        if payload is not None:
            return payload

    # No host-default; return the first per-kind credential we find.
    for kind_name in sorted(list_deployment_credentials().keys()):
        if kind_name == "default":
            continue
        payload = _read_credential_file(
            _base_dir() / _user_credential_filename(kind_name)
        )
        if payload is not None:
            return payload
    return None


def _read_credential_file(path: Path) -> dict | None:
    """Parse a single credential file. Returns None on any read /
    parse failure (permission error, corrupt JSON, wrong shape)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_deployment_credential(
    payload: dict,
    *,
    scope: str = "host",
    kind: str = "default",
) -> Path:
    """Write the deployment credential to the path resolved by
    ``deployment_credential_path(scope, kind)``.

    Returns the path written. File gets 0600 perms on POSIX (best-
    effort; no-op on Windows where ACLs would be a separate concern).

    Raises OSError if the directory can't be created or the file
    can't be written. Caller (``weave register``) translates to a
    user-visible error.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {_VALID_SCOPES}")
    path = deployment_credential_path(scope=scope, kind=kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def clear_deployment_credential(kind: str | None = None) -> None:
    """Remove deployment credential file(s).

    Args:
        kind: when provided, remove only that kind's file (e.g.
            "claude_code" -> deletes ~/.config/powerloom/deployment-claude_code.json).
            When None, removes EVERY known deployment credential
            on the host. The latter is used by ``weave register
            --revoke`` (future) and by daemons that detect their
            credential has been archived server-side.
    """
    if kind is not None:
        try:
            (_base_dir() / _user_credential_filename(kind)).unlink(missing_ok=True)
        except OSError:
            pass
        if kind == "default":
            try:
                HOST_DEPLOYMENT_CREDENTIAL_PATH.unlink(missing_ok=True)
            except OSError:
                pass
        return

    # No kind: clear everything.
    paths_to_clear = [
        HOST_DEPLOYMENT_CREDENTIAL_PATH,
        _base_dir() / DEPLOYMENT_CREDENTIAL_FILENAME,
    ]
    for k in list_deployment_credentials():
        if k != "default":
            paths_to_clear.append(_base_dir() / _user_credential_filename(k))
    for path in paths_to_clear:
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
    # Sprint cli-weave-open-20260430: per-user override for the
    # ~/.powerloom/worktrees/ root used by `weave open`. Set via
    # `weave profile set --worktree-root <path>`.
    worktree_root: str | None = None


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
    worktree_root: str | None = None


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
        worktree_root=profile.worktree_root,
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
    "worktree_root",
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
      2. ``<XDG>/powerloom/auth.json`` machine credential — sprint
         auth-bootstrap-20260430 / v0.7.16+. ``weave open`` writes a 90d
         machine credential here on first launch; takes precedence over
         the legacy PAT credential so a paired-on-this-machine flow uses
         the freshest binding. When the credential is within its 14-day
         refresh window (``now >= refresh_at``), this resolver also
         kicks off an inline refresh that rotates the token in place
         before returning. Refresh failures are non-fatal — the
         current token is returned unchanged.
      3. ``<POWERLOOM_HOME>/credentials`` file (the legacy desktop shape
         written by ``weave login`` on TTY hosts).

    Anything else returns ``None`` and the caller surfaces a "Not signed
    in" error.
    """
    env_token = os.environ.get("POWERLOOM_ACCESS_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    mcred = read_machine_credential()
    if mcred is not None:
        # Refresh-on-read (sprint thread 648bca84). Stays inline to keep
        # the CLI single-process; concurrent refresh races aren't an
        # issue because most CLI invocations are single-action.
        # Disabled when POWERLOOM_DISABLE_AUTO_REFRESH=1 (test harness +
        # ops escape hatch for diagnosing token issues).
        if (
            not os.environ.get("POWERLOOM_DISABLE_AUTO_REFRESH")
            and _maybe_refresh_machine_credential(mcred)
        ):
            # Refresh succeeded; reload to pick up the rotated token.
            mcred = read_machine_credential() or mcred
        return mcred["token"]
    path = credentials_file()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _maybe_refresh_machine_credential(cred: dict) -> bool:
    """If ``cred`` is in its refresh window, fire an inline refresh.

    Returns True iff the refresh succeeded and ``auth.json`` was rotated.

    Local import to dodge the module cycle: ``loomcli.auth`` already
    imports from ``loomcli.config`` for ``RuntimeConfig`` + path helpers.
    """
    from loomcli.auth import is_in_refresh_window, refresh_machine_credential

    if not is_in_refresh_window(cred):
        return False
    # Build a minimal cfg locally — full ``load_runtime_config`` would
    # recurse into ``_read_credentials_file`` which is exactly the
    # call site. The refresh client only needs api_base_url + the
    # current bearer token (which is already in ``cred``).
    cfg = RuntimeConfig(
        api_base_url=(
            os.environ.get("POWERLOOM_API_BASE_URL") or "https://api.powerloom.org"
        ).rstrip("/"),
        access_token=None,
        approval_justification=None,
        active_profile="default",
    )
    return refresh_machine_credential(cfg) is not None


# ---------------------------------------------------------------------------
# Machine credentials — sprint auth-bootstrap-20260430 / v0.7.16+
# ---------------------------------------------------------------------------


def read_machine_credential() -> dict | None:
    """Return the active machine credential dict, or ``None`` if missing/expired.

    Shape on disk (written by ``write_machine_credential``):
        {
          "credential_id": "...",
          "token": "mcred_...",
          "expires_at": "ISO-8601",
          "refresh_at": "ISO-8601",
          "issued_at": "ISO-8601",
          "machine_fingerprint": "..." | null,
          "name": "..." | null
        }

    Returns ``None`` when:
      - the file is absent
      - the file is unreadable / malformed JSON
      - ``token`` field is missing
      - ``expires_at`` has passed (revoked-server-side returns ``None``
        too — caller hits 401 and the resolver returns null)

    Callers that need expiry / refresh metadata for UX purposes (e.g.
    ``weave whoami`` showing credential origin, sprint thread
    ``648bca84`` refresh-on-use) should use this directly. Hot-path
    auth lookup goes through ``_read_credentials_file`` which only
    surfaces the raw token.
    """
    path = auth_file()
    if not path.exists():
        return None
    try:
        import json
        from datetime import datetime, timezone

        raw = path.read_text(encoding="utf-8")
        cred = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cred, dict):
        return None
    if not cred.get("token"):
        return None
    expires_at_str = cred.get("expires_at")
    if expires_at_str:
        try:
            # Tolerate both 'Z' and '+00:00' suffixes.
            expires_at = datetime.fromisoformat(
                str(expires_at_str).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return None
        except (ValueError, TypeError):
            # Unparseable expires_at — treat as expired/invalid.
            return None
    return cred


def write_machine_credential(cred: dict) -> None:
    """Persist the machine credential to ``auth_file()`` with 0600 mode on POSIX.

    Caller is responsible for the dict's contract (see ``read_machine_credential``
    docstring). This function just JSON-serialises and writes — no
    schema validation. Wrong-shape data round-trips through reads as
    ``None``, which surfaces as "no auth" rather than a hard error.
    """
    import json

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = auth_file()
    path.write_text(json.dumps(cred, indent=2), encoding="utf-8")
    # Best-effort restrict permissions on POSIX. Windows ACL lockdown
    # is a separate concern; the file lands under the user's profile
    # dir which already has user-only read by default in most setups.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_machine_credential() -> None:
    """Remove the machine credential file. Idempotent on missing files."""
    try:
        auth_file().unlink(missing_ok=True)
    except OSError:
        pass


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
