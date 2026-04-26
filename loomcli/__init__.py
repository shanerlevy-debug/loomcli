"""Weave — the Powerloom CLI.

Declarative manifests + resource inspection against the Powerloom
control plane. Validates manifests against the authoritative JSON
Schema published at github.com/shanerlevy-debug/loomcli (consumed
here as a git submodule at powerloom/schema/).

Public entry point is `loomcli.cli:app` (Typer). Installed as the
`weave` console script via pyproject.toml.
"""

__version__ = "0.6.2-rc1"
