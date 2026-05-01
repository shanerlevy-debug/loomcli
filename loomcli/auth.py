"""Login / logout / whoami commands' shared plumbing.

Login flows (Tier 1, loomcli 0.5.1+):
  - Browser-paste (default):  `weave login`
      Opens https://powerloom.org/settings/access-tokens in the
      user's browser; user mints a PAT, copies it, pastes into the
      prompt. Token is verified against /me before being persisted.
  - Direct PAT injection:     `weave login --pat <token>`
      For scripts and CI. Same verification.
  - Dev-mode impersonation:   `weave login --dev-as <email>`
      For localhost/docker-compose development only. Requires
      POWERLOOM_AUTH_MODE=dev on the control plane.

Tier 2 (v056 proper) will add a fully automated OIDC device-code
flow — CLI opens browser, server displays user-code, user approves
via the Web UI, CLI polls until approved. That requires API-side
+ Web UI additions; out of scope for 0.5.1.

On success, the access token is written to config.CREDENTIALS_FILE.
Subsequent commands pick it up via config.load_runtime_config().
"""
from __future__ import annotations

import webbrowser
from typing import Optional

from loomcli.client import PowerloomApiError, PowerloomClient
from loomcli.config import (
    RuntimeConfig,
    auth_file,
    clear_credentials,
    clear_machine_credential,
    credentials_file,
    load_runtime_config,
    read_machine_credential,
    write_credentials,
    write_machine_credential,
)

# The Web UI URL where users mint PATs. Overridable via env var for
# local/staging development (e.g. http://localhost:3000).
DEFAULT_WEB_URL = "https://powerloom.org"
PAT_MINT_PATH = "/settings/access-tokens"


def resolve_web_url() -> str:
    """Return the base URL of the Web UI — env override or default."""
    import os

    return os.environ.get("POWERLOOM_WEB_URL", DEFAULT_WEB_URL).rstrip("/")


def login_dev(cfg: RuntimeConfig, email: str) -> None:
    """Log in via the dev-mode impersonation endpoint."""
    with PowerloomClient(cfg) as client:
        token = client.dev_login(email)
    write_credentials(token)


def login_pat(cfg: RuntimeConfig, token: str) -> dict:
    """Write a Personal Access Token directly + verify it by calling /me.

    On verification failure, the credential is cleared and the error
    propagates. Keeps the CLI state consistent — the file only contains
    tokens we've seen /me succeed against.
    """
    token = token.strip()
    if not token:
        raise PowerloomApiError(0, "empty token")
    write_credentials(token)
    # Re-load the config so the new token is picked up by PowerloomClient.
    fresh_cfg = load_runtime_config()
    try:
        with PowerloomClient(fresh_cfg) as client:
            me = client.get("/me")
    except PowerloomApiError:
        clear_credentials()
        raise
    return me


def login_browser(cfg: RuntimeConfig, *, open_browser: bool = True) -> dict:
    """Browser-paste flow — open the Web UI's PAT page, prompt for paste.

    Returns the /me response dict on success. Raises PowerloomApiError
    if verification fails (credentials are cleared in that case).

    `open_browser=False` suppresses the actual browser launch (used by
    tests and by users on headless systems who will open it themselves).
    """
    import typer
    from rich.console import Console

    console = Console()
    web_url = resolve_web_url()
    pat_url = f"{web_url}{PAT_MINT_PATH}"

    console.print()
    console.print(f"[dim]Opening {pat_url} in your browser...[/dim]")
    if open_browser:
        try:
            webbrowser.open(pat_url)
        except Exception:
            pass  # Headless / no browser. The URL is already printed.

    console.print()
    console.print(f"[cyan]If your browser didn't open, visit:[/cyan]\n  {pat_url}")
    console.print()
    console.print("[cyan]1.[/cyan] Sign in to your Powerloom account (if prompted).")
    console.print("[cyan]2.[/cyan] Click [bold]Create new token[/bold].")
    console.print(
        "[cyan]3.[/cyan] Copy the token when it's displayed "
        "([yellow]it won't be shown again[/yellow])."
    )
    console.print()

    token = typer.prompt("Paste your token", hide_input=True).strip()
    if not token:
        raise PowerloomApiError(0, "empty token")

    return login_pat(cfg, token)


def login_oidc(cfg: RuntimeConfig) -> None:
    """Device-code OIDC flow. Stub — Tier 2 (v056 proper)."""
    raise NotImplementedError(
        "Fully-automated OIDC device-code login lands in v056. For now:\n"
        "  • weave login                          (browser paste — recommended)\n"
        "  • weave login --pat <token>            (non-interactive)\n"
        "  • weave login --dev-as <email>         (localhost dev mode)"
    )


def logout() -> None:
    clear_credentials()


def whoami(cfg: RuntimeConfig) -> dict:
    with PowerloomClient(cfg) as client:
        return client.get("/me")


# ---------------------------------------------------------------------------
# Credential origin — sprint auth-bootstrap-20260430 / thread fbb69176
# ---------------------------------------------------------------------------


