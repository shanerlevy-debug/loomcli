"""Aggregated pre-flight checks for ``weave open``.

Runs after redeem (we have the spec) but before clone (we haven't paid
the slow network round-trip yet). Goal: tell the user about every
fixable problem in one pass instead of dribbling them out one slow
failure at a time.

Categories:
  * **Required tooling** — git on PATH, runtime binary on PATH.
  * **Filesystem** — ``~/.powerloom/`` writable; warn on low disk space.
  * **Clone-auth** — when org policy is ``local_credentials``, detect
    that the user has *some* way to clone (``gh auth``, git credential
    helper, or SSH agent). Sprint thread ``79e876b1``.

Each check emits a ``PreflightCheck`` with status ``ok`` / ``warn`` /
``fail``. Caller renders one ``[ok|warn|fail] <check>`` line per check
in TTY mode or aggregates into a JSON dict in ``--quiet`` / piped
mode. Any ``fail`` rolls up into a ``PreflightFailed`` exception that
``open_cmd`` translates to a non-zero exit.

Sprint clone-auth-policy-20260430. Threads ``9233e176`` (tooling +
filesystem) and ``79e876b1`` (local-credentials detection).
"""
from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


PreflightStatus = Literal["ok", "warn", "fail"]


# Free-space threshold below which we warn (not fail). 500MB is enough
# for a small clone + worktree but tight for a Powerloom-sized repo.
LOW_DISK_WARN_THRESHOLD_BYTES = 500 * 1024 * 1024


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: PreflightStatus
    message: str
    fix_hint: Optional[str] = None

    @property
    def is_failure(self) -> bool:
        return self.status == "fail"


@dataclass(frozen=True)
class PreflightResult:
    checks: list[PreflightCheck]

    @property
    def any_failed(self) -> bool:
        return any(c.is_failure for c in self.checks)

    @property
    def failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.is_failure]

    def to_dict(self) -> dict:
        return {
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "fix_hint": c.fix_hint,
                }
                for c in self.checks
            ],
            "any_failed": self.any_failed,
        }


class PreflightFailed(RuntimeError):
    """One or more pre-flight checks reported ``fail``.

    Caller surfaces the per-check messages and exits non-zero.
    """

    def __init__(self, result: PreflightResult):
        super().__init__("preflight checks failed")
        self.result = result


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_git_on_path() -> PreflightCheck:
    if shutil.which("git"):
        return PreflightCheck(
            name="git",
            status="ok",
            message="git on PATH",
        )
    return PreflightCheck(
        name="git",
        status="fail",
        message="`git` not found on PATH.",
        fix_hint="Install git from https://git-scm.com/downloads",
    )


def check_runtime_on_path(runtime: str) -> PreflightCheck:
    """Validate that the runtime binary is on PATH for the chosen launch runtime.

    Antigravity has no binary — it runs via the local worker daemon.
    """
    from loomcli._open.runtime_exec import (
        ANTIGRAVITY_RUNTIME,
        RUNTIME_BINARIES,
    )

    if runtime == ANTIGRAVITY_RUNTIME:
        return PreflightCheck(
            name="runtime",
            status="ok",
            message="antigravity (no binary required)",
        )
    binary = RUNTIME_BINARIES.get(runtime)
    if binary is None:
        return PreflightCheck(
            name="runtime",
            status="fail",
            message=f"Unknown runtime {runtime!r} — engine drift?",
            fix_hint="Update loomcli (`pip install -U loomcli`).",
        )
    if shutil.which(binary):
        return PreflightCheck(
            name="runtime",
            status="ok",
            message=f"{runtime} → {binary} on PATH",
        )
    install_hint = {
        "claude": "https://docs.claude.com/en/docs/claude-code",
        "codex": "https://github.com/openai/codex",
        "gemini": "https://github.com/google/gemini-cli",
    }.get(binary, f"install `{binary}`")
    return PreflightCheck(
        name="runtime",
        status="fail",
        message=f"`{binary}` not found on PATH for runtime {runtime!r}.",
        fix_hint=f"Install: {install_hint}",
    )


def check_powerloom_writable(repos_root: Path, worktrees_root: Path) -> PreflightCheck:
    """Verify both Powerloom paths are writable (creating dirs as needed)."""
    for label, path in (("repos_root", repos_root), ("worktrees_root", worktrees_root)):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return PreflightCheck(
                name="filesystem",
                status="fail",
                message=f"{label} {path} is not writable: {exc}",
                fix_hint=(
                    f"Set `weave config set worktree-root <path>` to a "
                    f"writable location, or chmod {path.parent} to allow user write."
                ),
            )
        # Even on mkdir success the dir may be read-only; touch a sentinel.
        sentinel = path / ".weave-preflight-write-test"
        try:
            sentinel.write_text("", encoding="utf-8")
            sentinel.unlink()
        except OSError as exc:
            return PreflightCheck(
                name="filesystem",
                status="fail",
                message=f"{label} {path} exists but is read-only: {exc}",
                fix_hint=f"chmod u+w {path}",
            )
    return PreflightCheck(
        name="filesystem",
        status="ok",
        message=f"writable: {repos_root} + {worktrees_root}",
    )


