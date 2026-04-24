"""Smoke tests for `weave migrate v1-to-v2`."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from loomcli.cli import app

runner = CliRunner()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return p


def test_agent_clean_migration(tmp_path: Path) -> None:
    p = _write(tmp_path, "agent.yaml", """
        apiVersion: powerloom.app/v1
        kind: Agent
        metadata:
          name: code-reviewer
          ou_path: /acme/eng
        spec:
          display_name: Code Reviewer
          model: claude-sonnet-4-5
          system_prompt: Review code.
          owner_principal_ref: user:jane@acme.com
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p)])
    assert result.exit_code == 0, result.stdout
    # Find the migrated YAML in stdout (past the rich table).
    assert "apiVersion: powerloom.app/v2" in result.stdout
    assert "clean" in result.stdout


def test_legacy_alias_becomes_warn(tmp_path: Path) -> None:
    p = _write(tmp_path, "skill.yaml", """
        apiVersion: powerloom/v1
        kind: Skill
        metadata: { name: digest, ou_path: /acme }
        spec: { display_name: Digest }
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p)])
    assert result.exit_code == 0
    assert "warn" in result.stdout
    assert "legacy" in result.stdout or "powerloom/v1" in result.stdout


def test_group_needs_rewrite(tmp_path: Path) -> None:
    p = _write(tmp_path, "group.yaml", """
        apiVersion: powerloom.app/v1
        kind: Group
        metadata: { name: eng, ou_path: /acme/eng }
        spec: { display_name: Engineering }
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p)])
    # needs-rewrite is a rewrite warning, not an error — exit 0 without --check.
    assert result.exit_code == 0
    assert "needs-rewrite" in result.stdout
    # Banner comment in output:
    assert "retired in v2.0.0" in result.stdout


def test_check_exits_nonzero_on_rewrite(tmp_path: Path) -> None:
    p = _write(tmp_path, "group.yaml", """
        apiVersion: powerloom.app/v1
        kind: GroupMembership
        metadata: { name: x }
        spec: {}
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p), "--check"])
    assert result.exit_code == 1


def test_already_v2_is_skipped(tmp_path: Path) -> None:
    p = _write(tmp_path, "agent.yaml", """
        apiVersion: powerloom.app/v2
        kind: Agent
        metadata: { name: foo, ou_path: /acme }
        spec:
          display_name: Foo
          model: claude-sonnet-4-5
          system_prompt: x
          owner_principal_ref: user:j@a.com
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p)])
    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_in_place_rewrites(tmp_path: Path) -> None:
    p = _write(tmp_path, "agent.yaml", """
        apiVersion: powerloom.app/v1
        kind: Agent
        metadata: { name: code-reviewer, ou_path: /acme/eng }
        spec:
          display_name: Code Reviewer
          model: claude-sonnet-4-5
          system_prompt: Review code.
          owner_principal_ref: user:jane@acme.com
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p), "--in-place"])
    assert result.exit_code == 0
    new = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert new["apiVersion"] == "powerloom.app/v2"


def test_directory_recurses(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", """
        apiVersion: powerloom.app/v1
        kind: Skill
        metadata: { name: a, ou_path: /acme }
        spec: { display_name: A }
    """)
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(sub, "b.yml", """
        apiVersion: powerloom.app/v1
        kind: Skill
        metadata: { name: b, ou_path: /acme }
        spec: { display_name: B }
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(tmp_path)])
    assert result.exit_code == 0
    # Output concatenates both migrated docs; both names appear.
    out = result.stdout
    assert "name: a" in out
    assert "name: b" in out
    # And both bumped:
    assert out.count("apiVersion: powerloom.app/v2") >= 2


def test_unknown_apiversion_errors(tmp_path: Path) -> None:
    p = _write(tmp_path, "weird.yaml", """
        apiVersion: k8s.io/v1
        kind: Pod
    """)
    result = runner.invoke(app, ["migrate", "v1-to-v2", str(p)])
    assert result.exit_code == 1
    assert "error" in result.stdout