CREDENTIAL_ORIGIN_ENV_VAR = "env:POWERLOOM_ACCESS_TOKEN"
CREDENTIAL_ORIGIN_MACHINE = "machine_credential"
CREDENTIAL_ORIGIN_PAT = "pat_file"
CREDENTIAL_ORIGIN_NONE = "none"


def credential_origin() -> dict:
    """Return where the active access token is coming from + meta for ``weave whoami``.

    Resolution order matches ``config._read_credentials_file``:
      1. ``POWERLOOM_ACCESS_TOKEN`` env var
      2. ``auth.json`` (machine credential, sprint auth-bootstrap-20260430)
      3. ``credentials`` file (legacy PAT)

    Returns a dict with at least ``origin`` (one of the four constants
    above) and ``path``/metadata when applicable. Caller renders for
    UI; no token leakage — the raw token is never returned here.
    """
    import os

    if (env := os.environ.get("POWERLOOM_ACCESS_TOKEN")) and env.strip():
        return {"origin": CREDENTIAL_ORIGIN_ENV_VAR}

    mcred = read_machine_credential()
    if mcred is not None:
        return {
            "origin": CREDENTIAL_ORIGIN_MACHINE,
            "path": str(auth_file()),
            "credential_id": mcred.get("credential_id"),
            "token_prefix": _safe_token_prefix(mcred.get("token")),
            "expires_at": mcred.get("expires_at"),
            "refresh_at": mcred.get("refresh_at"),
            "machine_fingerprint": mcred.get("machine_fingerprint"),
            "name": mcred.get("name"),
        }

    pat_path = credentials_file()
    if pat_path.exists():
        return {"origin": CREDENTIAL_ORIGIN_PAT, "path": str(pat_path)}

    return {"origin": CREDENTIAL_ORIGIN_NONE}


def _safe_token_prefix(token) -> str | None:
    if not isinstance(token, str) or len(token) < 12:
        return None
    return token[:12]


# ---------------------------------------------------------------------------
# Machine credential — exchange + load (sprint auth-bootstrap-20260430)
# ---------------------------------------------------------------------------


def load_machine_credential() -> Optional[str]:
    """Return the raw machine-credential token, or ``None`` if missing/expired.

    Thin wrapper around ``config.read_machine_credential`` for callers
    that only need the token (e.g. building a one-off ``PowerloomClient``
    when bootstrapping). Returns ``None`` instead of an expired token —
    callers should handle the ``None`` case as "user must re-launch".
    """
    cred = read_machine_credential()
    if cred is None:
        return None
    token = cred.get("token")
    return token if isinstance(token, str) and token else None


def exchange_machine_credential(
    cfg: RuntimeConfig,
    *,
    launch_token: str,
    machine_fingerprint: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """POST ``/auth/machine-credentials/exchange`` and persist to ``auth.json``.

    The exchange endpoint is unauthenticated (sprint
    auth-bootstrap-20260430 thread 39d15c62) — the launch_token in the
    request body is itself proof of identity. We rebuild a client with
    no bearer for this single call so any local PAT doesn't accidentally
    cross-contaminate the audit trail.

    On success, writes the credential to ``auth.json`` BEFORE returning
    so a Ctrl-C between this call returning and the caller acting on
    it doesn't lose the raw token. The response from the engine is the
    only chance to capture it.

    Returns the engine response dict (``credential_id``, ``token``,
    ``expires_at``, ``refresh_at``).
    """
    body: dict = {"launch_token": launch_token}
    if machine_fingerprint:
        body["machine_fingerprint"] = machine_fingerprint
    if name:
        body["name"] = name

    # Use a no-bearer client — exchange is unauthed. Reusing ``cfg``
    # would attach any existing PAT to the call, which is harmless but
    # adds noise to the audit log.
    no_auth_cfg = RuntimeConfig(
        api_base_url=cfg.api_base_url,
        access_token=None,
        approval_justification=None,
        active_profile=cfg.active_profile,
    )
    with PowerloomClient(no_auth_cfg) as client:
        resp = client.post("/auth/machine-credentials/exchange", body)

    # Persist immediately. Add issued_at locally — the engine doesn't
    # return it but we want a complete record on disk for ``whoami``.
    from datetime import datetime, timezone

    cred = {
        "credential_id": resp.get("credential_id"),
        "token": resp.get("token"),
        "expires_at": resp.get("expires_at"),
        "refresh_at": resp.get("refresh_at"),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "machine_fingerprint": machine_fingerprint,
        "name": name,
    }
    write_machine_credential(cred)
    return resp


def refresh_machine_credential(cfg: RuntimeConfig) -> dict | None:
    """Rotate the on-disk machine credential by calling the engine's refresh endpoint.

    Pre-condition: ``auth.json`` carries a usable credential whose
    ``refresh_at`` has passed but ``expires_at`` has not.

    POST ``/auth/machine-credentials/{credential_id}/refresh`` with the
    *current* credential as the bearer. Server validates ownership +
    rotates the token (new raw + hash + prefix; new expires_at = now +
    90d). On success we write the new credential to disk and return the
    response dict; on failure we return ``None`` and the caller logs
    but doesn't block the current request — the existing token is
    still valid until ``expires_at``.

    Sprint thread 648bca84.
    """
    cred = read_machine_credential()
    if cred is None or not cred.get("credential_id") or not cred.get("token"):
        return None

    refresh_cfg = RuntimeConfig(
        api_base_url=cfg.api_base_url,
        access_token=cred["token"],  # authenticate as the current credential
        approval_justification=None,
        active_profile=cfg.active_profile,
    )
    try:
        with PowerloomClient(refresh_cfg) as client:
            resp = client.post(
                f"/auth/machine-credentials/{cred['credential_id']}/refresh",
                {},
            )
    except PowerloomApiError:
        # 401 / 410 / network — refresh failed. Don't disrupt the
        # caller's request; the current token is still valid until
        # expires_at. Caller may log.
        return None
    except Exception:  # noqa: BLE001
        return None

    new_cred = {
        # credential_id never changes — refresh rotates the secret only.
        "credential_id": cred["credential_id"],
        "token": resp.get("token"),
        "expires_at": resp.get("expires_at"),
        "refresh_at": resp.get("refresh_at"),
        "issued_at": cred.get("issued_at"),  # original issuance preserved
        "machine_fingerprint": cred.get("machine_fingerprint"),
        "name": cred.get("name"),
    }
    write_machine_credential(new_cred)
    return resp


def is_in_refresh_window(cred: dict) -> bool:
    """Return True if ``now >= refresh_at`` (i.e. eligible for refresh).

    ``refresh_at = expires_at - 14d`` per the engine contract. Tolerates
    'Z' suffix and missing fields (returns False for missing — nothing
    to refresh). Used by the resolver to decide whether to kick off
    a refresh on credential read.
    """
    from datetime import datetime, timezone

    refresh_at_str = cred.get("refresh_at")
    if not refresh_at_str:
        return False
    try:
        refresh_at = datetime.fromisoformat(
            str(refresh_at_str).replace("Z", "+00:00")
        )
        if refresh_at.tzinfo is None:
            refresh_at = refresh_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) >= refresh_at


