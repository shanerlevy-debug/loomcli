"""Tests for `weave enforce-check` (workstream 0c).

Covers:
  * Pattern file parsing (comments, blanks, missing file).
  * Glob matching for the canonical CC pattern shape `<ToolName>(<glob>)`.
  * `--mode warn` always exits 0; `--mode block` exits 1 on any match.
  * CLI args path (--tool-name + --tool-args).
  * Stdin JSON path (Claude Code PreToolUse hook contract).
  * Tool-input flattening for known tools (Bash command, file_path).
  * Quiet mode suppresses output but preserves exit code.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.commands.enforce_check_cmd import (
    _flatten_tool_input,
    _match_pattern,
    _read_patterns,
)


runner = CliRunner()


# ---------------------------------------------------------------------------
# Unit tests for the pure helpers
# ---------------------------------------------------------------------------


def test_read_patterns_handles_comments_and_blanks(tmp_path: Path) -> None:
    pf = tmp_path / "patterns"
    pf.write_text(
        "# header comment\n"
        "Bash(git push origin main*)\n"
        "\n"
        "Read(/etc/**)\n"
        "  # leading-space comment\n",
        encoding="utf-8",
    )
    patterns = _read_patterns(pf)
    assert patterns == ["Bash(git push origin main*)", "Read(/etc/**)"]


def test_read_patterns_missing_file_returns_empty(tmp_path: Path) -> None:
    """Sync hasn't run yet → no patterns → no matches → no breakage."""
    assert _read_patterns(tmp_path / "does-not-exist") == []


def test_match_pattern_tool_and_glob() -> None:
    assert _match_pattern("Bash(git push origin main*)", "Bash", "git push origin main")
    assert _match_pattern("Bash(git push origin main*)", "Bash", "git push origin main --force")
    assert not _match_pattern("Bash(git push origin main*)", "Bash", "git pull")
    # Tool name mismatch → no match even if args glob matches.
    assert not _match_pattern("Bash(git push *)", "Read", "git push origin main")


def test_match_pattern_bare_wildcard_matches_everything() -> None:
    assert _match_pattern("*", "Bash", "anything")
    assert _match_pattern("*", "Read", "/etc/passwd")


def test_match_pattern_malformed_returns_false() -> None:
    assert not _match_pattern("Bash no parens", "Bash", "x")
    assert not _match_pattern("Bash())(", "Bash", "x")


def test_flatten_tool_input_bash_command() -> None:
    assert _flatten_tool_input("Bash", {"command": "git push"}) == "git push"


def test_flatten_tool_input_file_path_tools() -> None:
    for tool in ("Read", "Edit", "Write", "Glob", "Grep"):
        assert _flatten_tool_input(tool, {"file_path": "/etc/passwd"}) == "/etc/passwd"


def test_flatten_tool_input_unknown_tool_jsondumps() -> None:
    out = _flatten_tool_input("CustomTool", {"x": 1, "a": 2})
    # Sorted keys for deterministic globbing.
    assert out == '{"a":2,"x":1}'


# ---------------------------------------------------------------------------
# CLI integration tests via Typer's CliRunner
# ---------------------------------------------------------------------------


def _write_patterns(tmp_path: Path, lines: list[str]) -> Path:
    pf = tmp_path / "patterns"
    pf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pf


def test_warn_mode_match_writes_stderr_exits_zero(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, ["Bash(git push origin main*)"])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "warn",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "git push origin main",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "[powerloom warn]" in result.stderr
    assert "Bash(git push origin main)" in result.stderr
    assert "matches policy pattern: Bash(git push origin main*)" in result.stderr


def test_warn_mode_no_match_silent_exits_zero(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, ["Bash(rm -rf*)"])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "warn",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "git status",
        ],
    )
    assert result.exit_code == 0
    assert result.stderr == ""


def test_block_mode_match_exits_one_with_block_prefix(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, ["Bash(git push origin main*)"])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "block",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "git push origin main",
        ],
    )
    assert result.exit_code == 1
    assert "[powerloom block]" in result.stderr


def test_block_mode_no_match_exits_zero(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, ["Bash(rm -rf*)"])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "block",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "git status",
        ],
    )
    assert result.exit_code == 0


def test_quiet_suppresses_output_but_preserves_exit(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, ["Bash(*)"])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "block",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "anything",
            "--quiet",
        ],
    )
    assert result.exit_code == 1
    assert result.stderr == ""


def test_stdin_json_claude_code_contract(tmp_path: Path) -> None:
    """The Claude Code hook contract pipes JSON to stdin. With --tool-name
    omitted, the helper falls back to parsing stdin."""
    pf = _write_patterns(tmp_path, ["Bash(git push *)"])
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin main"},
    })
    result = runner.invoke(
        app,
        ["enforce-check", "--mode", "warn", "--pattern-file", str(pf)],
        input=payload,
    )
    assert result.exit_code == 0
    assert "[powerloom warn]" in result.stderr
    assert "Bash(git push origin main)" in result.stderr


def test_stdin_no_payload_no_op_exits_zero(tmp_path: Path) -> None:
    """A hook fired for an event with no tool call (e.g. SessionStart)
    should be a no-op, not crash."""
    pf = _write_patterns(tmp_path, ["Bash(*)"])
    result = runner.invoke(
        app,
        ["enforce-check", "--mode", "block", "--pattern-file", str(pf)],
        input="",
    )
    assert result.exit_code == 0


def test_stdin_non_json_silent_exits_zero(tmp_path: Path) -> None:
    """Codex / Gemini hooks may pipe a different shape. Don't fail on
    unparseable stdin — just no-op."""
    pf = _write_patterns(tmp_path, ["Bash(*)"])
    result = runner.invoke(
        app,
        ["enforce-check", "--mode", "block", "--pattern-file", str(pf)],
        input="not-json",
    )
    assert result.exit_code == 0


def test_missing_pattern_file_no_op(tmp_path: Path) -> None:
    """A pre-sync deployment shouldn't break tool calls just because the
    pattern file doesn't exist yet."""
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "block",
            "--pattern-file", str(tmp_path / "nope"),
            "--tool-name", "Bash",
            "--tool-args", "anything",
        ],
    )
    assert result.exit_code == 0


def test_invalid_mode_exits_two(tmp_path: Path) -> None:
    pf = _write_patterns(tmp_path, [])
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "panic",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "x",
        ],
    )
    assert result.exit_code == 2
    assert "must be 'warn' or 'block'" in result.stderr


def test_multiple_patterns_match_all_reported(tmp_path: Path) -> None:
    pf = _write_patterns(
        tmp_path,
        ["Bash(git push *)", "Bash(* origin main*)"],
    )
    result = runner.invoke(
        app,
        [
            "enforce-check",
            "--mode", "warn",
            "--pattern-file", str(pf),
            "--tool-name", "Bash",
            "--tool-args", "git push origin main",
        ],
    )
    assert result.exit_code == 0
    # Both patterns should appear on separate lines.
    assert result.stderr.count("[powerloom warn]") == 2


def test_help_lists_enforce_check_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "enforce-check" in result.stdout