def check_disk_space(worktrees_root: Path) -> PreflightCheck:
    """Warn (not fail) when worktree root has < 500MB free.

    The threshold catches "you literally can't clone" cases without
    spamming on every healthy machine. Hard fail would be hostile —
    the user might be opening a tiny project that fits in 100MB.
    """
    probe = worktrees_root if worktrees_root.exists() else worktrees_root.parent
    if not probe.exists():
        # Should be unreachable after `check_powerloom_writable`; defensive.
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Couldn't probe disk usage at {probe}",
            fix_hint=None,
        )
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as exc:
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Couldn't read disk usage at {probe}: {exc}",
            fix_hint=None,
        )
    free_mb = usage.free // (1024 * 1024)
    if usage.free < LOW_DISK_WARN_THRESHOLD_BYTES:
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Only {free_mb} MB free on {probe} — clone may fail.",
            fix_hint="Free space, or `weave config set worktree-root <path>` to a larger drive.",
        )
    return PreflightCheck(
        name="disk_space",
        status="ok",
        message=f"{free_mb} MB free on {probe}",
    )


# ---------------------------------------------------------------------------
# Local-credentials clone-auth detection — sprint thread 79e876b1
# ---------------------------------------------------------------------------


def _gh_auth_ok(host: str = "github.com") -> bool:
    """Return True if `gh auth status --hostname <host>` succeeds.

    Best-effort: any non-zero exit OR missing gh binary returns False.
    """
    if not shutil.which("gh"):
        return False
    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--hostname", host],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def _git_credential_helper_ok(host: str = "github.com") -> bool:
    """Return True if `git credential fill` returns a username/password for ``host``.

    Builds the standard credential-helper input (``protocol=https\\n
    host=<host>\\n``) and reads the response. Best-effort: any failure
    returns False.
    """
    if not shutil.which("git"):
        return False
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    body = result.stdout or ""
    return "username=" in body and "password=" in body


def _ssh_agent_has_github_key() -> bool:
    """Return True if the SSH agent has any loaded identity.

    A precise "this key is authorized for github.com" check requires
    actually attempting an auth, which is too slow + side-effecty for
    a pre-flight. Identity loaded → likely usable; we accept the
    false-positive risk for the much-better UX.
    """
    if not shutil.which("ssh-add"):
        return False
    try:
        result = subprocess.run(
            ["ssh-add", "-L"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    return bool((result.stdout or "").strip())


def check_local_clone_credentials(
    repo_url: str,
    *,
    admin_email: Optional[str] = None,
) -> PreflightCheck:
    """Detect a usable clone credential when org policy = local_credentials.

    Probes (in order): ``gh auth status``, ``git credential fill``,
    SSH agent identity. First success short-circuits.
    """
    # Extract host for the credential probes; default to github.com when
    # the URL's host can't be parsed (rare — would imply a malformed spec).
    host = "github.com"
    try:
        from urllib.parse import urlparse

        parsed = urlparse(repo_url)
        if parsed.hostname:
            host = parsed.hostname
    except Exception:  # noqa: BLE001
        pass

    if _gh_auth_ok(host):
        return PreflightCheck(
            name="local_clone_credentials",
            status="ok",
            message=f"`gh` authenticated for {host}",
        )
    if _git_credential_helper_ok(host):
        return PreflightCheck(
            name="local_clone_credentials",
            status="ok",
            message=f"git credential helper has {host} entry",
        )
    if _ssh_agent_has_github_key():
        return PreflightCheck(
            name="local_clone_credentials",
            status="ok",
            message="ssh-agent has at least one identity loaded",
        )

    admin_blurb = f"\nAdmin: {admin_email}" if admin_email else ""
    return PreflightCheck(
        name="local_clone_credentials",
        status="fail",
        message=(
            f"git credentials for {host} not found.\n"
            "Org policy requires local credentials "
            "(clone_auth_mode = local_credentials)."
        ),
        fix_hint=(
            f"Run `gh auth login` or configure a credential helper.{admin_blurb}"
        ),
    )


# ---------------------------------------------------------------------------
# Aggregated runner
# ---------------------------------------------------------------------------


def run_preflights(
    *,
    runtime: str,
    repos_root: Path,
    worktrees_root: Path,
    repo_url: str,
    clone_auth_mode: str,
    admin_email: Optional[str] = None,
) -> PreflightResult:
    """Run all applicable pre-flights for this launch.

    Collects results regardless of individual failures so the caller
    can render them all in one pass.
    """
    checks: list[PreflightCheck] = [
        check_git_on_path(),
        check_runtime_on_path(runtime),
        check_powerloom_writable(repos_root, worktrees_root),
        check_disk_space(worktrees_root),
    ]
    # Local-credentials check is opt-in based on org policy.
    if clone_auth_mode == "local_credentials":
        checks.append(check_local_clone_credentials(repo_url, admin_email=admin_email))
    return PreflightResult(checks=checks)
