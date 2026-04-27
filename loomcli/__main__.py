"""Enables `python -m loomcli` in addition to the installed
`weave` console script.
"""
from loomcli.cli import main


if __name__ == "__main__":
    main()
