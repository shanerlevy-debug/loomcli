from __future__ import annotations

from typer.testing import CliRunner

from loomcli.cli import app
from loomcli.config import load_runtime_config


runner = CliRunner()


def test_profile_set_show_and_runtime_config(monkeypatch):
    monkeypatch.delenv("POWERLOOM_API_BASE_URL", raising=False)
    result = runner.invoke(
        app,
        [
            "profile",
            "set",
            "--profile",
            "dev",
            "--api-url",
            "http://localhost:8000",
            "--default-org",
            "/dev-org",
            "--default-ou",
            "/dev-org/engineering",
            "--default-agent",
            "/dev-org/engineering/alfred",
            "--default-runtime",
            "openai",
            "--default-model",
            "gpt-5.5",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    cfg = load_runtime_config()
    assert cfg.active_profile == "dev"
    assert cfg.api_base_url == "http://localhost:8000"
    assert cfg.default_org == "/dev-org"
    assert cfg.default_ou == "/dev-org/engineering"
    assert cfg.default_agent == "/dev-org/engineering/alfred"
    assert cfg.default_runtime == "openai"
    assert cfg.default_model == "gpt-5.5"
    assert cfg.default_output == "json"

    result = runner.invoke(app, ["profile", "show", "--json"])
    assert result.exit_code == 0, result.stdout
    assert '"default_model": "gpt-5.5"' in result.stdout


def test_profile_clear_removes_selected_default():
    result = runner.invoke(
        app,
        ["profile", "set", "--default-agent", "/dev-org/engineering/alfred"],
    )
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(app, ["profile", "clear", "--default-agent"])
    assert result.exit_code == 0, result.stdout

    cfg = load_runtime_config()
    assert cfg.default_agent is None
