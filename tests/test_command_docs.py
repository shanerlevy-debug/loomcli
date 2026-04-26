from __future__ import annotations

from pathlib import Path

from scripts.generate_command_docs import render_command_docs


def test_generated_command_docs_are_current():
    docs_path = Path(__file__).resolve().parents[1] / "docs" / "commands.generated.md"
    assert docs_path.read_text(encoding="utf-8") == render_command_docs()
