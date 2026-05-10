"""`weave enforce-check` — pattern-match a tool invocation against a
policy pattern file and report violations.

Workstream 0c of the agent-governance arc. Lives as the helper invoked
from a runtime's `PreToolUse` (Claude Code, Codex CLI) or `BeforeTool`
(Gemini / Antigravity) hook when:

  * a `warn`-mode `tool_deny` directive needs to surface a non-blocking
    warning when the agent attempts a matching tool call; **or**
  * a `enforce`-mode `tool_deny` directive on Codex CLI (which has no
    native per-tool deny — see
    `docs/architecture/agent-governance-rbac-extensions.md` §7.2)
    needs to *block* the call by exiting non-zero.

The two modes share a single helper so admins author the pattern once
and the same string applies in both places.

USAGE

    # Hook contract (the harness pipes JSON to stdin):
    weave enforce-check --mode warn --pattern-file .claude/.powerloom-warn-patterns

    # CLI args contract (testing, cross-runtime fallback):
    weave enforce-check --mode block \\
        --pattern-file .claude/.powerloom-deny-patterns \\
        --tool-name Bash \\
        --tool-args 'git push origin main'

PATTERN FILE FORMAT

    One pattern per line. `#`-prefixed lines and blank lines are
    ignored. Patterns are Claude-Code-style `<ToolName>(<glob>)`,
    matched via fnmatch:

        # No direct push to main
        Bash(git push origin main*)
        Bash(git push --force*)

        # No reads under /etc
        Read(/etc/**)

        # Match any tool call (escape hatch for development)
        # *

EXIT CODES

    `--mode warn`  — always 0. Match output goes to stderr; tool call
                     proceeds.
    `--mode block` — 0 if no match; 1 if any pattern matches. The
                     non-zero exit aborts the tool call (Codex / CC
                     hook contract treats non-zero as deny).
"""
from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer


# Default pattern-file paths, indexed by mode. Workstream-3's sync CLI
# writes to these locations alongside the runtime's settings file.
_DEFAULT_PATTERN_FILE = {
    "warn":  ".claude/.powerloom-warn-patterns",
    "block": ".claude/.powerloom-deny-patterns",
}


def _read_patterns(path: Path) -> list[str]:
    """Parse a pattern file. One pattern per line, `#`-prefixed lines
    and blank lines ignored. Trailing comments on the same line as a
    pattern are NOT supported — the entire line is the pattern."""
    if not path.exists():
        # Missing pattern file = no patterns = no matches. Documented
        # behavior: a sync that hasn't yet written the file shouldn't
        # break tool calls.
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _match_pattern(pattern: str, tool_name: str, tool_args: str) -> bool:
    """True if `pattern` matches the tool invocation. Pattern shape:
    `<ToolName>(<glob>)`. The glob is matched against `tool_args` via
    fnmatch. The bare wildcard `*` (with no parens) matches every tool
    call — escape hatch for dev/debug.
    """
    if pattern == "*":
        return True
    open_paren = pattern.find("(")
    close_paren = pattern.rfind(")")
    if open_paren == -1 or close_paren == -1 or close_paren < open_paren:
        # Malformed pattern. Skip rather than crash — sync CLI should
        # have validated, but we're defensive about handler input.
        return False
    pat_tool = pattern[:open_paren]
    pat_glob = pattern[open_paren + 1 : close_paren]
    if pat_tool != tool_name:
        return False
    return fnmatch.fnmatchcase(tool_args, pat_glob)


def _resolve_tool_invocation(
    tool_name: Optional[str],
    tool_args: Optional[str],
) -> tuple[str, str]:
    """Resolve (tool_name, tool_args) from CLI args first, falling back
    to stdin JSON (Claude Code's PreToolUse contract).

    Stdin shape (Claude Code):
        {"hook_event_name": "PreToolUse",
         "tool_name": "Bash",
         "tool_input": {"command": "git push origin main"}}

    `tool_input` is tool-specific. We flatten it to a single string for
    glob-matching: the Bash `command` field, or the JSON-dump of the
    whole `tool_input` for tools we don't know how to flatten."""
    if tool_name is not None:
        return tool_name, tool_args or ""

    # No CLI args — try stdin.
    if sys.stdin.isatty():
        # No piped input either. Nothing to check.
        return "", ""
    raw = sys.stdin.read()
    if not raw.strip():
        return "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Not JSON → don't fail, just no-op. Codex / Gemini hooks may
        # pipe a different shape; future work expands this branch.
        return "", ""

    name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input", {})
    args = _flatten_tool_input(name, tool_input)
    return name, args


def _flatten_tool_input(tool_name: str, tool_input: object) -> str:
    """Render a tool's structured input into the single string the
    pattern's glob matches against. Knows the canonical shape for the
    common Claude Code tools; falls through to JSON-dump for the rest.
    """
    if not isinstance(tool_input, dict):
        return str(tool_input)
    # Bash tool — `{"command": "..."}`. Glob against the command line.
    if tool_name == "Bash" and "command" in tool_input:
        return str(tool_input["command"])
    # File-touching tools — flatten to the path so patterns like
    # `Read(/etc/**)` work without admins knowing about the JSON shape.
    if tool_name in ("Read", "Edit", "Write", "Glob", "Grep") and "file_path" in tool_input:
        return str(tool_input["file_path"])
    # Unknown / future tools — stable JSON dump (sorted keys for
    # deterministic globbing).
    return json.dumps(tool_input, sort_keys=True, separators=(",", ":"))


def enforce_check_command(
    pattern_file: Annotated[
        Optional[Path],
        typer.Option(
            "--pattern-file",
            help=(
                "Path to the pattern file. Defaults to "
                "`.claude/.powerloom-warn-patterns` for `--mode warn` "
                "and `.claude/.powerloom-deny-patterns` for `--mode block`."
            ),
        ),
    ] = None,
    tool_name: Annotated[
        Optional[str],
        typer.Option(
            "--tool-name",
            help="Tool name (e.g. `Bash`). Falls back to stdin JSON if omitted.",
        ),
    ] = None,
    tool_args: Annotated[
        Optional[str],
        typer.Option(
            "--tool-args",
            help="Tool args (e.g. `git push origin main`). Falls back to stdin JSON if omitted.",
        ),
    ] = None,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="`warn` (always exit 0) or `block` (exit 1 on any match).",
        ),
    ] = "warn",
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress match output (CI / testing)."),
    ] = False,
) -> None:
    """Pattern-match a tool invocation against a policy pattern file."""
    if mode not in ("warn", "block"):
        typer.echo(f"--mode must be 'warn' or 'block' (got {mode!r})", err=True)
        raise typer.Exit(2)

    resolved_tool, resolved_args = _resolve_tool_invocation(tool_name, tool_args)
    if not resolved_tool:
        # No tool invocation to check. No-op exit per design — the hook
        # might fire for an event without a tool call (e.g. Codex's
        # SessionStart hook).
        raise typer.Exit(0)

    pf = pattern_file or Path(_DEFAULT_PATTERN_FILE[mode])
    patterns = _read_patterns(pf)
    if not patterns:
        raise typer.Exit(0)

    matched = [p for p in patterns if _match_pattern(p, resolved_tool, resolved_args)]
    if not matched:
        raise typer.Exit(0)

    if not quiet:
        prefix = "[powerloom block]" if mode == "block" else "[powerloom warn]"
        for pat in matched:
            typer.echo(
                f"{prefix} {resolved_tool}({resolved_args}) "
                f"matches policy pattern: {pat}",
                err=True,
            )

    raise typer.Exit(1 if mode == "block" else 0)
