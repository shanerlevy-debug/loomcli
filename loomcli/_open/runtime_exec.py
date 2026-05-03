"""Runtime hand-off — replace the weave process with the agent's binary.

After clone + worktree + register + env-file land, this module's
``exec_runtime`` becomes the user's shell: ``cd <worktree>``, load
``.powerloom-session.env`` into the env, then ``execvpe`` the runtime
binary (``claude`` / ``codex`` / ``gemini``) so signals propagate
cleanly and the user's terminal becomes the agent.

For ``antigravity`` runtime, the launch flow doesn't exec a binary —
the local ``weave antigravity-worker`` daemon picks up the registered
session by polling ``/agent-sessions``. We surface an instructional
note and exit 0 so the user knows what's next.

Sprint cli-weave-open-20260430, thread 53573d73.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loomcli._open.session_reg import SESSION_ENV_FILENAME


# ---------------------------------------------------------------------------
# Runtime → binary mapping
# ---------------------------------------------------------------------------


# Runtime → CLI binary name lookup table. New runtimes added here +
# in LaunchRuntime literal in schema/launch_spec.py.
RUNTIME_BINARIES: dict[str, str] = {
    "claude_code": "claude",
    "codex_cli": "codex",
    "gemini_cli": "gemini",
    # antigravity is dispatched via the local worker daemon, not exec'd
    # directly. RUNTIME_BINARIES does NOT carry it; ANTIGRAVITY_RUNTIME
    # is checked separately in exec_runtime().
}

ANTIGRAVITY_RUNTIME = "antigravity"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RuntimeBinaryError(RuntimeError):
    """The runtime's binary is missing or the runtime is unknown."""

    def __init__(self, message: str, *, binary: Optional[str] = None):
        super().__init__(message)
        self.binary = binary


# ---------------------------------------------------------------------------
# Pre-flight + exec
# ---------------------------------------------------------------------------


def binary_for_runtime(runtime: str) -> Optional[str]:
    """Return the binary name for ``runtime``, or None for non-exec runtimes
    (currently just ``antigravity``).

    Raises ``RuntimeBinaryError`` for unknown runtimes — the engine
    schema should never let one through, so reaching here means a
    forward-compat drift the CLI didn't catch.
    """
    if runtime == ANTIGRAVITY_RUNTIME:
        return None
    if runtime not in RUNTIME_BINARIES:
        raise RuntimeBinaryError(f"Unknown runtime: {runtime!r}")
    return RUNTIME_BINARIES[runtime]


def assert_runtime_available(runtime: str) -> None:
    """Pre-flight: confirm the runtime's binary is on PATH.

    Cheap check that runs before clone so the user doesn't wait through
    a slow clone only to find ``claude`` isn't installed. No-op for
    ``antigravity`` (no binary; the worker daemon flow handles it).
    """
    binary = binary_for_runtime(runtime)
    if binary is None:
        return
    if shutil.which(binary) is None:
        raise RuntimeBinaryError(
            f"`{binary}` not found on PATH for runtime {runtime!r}.",
            binary=binary,
        )


def _load_session_env(worktree: Path) -> dict[str, str]:
    """Parse ``.powerloom-session.env`` into a dict (or empty if missing).

    Format is flat ``KEY=VALUE``; no quoting because all values are
    well-known shapes (UUIDs, slugs, ISO timestamps). Comments (``#``)
    and blank lines are skipped.
    """
    env_file = worktree / SESSION_ENV_FILENAME
    if not env_file.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def exec_runtime(
    worktree: Path,
    runtime: str,
    *,
    extra_env: Optional[dict[str, str]] = None,
) -> None:
    """``cd worktree && run <runtime_binary>`` with session env baked in.

    POSIX: replaces the current process via ``os.execvpe`` so signals
    (Ctrl-C, SIGTERM) propagate cleanly to the runtime.

    Windows: runs as a subprocess and ``sys.exit``s with the child's
    return code. ``os.execvpe`` on Windows is implemented as
    spawn-and-exit, which leaves Claude Code's "trust this folder?"
    + Codex's first-run consent prompts unable to read keyboard input
    because the parent shell closes the console before the child's
    TTY is ready.

    Returns only on the antigravity branch (which doesn't exec) or
    on error before exec/spawn.
    """
    if runtime == ANTIGRAVITY_RUNTIME:
        # Antigravity can't be exec'd directly — its IDE-side worker
        # picks up the registered session by polling /agent-sessions.
        # Caller renders the instructional banner.
        return

    binary = binary_for_runtime(runtime)
    assert binary is not None  # non-antigravity passed binary_for_runtime
    binary_path = shutil.which(binary)
    if binary_path is None:
        raise RuntimeBinaryError(
            f"`{binary}` disappeared from PATH between pre-flight and exec.",
            binary=binary,
        )

    env = dict(os.environ)
    env.update(_load_session_env(worktree))
    if extra_env:
        env.update(extra_env)

    # POSIX: replace the process so signals (Ctrl-C, SIGTERM) propagate
    # cleanly. The agent's binary becomes the user's shell.
    #
    # Windows: `os.execvpe` exists but Python's CRT implementation
    # spawns the child and exits the parent — the parent shell may
    # close the console handle before the child's TTY is ready, which
    # left interactive prompts (Claude Code's "trust this folder?",
    # Codex's first-run consent) unable to read keyboard input. Use
    # `subprocess.run` with inherited stdio instead — slower (we hold
    # a parent process for the lifetime of the agent) but the console
    # passes through cleanly so prompts work.
    if _is_windows():
        completed = subprocess.run([binary_path], cwd=str(worktree), env=env)
        sys.exit(completed.returncode)
    os.chdir(worktree)
    os.execvpe(binary, [binary], env)


def _is_windows() -> bool:
    """Indirection so tests can patch the platform check without mucking
    with the real ``sys.platform`` attribute (which leaks across tests
    and behaves unevenly under ``patch.object``)."""
    return sys.platform == "win32"
