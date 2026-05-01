"""Tests for ``loomcli._open.rules_sync``.

Sprint cli-weave-open-20260430, thread 53fddf29.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import typer

from loomcli._open.rules_sync import (
    DirectiveResult,
    apply_directives,
)
from loomcli.schema.launch_spec import LaunchSpec, RulesSyncDirective


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _spec_with_rules_sync(directives: list[dict]) -> LaunchSpec:
    base = {
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-05-01T00:00:00Z",
        "expires_at": "2026-05-01T00:15:00Z",
        "actor": {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "email": "shane@bespoke-technology.com",
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
        "mcp_config": {"servers": []},
        "rules_sync": directives,
    }
    return LaunchSpec.model_validate(base)


# ---------------------------------------------------------------------------
# empty list — no-op
# ---------------------------------------------------------------------------


def test_apply_directives_empty_list_returns_empty(tmp_path: Path) -> None:
    spec = _spec_with_rules_sync([])
    result = apply_directives(spec, tmp_path, sync_fn=lambda **kw: None)
    assert result == []


# ---------------------------------------------------------------------------
# happy path — one directive × multiple runtimes
# ---------------------------------------------------------------------------


def test_apply_directives_invokes_sync_per_runtime(tmp_path: Path) -> None:
    """One directive with 3 runtimes → 3 sync_fn calls, all in the result."""
    sync_fn = MagicMock()
    spec = _spec_with_rules_sync([
        {
            "scope": "bespoke-technology.powerloom",
            "runtimes": ["claude_code", "codex_cli", "gemini_cli"],
        },
    ])

    results = apply_directives(spec, tmp_path, sync_fn=sync_fn)

    assert len(results) == 1
    r = results[0]
    assert r.scope == "bespoke-technology.powerloom"
    assert r.succeeded_runtimes == ["claude_code", "codex_cli", "gemini_cli"]
    assert r.failed_runtimes == []
    assert r.fully_succeeded
    assert sync_fn.call_count == 3
    # Each call gets the directive's scope + per-runtime + worktree workdir.
    for call, runtime in zip(
        sync_fn.call_args_list, ["claude_code", "codex_cli", "gemini_cli"]
    ):
        kwargs = call.kwargs
        assert kwargs["scope"] == "bespoke-technology.powerloom"
        assert kwargs["runtime"] == runtime
        assert kwargs["workdir"] == tmp_path
        assert kwargs["quiet"] is True
        assert kwargs["dry_run"] is False


# ---------------------------------------------------------------------------
# multiple directives
# ---------------------------------------------------------------------------


def test_apply_directives_multiple_directives_all_invoked(tmp_path: Path) -> None:
    sync_fn = MagicMock()
    spec = _spec_with_rules_sync([
        {"scope": "org.proj1", "runtimes": ["claude_code"]},
        {"scope": "org.proj2", "runtimes": ["codex_cli", "gemini_cli"]},
    ])

    results = apply_directives(spec, tmp_path, sync_fn=sync_fn)

    assert [r.scope for r in results] == ["org.proj1", "org.proj2"]
    assert results[0].succeeded_runtimes == ["claude_code"]
    assert results[1].succeeded_runtimes == ["codex_cli", "gemini_cli"]
    assert sync_fn.call_count == 3


# ---------------------------------------------------------------------------
# per-runtime failure within a directive
# ---------------------------------------------------------------------------


def test_apply_directives_partial_runtime_failure_continues(
    tmp_path: Path,
) -> None:
    """One runtime fails (typer.Exit) — others in the same directive succeed."""
    seen_runtimes = []

    def fake_sync(*, scope, runtime, workdir, dry_run, quiet):
        seen_runtimes.append(runtime)
        if runtime == "codex_cli":
            raise typer.Exit(code=2)
        return None

    spec = _spec_with_rules_sync([
        {
            "scope": "bespoke-technology.powerloom",
            "runtimes": ["claude_code", "codex_cli", "gemini_cli"],
        },
    ])

    results = apply_directives(spec, tmp_path, sync_fn=fake_sync)
    r = results[0]
    assert r.succeeded_runtimes == ["claude_code", "gemini_cli"]
    assert len(r.failed_runtimes) == 1
    assert r.failed_runtimes[0][0] == "codex_cli"
    assert "Exit" in r.failed_runtimes[0][1]
    # All three were attempted — the failure didn't short-circuit.
    assert seen_runtimes == ["claude_code", "codex_cli", "gemini_cli"]


def test_apply_directives_arbitrary_exception_translates_to_failed(
    tmp_path: Path,
) -> None:
    def fake_sync(*, scope, runtime, workdir, dry_run, quiet):
        raise RuntimeError("network blip")

    spec = _spec_with_rules_sync([
        {"scope": "x.y", "runtimes": ["claude_code"]},
    ])

    results = apply_directives(spec, tmp_path, sync_fn=fake_sync)
    r = results[0]
    assert r.succeeded_runtimes == []
    assert r.failed_runtimes == [("claude_code", "network blip")]
    assert r.fully_failed


# ---------------------------------------------------------------------------
# directive_result properties
# ---------------------------------------------------------------------------


def test_directive_result_fully_succeeded_property() -> None:
    r = DirectiveResult(scope="x", succeeded_runtimes=["claude_code"])
    assert r.fully_succeeded


def test_directive_result_fully_failed_property() -> None:
    r = DirectiveResult(
        scope="x", failed_runtimes=[("claude_code", "boom")]
    )
    assert r.fully_failed


def test_directive_result_mixed_is_neither_full_success_nor_full_fail() -> None:
    r = DirectiveResult(
        scope="x",
        succeeded_runtimes=["claude_code"],
        failed_runtimes=[("codex_cli", "boom")],
    )
    assert not r.fully_succeeded
    assert not r.fully_failed
