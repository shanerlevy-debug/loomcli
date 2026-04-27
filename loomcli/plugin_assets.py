"""Bundled client-plugin asset discovery and export helpers."""
from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

from loomcli import __version__
from loomcli.config import config_dir


CLIENT_NAMES = ("claude-code", "codex", "gemini", "antigravity")


class PluginAssetError(RuntimeError):
    """Raised when a requested client plugin cannot be found or exported."""


def plugin_export_root() -> Path:
    """Return the stable local root where bundled plugin assets are exported."""
    plugin_home = os.environ.get("POWERLOOM_PLUGIN_HOME")
    if plugin_home:
        return Path(plugin_home).expanduser() / __version__
    if os.name == "nt" and not os.environ.get("POWERLOOM_HOME"):
        # Python installed from the Microsoft Store can virtualize writes under
        # AppData\Local. Other CLIs launched by weave (codex/gemini/claude)
        # then cannot see the exported marketplace path. Keep plugin assets in
        # a normal user-profile cache by default on Windows.
        return Path.home() / ".powerloom" / "plugins" / __version__
    return config_dir() / "plugins" / __version__


def ensure_plugin_assets(client: str, *, force: bool = False) -> Path:
    """Export bundled assets for ``client`` and return the client root path.

    Source checkouts use the repo's ``plugin/`` and ``plugins/`` directories as
    the source. Built wheels use ``loomcli/_bundled_plugins`` package data.
    """
    client = _normalize_client(client)
    source = _source_for(client)
    destination = plugin_export_root() / _export_key(client)
    if force or not _looks_exported(client, destination):
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_tree(source, destination)
    return destination


def plugin_path(client: str, *, force: bool = False) -> Path:
    """Return the path a client should be pointed at after assets are exported."""
    root = ensure_plugin_assets(client, force=force)
    if client in {"gemini", "antigravity"}:
        return root / "powerloom-weave"
    return root


def _normalize_client(client: str) -> str:
    if client not in CLIENT_NAMES:
        expected = ", ".join(CLIENT_NAMES)
        raise PluginAssetError(f"unknown client {client!r}; expected one of: {expected}")
    return client


def _export_key(client: str) -> str:
    return "gemini" if client == "antigravity" else client


def _source_for(client: str):
    key = _export_key(client)
    bundled = resources.files("loomcli").joinpath("_bundled_plugins", key)
    if _exists(bundled):
        return bundled

    repo_root = Path(__file__).resolve().parents[1]
    if key == "claude-code":
        source = repo_root / "plugin"
    else:
        source = repo_root / "plugins" / key
    if source.exists():
        return source
    raise PluginAssetError(f"bundled plugin assets for {client!r} were not found")


def _looks_exported(client: str, path: Path) -> bool:
    if client == "claude-code":
        return (path / ".claude-plugin" / "plugin.json").exists()
    if client == "codex":
        return (
            path / ".agents" / "plugins" / "marketplace.json"
        ).exists() and (
            path / "powerloom-weave" / ".codex-plugin" / "plugin.json"
        ).exists()
    if client in {"gemini", "antigravity"}:
        return (path / "powerloom-weave" / "gemini-extension.json").exists()
    return False


def _copy_tree(source, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in _SKIP_NAMES:
            continue
        target = destination / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with item.open("rb") as reader:
                target.write_bytes(reader.read())


def _exists(resource) -> bool:
    try:
        return resource.exists()
    except FileNotFoundError:
        return False


_SKIP_NAMES: set[str] = {"__pycache__", ".pytest_cache"}
