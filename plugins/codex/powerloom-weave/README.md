# Powerloom Weave for Codex

Codex plugin package for operating the Powerloom `weave` CLI.

## What It Provides

- `powerloom-onboarding` skill — first-10-minutes walk for fresh Codex sessions: install loomcli, sign in, load this plugin, file your first tracker thread.
- `weave-tracker` skill — full §4.10 tracker thread workflow (create / pluck / reply / done; multi-session coordination; sub-principal attribution).
- `weave-interpreter` skill — comprehensive `weave` CLI reference for general operations.
- Manifest authoring and troubleshooting guidance.
- Provider-agnostic invocation rules: Codex should use Powerloom's control plane, not direct provider API calls.

## Install

Codex installs plugin marketplaces, not raw plugin package directories. Let `weave` export the bundled marketplace assets and add the exported root:

```bash
pip install -U loomcli
weave plugin install codex --execute
```

`weave plugin instructions codex` prints the exact marketplace path and `weave plugin doctor codex` checks that the package exists and Codex is on PATH.

The plugin package lives at `plugins/codex/powerloom-weave`; the marketplace manifest lives at `plugins/codex/.agents/plugins/marketplace.json`.

Codex CLI currently exposes marketplace management from the terminal. If the plugin does not appear as enabled after adding the marketplace, enable `powerloom-weave@powerloom` in Codex's plugin UI or add this to `~/.codex/config.toml`:

```toml
[plugins."powerloom-weave@powerloom"]
enabled = true
```

The plugin expects `weave` to be installed:

```bash
pip install -U loomcli
weave --version
```

## Onboarding a fresh Codex session

If you're a fresh Codex session reading this for the first time, ask "how do I get started with Powerloom?" — the `powerloom-onboarding` skill auto-loads and walks you through installing loomcli, `weave login`, loading this plugin, and filing your first tracker thread.
If Codex reports that the marketplace root does not contain a supported manifest, rerun `weave plugin instructions codex` and use the exported marketplace root it prints, not the nested `powerloom-weave` package directory.
