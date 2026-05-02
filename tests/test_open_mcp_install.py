"""Tests for ``loomcli._open.mcp_install``.

Sprint skills-mcp-bootstrap-20260430, thread d240bfd7.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from loomcli._open.mcp_install import (
    PROJECT_MCP_FILENAME,
    McpInstallResult,
    install_mcp_config,
)
from loomcli.schema.launch_spec import LaunchSpec


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _spec_with_mcp(servers: list[dict]) -> LaunchSpec:
    base = {
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-05-01T00:00:00Z",
        "expires_at": "2026-05-01T00:15:00Z",
        "actor": {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "email": "x@y.com",
            "runtime": "claude_code",
        },
        "project": {
            "id": "33333333-3333-3333-3333-333333333333",
            "slug": "powerloom",
            "repo_url": "https://github.com/x/y.git",
            "default_branch": "main",
        },
        "scope": {
            "slug": "cc-test-20260501",
            "branch_base": "main",
            "branch_name": "session/cc-test-20260501",
        },
        "runtime": "claude_code",
        "skills": [],
        "capabilities": [],
        "clone_auth": {"mode": "server_minted"},
        "mcp_config": {"servers": servers},
        "rules_sync": [],
    }
    return LaunchSpec.model_validate(base)


@pytest.fixture
def no_global_mcp(tmp_path, monkeypatch):
    """Patch GLOBAL_MCP_CANDIDATES to a tmp dir with no files."""
    fake_globals = (
        tmp_path / "fake-claude-mcp.json",
        tmp_path / "fake-home-mcp.json",
    )
    monkeypatch.setattr(
        "loomcli._open.mcp_install.GLOBAL_MCP_CANDIDATES", fake_globals
    )
    yield fake_globals


# ---------------------------------------------------------------------------
# install_mcp_config — empty / fresh / global-skip / error paths
# ---------------------------------------------------------------------------


def test_empty_spec_returns_skipped(tmp_path, no_global_mcp) -> None:
    spec = _spec_with_mcp([])
    result = install_mcp_config(spec, tmp_path)
    assert result.written_path is None
    assert result.skipped_reason == "empty_spec"
    assert not (tmp_path / PROJECT_MCP_FILENAME).exists()


def test_fresh_worktree_writes_mcp_json(tmp_path, no_global_mcp) -> None:
    spec = _spec_with_mcp([
        {
            "name": "powerloom",
            "command": "weave",
            "args": ["mcp", "serve"],
            "env": {"POWERLOOM_API_BASE_URL": "https://api.example.com"},
            "attach_token": "sat_xxxxxxxx",
        },
    ])
    result = install_mcp_config(spec, tmp_path)
    assert result.written_path == tmp_path / PROJECT_MCP_FILENAME
    assert result.server_names == ["powerloom"]
    assert result.skipped_reason is None
    payload = json.loads(
        (tmp_path / PROJECT_MCP_FILENAME).read_text(encoding="utf-8"),
    )
    assert "mcpServers" in payload
    assert "powerloom" in payload["mcpServers"]
    server = payload["mcpServers"]["powerloom"]
    assert server["command"] == "weave"
    assert server["args"] == ["mcp", "serve"]
    assert server["env"]["POWERLOOM_API_BASE_URL"] == "https://api.example.com"
    # attach_token surfaces as POWERLOOM_ATTACH_TOKEN in the env block.
    assert server["env"]["POWERLOOM_ATTACH_TOKEN"] == "sat_xxxxxxxx"


def test_existing_env_in_spec_preserved(tmp_path, no_global_mcp) -> None:
    """attach_token is added without clobbering existing env keys."""
    spec = _spec_with_mcp([
        {
            "name": "powerloom",
            "command": "weave",
            "args": [],
            "env": {"POWERLOOM_ATTACH_TOKEN": "spec-supplied"},
            "attach_token": "would-be-clobber",
        },
    ])
    result = install_mcp_config(spec, tmp_path)
    payload = json.loads(result.written_path.read_text(encoding="utf-8"))
    # setdefault — existing env value wins over the attach_token.
    assert (
        payload["mcpServers"]["powerloom"]["env"]["POWERLOOM_ATTACH_TOKEN"]
        == "spec-supplied"
    )


def test_multiple_servers_in_spec(tmp_path, no_global_mcp) -> None:
    spec = _spec_with_mcp([
        {
            "name": "powerloom",
            "command": "weave",
            "args": ["mcp", "serve"],
            "env": {},
            "attach_token": "sat_a",
        },
        {
            "name": "external",
            "command": "node",
            "args": ["./server.js"],
            "env": {"NODE_ENV": "production"},
            "attach_token": None,
        },
    ])
    result = install_mcp_config(spec, tmp_path)
    payload = json.loads(result.written_path.read_text(encoding="utf-8"))
    assert set(payload["mcpServers"].keys()) == {"powerloom", "external"}
    # external server has no attach_token.
    assert (
        "POWERLOOM_ATTACH_TOKEN"
        not in payload["mcpServers"]["external"].get("env", {})
    )
    assert result.server_names == ["powerloom", "external"]


def test_no_args_omits_args_key(tmp_path, no_global_mcp) -> None:
    """Optional args left out when empty."""
    spec = _spec_with_mcp([
        {"name": "minimal", "command": "weave", "args": [], "env": {}},
    ])
    result = install_mcp_config(spec, tmp_path)
    payload = json.loads(result.written_path.read_text(encoding="utf-8"))
    assert "args" not in payload["mcpServers"]["minimal"]


def test_writes_file_with_0600_on_posix(tmp_path, no_global_mcp) -> None:
    if sys.platform == "win32":
        pytest.skip("0600 is a POSIX permission concept")
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    install_mcp_config(spec, tmp_path)
    mode = os.stat(tmp_path / PROJECT_MCP_FILENAME).st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# global-config detection — skip when user already has Powerloom registered
# ---------------------------------------------------------------------------


def test_skips_when_global_has_powerloom_server(tmp_path, monkeypatch) -> None:
    """Skip when a global config carries any 'powerloom*' server entry."""
    global_path = tmp_path / "user-global-mcp.json"
    global_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "powerloom-platform": {"command": "weave", "args": []},
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "loomcli._open.mcp_install.GLOBAL_MCP_CANDIDATES",
        (global_path,),
    )
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    # Use a separate worktree dir so we can assert no .mcp.json was written.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = install_mcp_config(spec, worktree)
    assert result.written_path is None
    assert result.skipped_reason == "global_powerloom_already_registered"
    assert result.server_names == ["powerloom"]
    assert not (worktree / PROJECT_MCP_FILENAME).exists()


def test_global_with_no_powerloom_does_not_skip(tmp_path, monkeypatch) -> None:
    """Global config exists but has only non-Powerloom servers → don't skip."""
    global_path = tmp_path / "user-global-mcp.json"
    global_path.write_text(
        json.dumps(
            {"mcpServers": {"some-other-tool": {"command": "x"}}},
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "loomcli._open.mcp_install.GLOBAL_MCP_CANDIDATES",
        (global_path,),
    )
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = install_mcp_config(spec, worktree)
    assert result.written_path is not None
    assert result.skipped_reason is None


def test_malformed_global_config_does_not_skip(tmp_path, monkeypatch) -> None:
    """Malformed JSON in global → treated as no Powerloom registered."""
    global_path = tmp_path / "user-global-mcp.json"
    global_path.write_text("not valid json", encoding="utf-8")
    monkeypatch.setattr(
        "loomcli._open.mcp_install.GLOBAL_MCP_CANDIDATES",
        (global_path,),
    )
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = install_mcp_config(spec, worktree)
    # Defensive — write proceeds rather than blocking on a hostile global.
    assert result.written_path is not None


def test_powerloom_match_is_case_insensitive(tmp_path, monkeypatch) -> None:
    global_path = tmp_path / "user-global-mcp.json"
    global_path.write_text(
        json.dumps({"mcpServers": {"PowerloomHome": {"command": "x"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "loomcli._open.mcp_install.GLOBAL_MCP_CANDIDATES",
        (global_path,),
    )
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    result = install_mcp_config(spec, worktree)
    assert result.skipped_reason == "global_powerloom_already_registered"


# ---------------------------------------------------------------------------
# IO error path
# ---------------------------------------------------------------------------


def test_write_failure_recorded_as_error(tmp_path, no_global_mcp) -> None:
    spec = _spec_with_mcp([
        {"name": "powerloom", "command": "weave", "args": [], "env": {}, "attach_token": "x"},
    ])
    # Pre-create a path where target should be — as a directory — so
    # write_text fails.
    bad_target = tmp_path / PROJECT_MCP_FILENAME
    bad_target.mkdir()
    result = install_mcp_config(spec, tmp_path)
    assert result.written_path is None
    assert result.error is not None
    assert "could not write" in result.error
