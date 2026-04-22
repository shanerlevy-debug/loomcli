#!/usr/bin/env bash
#
# Build the `weave` single-binary via PyInstaller.
#
# Usage (from this repo root):
#     ./build-binary.sh
#
# Output:  dist/weave          (or dist/weave.exe on Windows)
#
# Prereqs: Python 3.11+, pip, this package installed with [dev] extras.
# The script installs [dev] itself if PyInstaller isn't already on PATH,
# so a clean checkout works without additional setup.
#
# The spec bundles the JSON Schema from `schema/v1/` into the binary so
# `weave` can validate manifests without needing the repo checkout.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Sanity check — schema bundle must be present.
if [[ ! -f "schema/v1/powerloom.v1.bundle.json" ]]; then
    echo "error: schema/v1/powerloom.v1.bundle.json missing." >&2
    echo "  Ensure the repo checkout is complete." >&2
    exit 1
fi

if ! python -c 'import PyInstaller' 2>/dev/null; then
    echo "PyInstaller not found — installing [dev] extras…"
    pip install -e ".[dev]"
fi

rm -rf build/ dist/
pyinstaller loomcli.spec

echo
echo "Built: $HERE/dist/weave"
"$HERE/dist/weave" --version
