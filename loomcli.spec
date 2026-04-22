# PyInstaller spec for the `weave` single-binary build.
#
# Produces a platform-native binary under dist/weave (or dist/weave.exe
# on Windows). The spec opts for --onefile mode so admins can
# `chmod +x weave && ./weave apply manifest.yaml` without any extra
# files.
#
# Cross-platform note: PyInstaller builds on the host OS only. To ship
# binaries for Linux/macOS/Windows you need a CI matrix — tracked in
# Powerloom's `docs/loomcli-overhaul.md` M1.b.
#
# Build locally (from the repo root):
#     pip install -e ".[dev]"
#     pyinstaller loomcli.spec
#
# Alternative distribution path: `pip install -e .` for dev, or
# `pip install .` to install into the current venv. The binary is the
# "no Python required" option; pip remains the preferred dev path.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None


a = Analysis(
    ['loomcli/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle the JSON Schema so `weave` can validate manifests without
        # needing the repo checkout.
        ('schema/v1', 'schema/v1'),
    ],
    hiddenimports=[
        # Typer lazy-imports click internals that PyInstaller misses
        # occasionally; list them defensively.
        'click',
        'click.shell_completion',
        'pydantic.deprecated.decorator',
        # jsonschema lazy-imports format checkers.
        'jsonschema_specifications',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='weave',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
