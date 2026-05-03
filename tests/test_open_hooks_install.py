"""Tests for ``loomcli._open.hooks_install``.

console-deployability sprint PR2, thread ``ebedfd86``.

Coverage:

* `install_runtime_hooks(codex_cli)` — installs the bundled scripts
  into the target dir on first run; idempotent (`unchanged`) on
  re-run.
* `install_runtime_hooks(gemini_cli)` — same pattern, different
  target.
* `install_runtime_hooks(antigravity)` — maps to the Gemini hooks
  (Antigravity is Gemini's IDE shell).
* `install_runtime_hooks(claude_code)` — known-but-noop, returns a
  `skipped_reason` mentioning Claude's native hook system.
* Unknown runtime — returns `skipped_reason='unknown_runtime'`.
* Missing runtime root (e.g. `~/.codex/` doesn't exist) — returns
  `skipped_reason='runtime_not_detected'`.
* Drift detection — re-running with a hand-edited file detects the
  drift and reports `updated`.
* `dry_run=True` — reports the action that would happen but doesn't
  write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loomcli._open.hooks_install import (
    InstallResult,
    install_runtime_hooks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runtime_root_at(tmp: Path, name: str) -> Path:
    """Create a fake runtime root directory (e.g. tmp/.codex/) so the
    install_runtime_hooks runtime-detection check passes. Returns the
    hooks subdirectory path (not yet created)."""
    root = tmp / f".{name}"
    root.mkdir()
    return root / "hooks"


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_first_run_installs_all(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "codex")
    result = install_runtime_hooks("codex_cli", target_dir=target)
    assert result.skipped_reason is None
    assert len(result.files) >= 2  # session_start + session_end at minimum
    assert all(f.action == "installed" for f in result.files)
    for f in result.files:
        assert f.path.exists()
        assert f.path.stat().st_size > 0


def test_codex_idempotent_second_run(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "codex")
    install_runtime_hooks("codex_cli", target_dir=target)
    result2 = install_runtime_hooks("codex_cli", target_dir=target)
    assert all(f.action == "unchanged" for f in result2.files)


def test_codex_drift_detection(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "codex")
    install_runtime_hooks("codex_cli", target_dir=target)
    drifted = next(target.iterdir())
    drifted.write_text("# tampered\n", encoding="utf-8")
    result = install_runtime_hooks("codex_cli", target_dir=target)
    actions = {f.path.name: f.action for f in result.files}
    assert actions[drifted.name] == "updated"


# ---------------------------------------------------------------------------
# Gemini + Antigravity (both map to the gemini hooks)
# ---------------------------------------------------------------------------


def test_gemini_first_run_installs(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "gemini")
    result = install_runtime_hooks("gemini_cli", target_dir=target)
    assert result.skipped_reason is None
    assert all(f.action == "installed" for f in result.files)


def test_antigravity_uses_gemini_hooks(tmp_path: Path):
    """Antigravity is Gemini's IDE shell — same hook substrate."""
    target = _runtime_root_at(tmp_path, "gemini")
    result = install_runtime_hooks("antigravity", target_dir=target)
    assert result.skipped_reason is None
    assert len(result.files) >= 2


# ---------------------------------------------------------------------------
# Claude / unknown / missing-root
# ---------------------------------------------------------------------------


def test_claude_code_is_documented_noop():
    """Claude has its own native hook system. install_runtime_hooks
    skips with a clear reason."""
    result = install_runtime_hooks("claude_code")
    assert result.skipped_reason is not None
    assert "no_shell_hook_substrate" in result.skipped_reason
    assert result.files == ()


def test_unknown_runtime_skips():
    result = install_runtime_hooks("rogue_runtime")
    assert result.skipped_reason is not None
    assert "unknown_runtime" in result.skipped_reason


def test_missing_runtime_root_skips_with_friendly_reason(tmp_path: Path):
    """When the runtime root (e.g. ~/.codex/) doesn't exist, we don't
    create it — that means the runtime isn't installed on this host
    and we shouldn't pollute disk with a hooks/ dir under a
    non-existent runtime config root."""
    fake_target = tmp_path / "no-such-runtime-dir" / "hooks"
    result = install_runtime_hooks("codex_cli", target_dir=fake_target)
    assert result.skipped_reason is not None
    assert "runtime_not_detected" in result.skipped_reason
    assert not fake_target.exists()


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_reports_action_without_writing(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "codex")
    result = install_runtime_hooks(
        "codex_cli", dry_run=True, target_dir=target
    )
    # Reports `installed` (what would happen) but no files actually
    # land on disk.
    assert all(f.action == "installed" for f in result.files)
    assert not any(target.iterdir()) if target.exists() else True


# ---------------------------------------------------------------------------
# Returned InstallResult shape
# ---------------------------------------------------------------------------


def test_install_result_carries_runtime_tag(tmp_path: Path):
    target = _runtime_root_at(tmp_path, "codex")
    result = install_runtime_hooks("codex_cli", target_dir=target)
    assert isinstance(result, InstallResult)
    assert result.runtime == "codex_cli"
