"""Bare-clone + git-worktree helpers for ``weave open``.

Strategy: one bare clone per Powerloom project at
``~/.powerloom/repos/<project_slug>.git/``. Each launch's worktree is
``git worktree add ~/.powerloom/worktrees/<scope>-<short_id>/`` against
that bare clone. Multiple launches against the same project share the
clone, saving disk + bandwidth; per-launch worktrees keep concurrent
sessions in their own checkout.

``short_id`` derives from ``launch_id`` (first 4 hex chars). Two
clicks on the same scope mint different launch_ids → different
short_ids → sibling worktrees, so paired-agent sessions don't collide.
A 5-min-cache resume of the *same* launch_id yields the *same*
short_id → idempotent worktree path → resume-on-interrupt.

Sprint: cli-weave-open-20260430. Thread: 864c55a4 — bare-clone +
git worktree add.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Sentinel — Windows MAX_PATH is 260; leave headroom for git's internal
# ``.git/refs/...`` paths under the worktree before we trip an OS error.
WINDOWS_PATH_WARN_THRESHOLD = 240


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GitOpError(RuntimeError):
    """Generic failure from a git subprocess invocation we couldn't recover from.

    Carries the underlying CalledProcessError if available so the caller
    can render a sensible CLI message + exit code.
    """

    def __init__(self, message: str, *, cmd: list[str], stderr: str = ""):
        super().__init__(message)
        self.cmd = cmd
        self.stderr = stderr


class CloneAuthError(GitOpError):
    """Git clone / fetch returned 401 / 403 — credentials issue.

    Distinguished from generic GitOpError so the CLI can surface the
    "configure git creds" hint when ``clone_auth.mode`` is
    ``local_credentials`` (per the org clone-auth-policy sprint).
    """


class WorktreePathInvalidError(GitOpError):
    """The target worktree path exists but isn't a usable git worktree.

    Usually means a previous ``weave open`` on this token was
    interrupted partway through clone or worktree creation. Surface
    actionable cleanup hint to the operator.
    """


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeaveOpenPaths:
    """Location of the per-user weave-open directories.

    Resolved once at ``open_cmd.run`` entry; passed to helpers so tests
    can substitute a tmp-dir root without monkeypatching ``Path.home``.
    """

    repos_root: Path
    worktrees_root: Path

    @classmethod
    def default(cls) -> "WeaveOpenPaths":
        base = Path.home() / ".powerloom"
        return cls(
            repos_root=base / "repos",
            worktrees_root=base / "worktrees",
        )

    @classmethod
    def with_worktree_root(cls, worktrees_root: Path) -> "WeaveOpenPaths":
        """Override the worktree root (drives the future ``--root`` flag)."""
        return cls(
            repos_root=Path.home() / ".powerloom" / "repos",
            worktrees_root=worktrees_root,
        )


def project_clone_path(paths: WeaveOpenPaths, project_slug: str) -> Path:
    return paths.repos_root / f"{project_slug}.git"


def worktree_path(
    paths: WeaveOpenPaths,
    scope_slug: str,
    short_id: str,
) -> Path:
    return paths.worktrees_root / f"{scope_slug}-{short_id}"


def short_id_from_launch_id(launch_id_hex: str) -> str:
    """First 4 hex chars of the launch UUID (no dashes).

    Two clicks → two launch_ids → two short_ids → sibling worktrees.
    A 5-min-cache resume returns the same launch_id → same short_id →
    same worktree path (idempotent resume-on-interrupt).
    """
    cleaned = launch_id_hex.replace("-", "")
    if len(cleaned) < 4:
        raise ValueError(f"launch_id too short to derive short_id: {launch_id_hex!r}")
    return cleaned[:4]


# ---------------------------------------------------------------------------
# Auth — wrap the clone URL with the launch-spec's clone_auth.token
# when present (server_minted mode). local_credentials passes through
# and relies on the user's credential helper / ssh agent.
# ---------------------------------------------------------------------------


def build_clone_url(repo_url: str, clone_auth_token: Optional[str]) -> str:
    """Return an HTTPS clone URL with token injected when present.

    GitHub App installation tokens use the ``x-access-token:<token>``
    user prefix; the same shape works for GitLab's ``oauth2:`` and
    Bitbucket's ``x-token-auth:`` flavours, which the engine should
    map at mint time before this layer ever sees a token.

    No-op when token is None — the URL passes through unchanged.
    """
    if not clone_auth_token:
        return repo_url
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        # SSH / git:// can't carry a token; let the URL pass through and
        # rely on the user's ssh agent. If we got here with mode=
        # server_minted + an SSH URL the engine misconfigured the spec.
        return repo_url
    netloc = f"x-access-token:{clone_auth_token}@{parsed.netloc}"
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def _run_git(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a git subprocess. Translates known auth failures to CloneAuthError."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        # GitHub returns 401 in stderr text; GitLab uses "Authentication
        # failed"; both contain enough signal to flag.
        haystack = stderr.lower()
        if any(
            needle in haystack
            for needle in (
                "401",
                "403",
                "authentication failed",
                "could not read username",
                "permission denied",
            )
        ):
            raise CloneAuthError(
                "Git authentication failed.",
                cmd=cmd,
                stderr=stderr,
            ) from exc
        raise GitOpError(
            f"git {cmd[1] if len(cmd) > 1 else ''} failed: {stderr or exc}",
            cmd=cmd,
            stderr=stderr,
        ) from exc


def assert_git_available() -> None:
    """Raise GitOpError if ``git`` isn't on PATH. Cheap pre-flight."""
    if shutil.which("git") is None:
        raise GitOpError(
            "`git` not found on PATH.",
            cmd=["git", "--version"],
        )


