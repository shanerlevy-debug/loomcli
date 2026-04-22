"""Login / logout / whoami commands' shared plumbing.

Login flows:
  - Dev mode:  `weave auth login --dev-as admin@dev.local`
                (control plane POWERLOOM_AUTH_MODE=dev required)
  - OIDC device code: deferred to Phase 6 — stub below returns a
    helpful error so admins know what to expect.

On success, the access token is written to config.CREDENTIALS_FILE.
Subsequent commands pick it up via config.load_runtime_config().
"""
from __future__ import annotations

from loomcli.client import PowerloomClient
from loomcli.config import RuntimeConfig, clear_credentials, write_credentials


def login_dev(cfg: RuntimeConfig, email: str) -> None:
    """Log in via the dev-mode impersonation endpoint."""
    with PowerloomClient(cfg) as client:
        token = client.dev_login(email)
    write_credentials(token)


def login_oidc(cfg: RuntimeConfig) -> None:
    """Device-code OIDC flow. Stub — Phase 6."""
    raise NotImplementedError(
        "OIDC device-code login is a Phase 6 deliverable. For now, use "
        "`weave auth login --dev-as <email>` against a control plane "
        "running POWERLOOM_AUTH_MODE=dev."
    )


def logout() -> None:
    clear_credentials()


def whoami(cfg: RuntimeConfig) -> dict:
    with PowerloomClient(cfg) as client:
        return client.get("/me")
