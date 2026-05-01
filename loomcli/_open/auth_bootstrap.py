"""Auth bootstrap during ``weave open`` — auto-exchange launch_token for a 90d machine credential.

When a user pastes ``weave open <token>`` on a machine that has no
existing machine credential, this hook fires after redeem succeeds:

  1. Detect: ``config.read_machine_credential() is None``.
  2. Compute a stable machine fingerprint (sha256 of hostname:os:arch).
  3. POST ``/auth/machine-credentials/exchange`` (unauthenticated;
     launch_token in body is the auth).
  4. Persist the response to ``<XDG>/powerloom/auth.json``.

After this fires once on a host, subsequent weave commands resolve
the credential automatically via the resolution chain in
``config._read_credentials_file``. The user never has to type
``weave login`` (the milestone's design intent).

Sprint auth-bootstrap-20260430, thread fbb69176.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loomcli.auth import (
    compute_machine_fingerprint,
    exchange_machine_credential,
)
from loomcli.client import PowerloomApiError
from loomcli.config import RuntimeConfig, read_machine_credential


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of the auth-bootstrap step."""

    minted: bool
    """True iff a fresh machine credential was issued and persisted."""

    skipped_reason: Optional[str] = None
    """One of: 'already_have_machine_credential', None when minted."""

    error: Optional[str] = None
    """Human-readable failure summary when the exchange call errored.
    Bootstrap failures are always non-fatal — `weave open` continues."""

    credential_id: Optional[str] = None
    """Engine-side credential UUID (when minted)."""


def maybe_bootstrap_machine_credential(
    cfg: RuntimeConfig,
    *,
    launch_token: str,
    name: Optional[str] = None,
) -> BootstrapResult:
    """Exchange ``launch_token`` for a machine credential iff missing.

    Idempotent on already-have-credential (no-op + ``skipped_reason``
    populated). Engine errors translate to ``error`` field but never
    raise — losing this credential is a UX regression, not a launch
    failure (the user can always re-bootstrap from a fresh launch).

    The launch token is single-shot per launch on the engine side
    (sprint thread 39d15c62) — second call returns 410, which we
    surface as the "already exchanged elsewhere" error case.
    """
    if read_machine_credential() is not None:
        return BootstrapResult(
            minted=False, skipped_reason="already_have_machine_credential"
        )

    fingerprint = compute_machine_fingerprint()
    try:
        resp = exchange_machine_credential(
            cfg,
            launch_token=launch_token,
            machine_fingerprint=fingerprint,
            name=name,
        )
    except PowerloomApiError as exc:
        # 410 = already exchanged with a different machine; 404 = launch
        # not found / expired (token TTL elapsed mid-flow); other = engine
        # transient. All non-fatal here — `weave open` continues with
        # whatever auth the user already had.
        return BootstrapResult(
            minted=False,
            error=f"HTTP {exc.status_code}: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return BootstrapResult(minted=False, error=str(exc))

    return BootstrapResult(
        minted=True,
        credential_id=resp.get("credential_id"),
    )
