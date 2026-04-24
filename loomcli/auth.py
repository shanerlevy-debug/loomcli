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
    clear_credentials,
    load_runtime_config,
    write_credentials,
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
