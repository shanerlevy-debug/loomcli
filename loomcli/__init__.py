"""Weave — the Powerloom CLI.

Declarative manifests + resource inspection against the Powerloom
control plane. Validates manifests against the authoritative JSON
Schema published at github.com/shanerlevy-debug/loomcli (consumed
here as a git submodule at powerloom/schema/).

Public entry point is `loomcli.cli:app` (Typer). Installed as the
`weave` console script via pyproject.toml.

``__version__`` is sourced from the installed package's metadata via
``importlib.metadata`` so a single edit to ``pyproject.toml`` is the
only place a release version lives. Pre-v0.7.11 this was a hardcoded
constant that drifted from pyproject (v0.7.10 wheel was reporting
v0.7.7) — see CHANGELOG entry for v0.7.11 for the postmortem.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("loomcli")
except PackageNotFoundError:
    # Editable / source-tree usage where the wheel hasn't been
    # installed. Fall back so `python -m loomcli` doesn't crash.
    __version__ = "0.0.0+unknown"
