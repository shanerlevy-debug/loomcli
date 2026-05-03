"""Tests for ``loomcli._open.runtime_configs_install``.

Console-deployability sprint PR4, thread ``ea3be766``.

The module fetches translator-rendered configs from the engine
(``GET /launches/{token}/runtime-configs``) and writes them to disk.
These tests mock the engine call and verify:

* Claude launches short-circuit (the legacy install path covers them).
* Codex / Gemini / Antigravity launches apply the returned files.
* Idempotency — second run on identical content reports ``unchanged``.
* Drift — second run on tampered content reports ``updated``.
* Path traversal — refuses to write outside ``target_dir``.
* Engine fetch failure is non-fatal — surfaces as a warning, not a raise.
* ``post_install_steps`` and ``warnings`` from the engine flow through.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from loomcli._open.runtime_configs_install import (
    RuntimeConfigsInstallResult,
    RuntimeFileResult,
    install_runtime_configs,
)
from loomcli.schema.launch_spec import LaunchSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(*, runtime: str = "codex_cli") -> LaunchSpec:
    """Build a minimal LaunchSpec stub for the given runtime."""
    return LaunchSpec.model_validate({
        "schema_version": 1,
        "launch_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-05-03T00:00:00Z",
        "expires_at": "2026-05-03T00:15:00Z",
        "actor": {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "email": "shane@example.com",
            "runtime": runtime,
        },
        "project": {
            "id": "33333333-3333-3333-3333-333333333333",
            "slug": "powerloom",
            "repo_url": "https://github.com/x/y.git",
            "default_branch": "main",
        },
        "scope": {
            "slug": "rt-cfg-test",
            "branch_base": "main",
            "branch_name": "session/rt-cfg-test",
        },
        "runtime": runtime,
        "skills": [],
        "capabilities": [],
        "clone_auth": {"mode": "server_minted"},
        "mcp_config": {"servers": []},
        "rules_sync": [],
    })


def _mock_client(payload: dict[str, Any]) -> MagicMock:
    """Build a MagicMock PowerloomClient whose ``.get`` returns
    ``payload`` once. ``.close()`` is a no-op."""
    client = MagicMock()
    client.get.return_value = payload
    return client


_CFG_STUB = MagicMock(name="LoomcliRuntimeConfig-stub")


# ---------------------------------------------------------------------------
# Short-circuit cases
# ---------------------------------------------------------------------------


def test_claude_runtime_short_circuits_without_calling_engine(tmp_path) -> None:
    """Claude path is owned by skills_install + mcp_install. The
    runtime-configs endpoint shouldn't even be called."""
    client = _mock_client({"configs": []})  # would be applied if called
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="claude_code"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    assert result.skipped_reason == "claude_runtime"
    assert result.files == []
    client.get.assert_not_called()


def test_empty_configs_returns_skipped_reason(tmp_path) -> None:
    """When the engine returns no configs (launch had no skills/MCP),
    surface as skipped — operator sees a 'no configs' line, not an
    empty success."""
    client = _mock_client({"runtime": "codex_cli", "configs": []})
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    assert result.skipped_reason == "empty_configs"
    assert result.files == []


def test_engine_fetch_failure_is_nonfatal(tmp_path) -> None:
    """An engine error is surfaced as a warning, not raised. The
    launch continues — operator can re-run after the engine recovers."""
    from loomcli.client import PowerloomApiError

    client = MagicMock()
    client.get.side_effect = PowerloomApiError(502, "engine 502")
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    assert result.skipped_reason == "fetch_failed"
    assert result.warnings, "fetch_failed must carry a warning"
    assert "engine 502" in result.warnings[0]


# ---------------------------------------------------------------------------
# Apply path — installed / unchanged / updated
# ---------------------------------------------------------------------------


def _codex_skill_payload() -> dict[str, Any]:
    """A realistic Codex translator output for one skill."""
    return {
        "runtime": "codex_cli",
        "configs": [
            {
                "kind": "skill",
                "runtime": "codex",
                "files": [
                    {
                        "path": ".codex/skills/widget-parser/skill.toml",
                        "content": 'name = "widget-parser"\n',
                        "mode": 0o644,
                    },
                    {
                        "path": ".codex/skills/widget-parser/instructions.md",
                        "content": "# widget-parser\n\nExample skill body.\n",
                        "mode": 0o644,
                    },
                ],
                "env": {},
                "post_install_steps": [
                    "Codex auto-discovers skills on next session open.",
                ],
                "warnings": [],
            },
        ],
    }


def test_first_run_writes_all_files(tmp_path) -> None:
    client = _mock_client(_codex_skill_payload())
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    assert result.skipped_reason is None
    actions = {f.path.name: f.action for f in result.files}
    assert actions == {"skill.toml": "installed", "instructions.md": "installed"}
    # Files actually exist
    assert (tmp_path / ".codex/skills/widget-parser/skill.toml").exists()
    assert (tmp_path / ".codex/skills/widget-parser/instructions.md").exists()