def expired_machine_credential_meta() -> dict | None:
    """Return metadata for a *recently* expired machine credential, if any.

    ``read_machine_credential`` returns ``None`` for both "missing"
    and "expired" — useful for the resolver, but the UX layer wants
    to distinguish so it can surface "your credential expired on X.
    Run `weave open <token>` to re-pair" rather than the generic
    "Not signed in" message.

    This helper reads the file *without* the expiry filter and
    returns the credential dict iff it's actually expired. Used by
    ``weave whoami`` and the once-per-process startup warning.
    """
    from datetime import datetime, timezone

    path = auth_file()
    if not path.exists():
        return None
    try:
        import json as _json

        cred = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None
    if not isinstance(cred, dict):
        return None
    expires_at_str = cred.get("expires_at")
    if not expires_at_str:
        return None
    try:
        expires_at = datetime.fromisoformat(
            str(expires_at_str).replace("Z", "+00:00")
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if expires_at <= datetime.now(timezone.utc):
        return cred
    return None


def compute_machine_fingerprint() -> str:
    """Return an opaque SHA-256 of ``<hostname>:<os>:<arch>``.

    Lets the user identify their machines in the revoke UI without
    exposing raw hostname/OS data server-side. Stable across
    invocations on the same host, distinct across machines.
    """
    import hashlib
    import platform
    import socket

    parts = [
        socket.gethostname() or "unknown",
        platform.system() or "unknown",
        platform.machine() or "unknown",
    ]
    raw = ":".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clear_all_credentials() -> None:
    """Remove both machine credential and legacy PAT credential files.

    Used by ``weave logout`` (in tandem with the existing PAT clear).
    Idempotent — missing files are no-ops.
    """
    clear_credentials()
    clear_machine_credential()


# ---------------------------------------------------------------------------
# PAT management (Tier 1, 0.5.1)
# ---------------------------------------------------------------------------


def pat_create(
    cfg: RuntimeConfig,
    *,
    name: str,
    expires_at: Optional[str] = None,
) -> dict:
    """Mint a new PAT via the API. Returns the full mint response — the
    caller is responsible for surfacing `raw_token` (shown once)."""
    body: dict = {"name": name}
    if expires_at:
        body["expires_at"] = expires_at
    with PowerloomClient(cfg) as client:
        return client.post("/users/me/personal-access-tokens", body)


def pat_list(cfg: RuntimeConfig) -> list[dict]:
    """List all PATs for the current user (metadata only — no raw tokens)."""
    with PowerloomClient(cfg) as client:
        resp = client.get("/users/me/personal-access-tokens")
    # API may return a list directly or a paginated wrapper.
    if isinstance(resp, list):
        return resp
    return resp.get("items", []) or resp.get("results", []) or []


def pat_revoke(cfg: RuntimeConfig, pat_id: str) -> None:
    """Revoke a PAT by its UUID."""
    with PowerloomClient(cfg) as client:
        client.delete(f"/users/me/personal-access-tokens/{pat_id}")
