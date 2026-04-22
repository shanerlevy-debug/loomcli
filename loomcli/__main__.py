"""Enables `python -m loomcli` in addition to the installed
`weave` console script.
"""
from loomcli.cli import app


if __name__ == "__main__":
    app()
