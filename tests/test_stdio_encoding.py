"""Tests for stdin encoding handling on Windows-style cp1252 / UTF-16 input.

The bug these tests guard against: piping a UTF-8 markdown file into
`weave thread create --description-from-stdin` on Windows used to crash
deep in httpx with `UnicodeEncodeError: surrogates not allowed`. The
fix in cli.py is two-pronged:

  1. _configure_stdio() reconfigures sys.stdin to UTF-8 strict on Windows
     so the bytes are decoded correctly in the first place.
  2. main() catches UnicodeDecodeError + the surrogate-flavored
     UnicodeEncodeError and prints a single-line remedy ("re-export
     with PYTHONUTF8=1 ...") instead of dumping a 50-line traceback.
"""
from __future__ import annotations

import sys

import pytest
import typer
from typer.testing import CliRunner

from loomcli import cli


runner = CliRunner()


def test_main_translates_unicode_decode_error_to_friendly_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If something inside the app raises UnicodeDecodeError (the shape we'd
    see when stdin holds a UTF-16 BOM that Python tried to decode as UTF-8),
    main() should print a one-line remedy and exit 1 — not a traceback."""

    def fake_app() -> None:
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "unexpected BOM")

    monkeypatch.setattr(cli, "app", fake_app)
    with pytest.raises(typer.Exit) as exc:
        cli.main()
    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    assert "stdin is not valid UTF-8" in captured.err
    assert "PYTHONUTF8=1" in captured.err
    assert "iconv" in captured.err


def test_main_translates_surrogate_unicode_encode_error_to_friendly_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The cp1252 + surrogateescape flow produces lone-surrogate codepoints
    that explode at httpx's encode("utf-8") step. main() identifies that
    shape ("surrogates not allowed") and prints the same remedy as the
    decode path."""

    def fake_app() -> None:
        # Reproduce the shape of the real production crash from
        # httpx's encode_json call site.
        raise UnicodeEncodeError(
            "utf-8", "\udc94", 0, 1, "surrogates not allowed"
        )

    monkeypatch.setattr(cli, "app", fake_app)
    with pytest.raises(typer.Exit) as exc:
        cli.main()
    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    assert "lone surrogate codepoints" in captured.err
    assert "PYTHONUTF8=1" in captured.err


def test_main_passes_through_unrelated_unicode_encode_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output-side UnicodeEncodeError (e.g. terminal can't render U+2014)
    keeps its old behaviour: a generic 'Output encoding error' line, not
    the stdin-shaped remedy."""

    def fake_app() -> None:
        # Shape of a stdout encode error when the terminal codec can't
        # represent a normal codepoint — note: NOT a lone surrogate.
        raise UnicodeEncodeError("cp1252", "—", 0, 1, "no mapping")

    monkeypatch.setattr(cli, "app", fake_app)
    with pytest.raises(typer.Exit) as exc:
        cli.main()
    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    assert "Output encoding error" in captured.err
    assert "PYTHONUTF8=1" not in captured.err  # not the stdin remedy


def test_configure_stdio_is_idempotent_on_non_windows() -> None:
    """On non-Windows platforms the function is a no-op; ensure it doesn't
    raise even when called twice (which the import-time call already did
    once)."""
    if sys.platform == "win32":
        pytest.skip("Windows-only path is exercised in production; this guards POSIX")
    cli._configure_stdio()
    cli._configure_stdio()