def ensure_bare_clone(
    paths: WeaveOpenPaths,
    project_slug: str,
    repo_url: str,
    clone_auth_token: Optional[str],
) -> Path:
    """Clone the project bare if missing; fetch origin if present.

    Returns the path to the bare clone. Idempotent — safe to call on
    every redeem; first call clones, subsequent calls just fetch.
    """
    paths.repos_root.mkdir(parents=True, exist_ok=True)
    target = project_clone_path(paths, project_slug)

    auth_url = build_clone_url(repo_url, clone_auth_token)

    if not target.exists():
        _run_git(
            ["git", "clone", "--bare", auth_url, str(target)],
            timeout=600,
        )
        return target

    # Already cloned — fetch latest. Fetching with a fresh clone-auth
    # URL transparently rotates the credential, so token expiry between
    # mints doesn't matter.
    _run_git(
        ["git", "remote", "set-url", "origin", auth_url],
        cwd=target,
    )
    _run_git(
        ["git", "fetch", "--prune", "origin"],
        cwd=target,
        timeout=300,
    )
    return target


def create_worktree(
    paths: WeaveOpenPaths,
    bare_clone: Path,
    scope_slug: str,
    branch_base: str,
    branch_name: str,
    short_id: str,
) -> Path:
    """``git worktree add`` a fresh checkout for this launch.

    Idempotent on the same ``(scope_slug, short_id)``: if the path
    already exists *and* is a registered worktree of this bare clone,
    return it as-is (resume-on-interrupt). If the path exists but is
    not a registered worktree, raise WorktreePathInvalidError so the
    operator can clean up.
    """
    paths.worktrees_root.mkdir(parents=True, exist_ok=True)
    target = worktree_path(paths, scope_slug, short_id)

    # Windows path-length pre-warn (caller decides whether to abort).
    # Surfacing as a positional return alongside the path would make the
    # signature awkward; instead the caller checks _path_length_warning
    # after success. Tests use _path_length_warning directly.

    if target.exists():
        if _is_registered_worktree(bare_clone, target):
            return target
        raise WorktreePathInvalidError(
            (
                f"Path {target} exists but is not a registered worktree of "
                f"{bare_clone}. Inspect manually; if it's leftover from a "
                "prior failed launch, remove it and retry."
            ),
            cmd=["git", "worktree", "list"],
        )

    _run_git(
        [
            "git",
            "worktree",
            "add",
            str(target),
            "-b",
            branch_name,
            f"origin/{branch_base}",
        ],
        cwd=bare_clone,
        timeout=120,
    )
    return target


def _is_registered_worktree(bare_clone: Path, candidate: Path) -> bool:
    """Return True iff ``candidate`` is in this bare clone's worktree list."""
    try:
        result = _run_git(
            ["git", "worktree", "list", "--porcelain"],
            cwd=bare_clone,
        )
    except GitOpError:
        return False
    candidate_resolved = str(candidate.resolve())
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            worktree_listed = line[len("worktree "):].strip()
            if Path(worktree_listed).resolve().__str__() == candidate_resolved:
                return True
    return False


def path_length_warning(target: Path) -> Optional[str]:
    """Return a warning string if the worktree path is uncomfortably long.

    Windows MAX_PATH is 260 chars; git's internal refs add ~20 on top of
    the worktree root, so we warn at 240. Returns None when the path is
    fine. Caller decides whether to print or escalate.
    """
    raw_len = len(str(target))
    if raw_len >= WINDOWS_PATH_WARN_THRESHOLD:
        return (
            f"Worktree path is {raw_len} chars; Windows users may hit "
            f"MAX_PATH (260) under git's internal refs. Consider a shorter "
            f"`weave config set worktree-root` or rename the scope."
        )
    return None