def test_second_run_with_identical_content_is_unchanged(tmp_path) -> None:
    """SHA-256 idempotency — second run on byte-identical files
    reports `unchanged` for every file."""
    payload = _codex_skill_payload()
    install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    result2 = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    assert all(f.action == "unchanged" for f in result2.files)


def test_drift_detection_reports_updated(tmp_path) -> None:
    """Hand-tamper a file between runs; second run must detect drift
    and rewrite the file (action='updated')."""
    payload = _codex_skill_payload()
    install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    drifted = tmp_path / ".codex/skills/widget-parser/skill.toml"
    drifted.write_text("# tampered\n", encoding="utf-8")

    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    actions = {f.path.name: f.action for f in result.files}
    # The drifted file gets rewritten; the unchanged one stays unchanged.
    assert actions["skill.toml"] == "updated"
    assert actions["instructions.md"] == "unchanged"


# ---------------------------------------------------------------------------
# Defense-in-depth — path traversal + bad b64
# ---------------------------------------------------------------------------


def test_refuses_absolute_paths(tmp_path) -> None:
    payload = {
        "runtime": "codex_cli",
        "configs": [{
            "kind": "skill", "runtime": "codex",
            "files": [{
                "path": "/etc/passwd-takeover",
                "content": "no",
                "mode": 0o644,
            }],
            "env": {}, "post_install_steps": [], "warnings": [],
        }],
    }
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    actions = [(f.action, f.detail) for f in result.files]
    assert actions[0][0] == "error"
    assert "outside target_dir" in (actions[0][1] or "")
    # And nothing was written under the .codex/ tree (conftest's
    # powerloom-home/ is unrelated and lives at tmp_path itself).
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / "etc").exists()


def test_refuses_dotdot_traversal(tmp_path) -> None:
    payload = {
        "runtime": "codex_cli",
        "configs": [{
            "kind": "skill", "runtime": "codex",
            "files": [{
                "path": "../../etc/escape",
                "content": "no",
                "mode": 0o644,
            }],
            "env": {}, "post_install_steps": [], "warnings": [],
        }],
    }
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    assert result.files[0].action == "error"
    assert "outside target_dir" in (result.files[0].detail or "")


def test_invalid_base64_surfaces_as_error(tmp_path) -> None:
    payload = {
        "runtime": "codex_cli",
        "configs": [{
            "kind": "skill", "runtime": "codex",
            "files": [{
                "path": ".codex/binary",
                "content": "",
                "bytes_content_b64": "@@@not-valid-b64@@@",
                "mode": 0o644,
            }],
            "env": {}, "post_install_steps": [], "warnings": [],
        }],
    }
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    assert result.files[0].action == "error"
    assert "bytes_content_b64" in (result.files[0].detail or "")


# ---------------------------------------------------------------------------
# Translator metadata flows through to the result
# ---------------------------------------------------------------------------


def test_post_install_steps_and_warnings_flow_through(tmp_path) -> None:
    """The engine's per-config `post_install_steps` and `warnings`
    aggregate into the result so the CLI can render them."""
    payload = {
        "runtime": "codex_cli",
        "configs": [
            {
                "kind": "mcp_server", "runtime": "codex",
                "files": [],
                "env": {"POWERLOOM_MCP_TOKEN": ""},
                "post_install_steps": [
                    "Set POWERLOOM_MCP_TOKEN in your shell rc.",
                ],
                "warnings": ["Codex doesn't auto-merge MCP snippets."],
            },
            {
                "kind": "skill", "runtime": "codex",
                "files": [],
                "env": {},
                "post_install_steps": ["Codex auto-discovers skills."],
                "warnings": [],
            },
        ],
    }
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="codex_cli"),
        launch_token="lt_xxx", target_dir=tmp_path,
        client=_mock_client(payload),
    )
    assert "Set POWERLOOM_MCP_TOKEN in your shell rc." in result.post_install_steps
    assert "Codex auto-discovers skills." in result.post_install_steps
    assert "Codex doesn't auto-merge MCP snippets." in result.warnings


# ---------------------------------------------------------------------------
# Antigravity dispatches to translator path (not Claude short-circuit)
# ---------------------------------------------------------------------------


def test_antigravity_runtime_calls_engine(tmp_path) -> None:
    payload = {"runtime": "antigravity", "configs": []}
    client = _mock_client(payload)
    result = install_runtime_configs(
        _CFG_STUB, _spec(runtime="antigravity"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    # Empty-configs surfaces as skipped, but the engine WAS called
    # (proving Antigravity isn't routed through the claude_runtime
    # short-circuit).
    assert result.skipped_reason == "empty_configs"
    client.get.assert_called_once_with("/launches/lt_xxx/runtime-configs")


def test_gemini_runtime_calls_engine(tmp_path) -> None:
    client = _mock_client({"runtime": "gemini_cli", "configs": []})
    install_runtime_configs(
        _CFG_STUB, _spec(runtime="gemini_cli"),
        launch_token="lt_xxx", target_dir=tmp_path, client=client,
    )
    client.get.assert_called_once_with("/launches/lt_xxx/runtime-configs")
