"""Install runtime lifecycle hooks during ``weave open``.

Console-deployability sprint PR2 — thread ``ebedfd86``.

Powerloom's Sprint 7 PR2 (#362) shipped per-host shell hooks that fire
on agent session start/end and POST to the Powerloom API so downstream
listeners (Sprint 7 PR3 SessionEnd handler etc.) can react. Per Shane
2026-05-03 the canonical install path is via ``weave open`` — first-run
on a host installs the hooks, subsequent runs are no-ops.

Per-host (NOT per-session) install — hooks live at
``~/.codex/hooks/`` etc., not in the worktree.

Bundled approach: the hook scripts ship inside the loomcli package
under ``loomcli/_bundled_runtimes/{runtime}/hooks/``. When Powerloom's
runtime hook scripts change, a new loomcli release brings them along.
Single source of truth on the Powerloom side; loomcli ships a
snapshot.

Hook-script source: ``loomcli/_bundled_runtimes/{runtime}/hooks/`` —
loaded via ``importlib.resources`` so it works from a wheel + a dev
checkout. Mirror of the Powerloom repo's ``runtimes/{runtime}/hooks/``.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterable, Literal, Optional


# ---------------------------------------------------------------------------
# Runtime tag → host config dir + bundled-template subpath
# ---------------------------------------------------------------------------


# loomcli's launch_spec uses these tags. We map to the powerloom api's
# shorter names (codex / gemini / claude) for the bundled-template
# directory layout, plus the host config dir each runtime expects.
_RUNTIME_CONFIG = {
    "codex_cli": {
        "bundled": "codex",
        "host_dir": Path.home() / ".codex" / "hooks",
    },
    "gemini_cli": {
        "bundled": "gemini",
        # Antigravity / Gemini CLI both consume from this path per
        # the agents.toml convention.
        "host_dir": Path.home() / ".gemini" / "hooks",
    },
    "antigravity": {
        # Antigravity is Gemini's IDE shell — same hook substrate.
        "bundled": "gemini",
        "host_dir": Path.home() / ".gemini" / "hooks",
    },
    # claude_code → no-op. Claude's hook system uses settings.json
    # entries (see existing loomcli/_bundled_plugins/claude-code/hooks/).
    # Sprint 7 PR2 didn't ship an equivalent shell-script shim for
    # Claude because the native config covers it. install_runtime_hooks
    # returns an empty success result for this runtime.
}


_KNOWN_RUNTIMES = {"codex_cli", "gemini_cli", "antigravity", "claude_code"}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


HookAction = Literal["installed", "updated", "unchanged", "error", "skipped"]


@dataclass(frozen=True)
class HookFileResult:
    """Per-file outcome of a hook install pass."""

    path: Path
    action: HookAction
    detail: Optional[str] = None


@dataclass(frozen=True)
class InstallResult:
    """Roll-up of the install pass for one runtime."""

    runtime: str
    skipped_reason: Optional[str] = None  # set when whole runtime skipped
    files: tuple[HookFileResult, ...] = ()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_runtime_hooks(
    runtime: str,
    *,
    dry_run: bool = False,
    target_dir: Optional[Path] = None,
) -> InstallResult:
    """Install Powerloom runtime lifecycle hooks for ``runtime`` into
    the operator's host config dir.

    Idempotent: if the destination file already has the same content
    as the bundled template, action is ``unchanged``. Different
    content → ``updated``. Absent → ``installed``.

    Args:
        runtime: ``codex_cli`` / ``gemini_cli`` / ``antigravity`` /
            ``claude_code``. ``claude_code`` is a documented no-op
            (Claude's native hook system replaces this shim).
        dry_run: Don't write anything; return what *would* happen.
        target_dir: Override the host config dir (used by tests
            against tmpdirs). Defaults to ``~/.{runtime}/hooks/``.

    Returns:
        :class:`InstallResult`. ``skipped_reason`` is set when the
        whole call was a no-op (e.g. claude_code, unknown runtime,
        missing host config root).
    """
    if runtime not in _KNOWN_RUNTIMES:
        return InstallResult(
            runtime=runtime,
            skipped_reason=f"unknown_runtime: {runtime!r}",
        )

    config = _RUNTIME_CONFIG.get(runtime)
    if config is None:
        # claude_code falls here intentionally — it's known but has no
        # shell-script-shim install.
        return InstallResult(
            runtime=runtime,
            skipped_reason=(
                "no_shell_hook_substrate: this runtime configures "
                "lifecycle hooks via its native settings (e.g. "
                "Claude's settings.json), not via standalone shell "
                "scripts. Nothing to install."
            ),
        )

    bundled_subpath: str = config["bundled"]
    host_dir: Path = target_dir or config["host_dir"]

    # Don't create the host dir if its PARENT (e.g. ~/.codex/) doesn't
    # exist — that means the runtime isn't installed on this machine,
    # and creating an empty hooks/ dir under a non-existent runtime
    # config root would be silently broken.
    runtime_root = host_dir.parent
    if not runtime_root.exists():
        return InstallResult(
            runtime=runtime,
            skipped_reason=(
                f"runtime_not_detected: {runtime_root} does not "
                "exist. Install / run the runtime first, then "
                "re-run `weave open`."
            ),
        )

    # Walk the bundled template dir + apply each file.
    template_files = list(_iter_bundled_hooks(bundled_subpath))
    if not template_files:
        return InstallResult(
            runtime=runtime,
            skipped_reason=(
                f"no_templates: nothing bundled under "
                f"runtimes/{bundled_subpath}/hooks/. This is a "
                "loomcli packaging issue."
            ),
        )

    results: list[HookFileResult] = []
    for tmpl_name, tmpl_bytes in template_files:
        dst = host_dir / tmpl_name
        results.append(_apply_one(tmpl_bytes, dst, dry_run=dry_run))

    return InstallResult(runtime=runtime, files=tuple(results))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_bundled_hooks(
    runtime_subpath: str,
) -> Iterable[tuple[str, bytes]]:
    """Yield ``(filename, bytes)`` for every hook script bundled with
    loomcli under ``loomcli/_bundled_runtimes/{runtime_subpath}/hooks/``.

    Uses ``importlib.resources`` so it works whether loomcli is
    installed from a wheel or running from a dev checkout.
    """
    pkg_root = files("loomcli") / "_bundled_runtimes" / runtime_subpath / "hooks"
    if not pkg_root.is_dir():
        return
    for entry in pkg_root.iterdir():
        if not entry.name.endswith(".sh"):
            continue
        with as_file(entry) as path:
            yield entry.name, Path(path).read_bytes()


def _apply_one(
    payload: bytes, dst: Path, *, dry_run: bool
) -> HookFileResult:
    expected = hashlib.sha256(payload).hexdigest()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return HookFileResult(
            path=dst, action="error", detail=f"mkdir: {e}"
        )

    existing_action: HookAction = "installed"
    if dst.exists():
        try:
            existing_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
        except OSError as e:
            return HookFileResult(
                path=dst, action="error", detail=f"read: {e}"
            )
        if existing_hash == expected:
            return HookFileResult(path=dst, action="unchanged")
        existing_action = "updated"

    if dry_run:
        return HookFileResult(path=dst, action=existing_action)

    try:
        dst.write_bytes(payload)
        # Make executable (skip on Windows where chmod is mostly a
        # no-op anyway; the cygwin/git-bash path that runs the shell
        # script will respect the file mode if it can read it).
        if os.name != "nt":
            dst.chmod(0o755)
    except OSError as e:
        return HookFileResult(
            path=dst, action="error", detail=f"write: {e}"
        )
    return HookFileResult(path=dst, action=existing_action)
