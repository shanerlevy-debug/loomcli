"""Generate docs/commands.generated.md from loomcli.command_registry."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loomcli.command_registry import list_commands


DOC_PATH = ROOT / "docs" / "commands.generated.md"


def render_command_docs() -> str:
    lines = [
        "# Weave Command Registry",
        "",
        "Generated from `loomcli.command_registry.COMMANDS`. Do not edit by hand.",
        "",
        "| Command | Category | Summary |",
        "|---|---|---|",
    ]
    for row in list_commands():
        lines.append(
            f"| `{row['command']}` | {row['category']} | {row['summary']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if docs are stale.")
    args = parser.parse_args()

    expected = render_command_docs()
    if args.check:
        actual = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if actual != expected:
            print(f"{DOC_PATH} is stale; run python scripts/generate_command_docs.py")
            return 1
        return 0

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(expected, encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
