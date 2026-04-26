# Powerloom Weave for Codex

Codex plugin package for operating the Powerloom `weave` CLI.

## What It Provides

- `powerloom-onboarding` skill — first-10-minutes walk for fresh Codex sessions: install loomcli, sign in, load this plugin, file your first tracker thread.
- `weave-tracker` skill — full §4.10 tracker thread workflow (create / pluck / reply / done; multi-session coordination; sub-principal attribution).
- `weave-interpreter` skill — comprehensive `weave` CLI reference for general operations.
- Manifest authoring and troubleshooting guidance.
- Provider-agnostic invocation rules: Codex should use Powerloom's control plane, not direct provider API calls.

## Development Install

Add this plugin directory to a local Codex plugin marketplace, or load it using the Codex plugin development flow available in your environment.

The plugin expects `weave` to be installed:

```bash
pip install -e /path/to/loomcli
weave --version
```

## Onboarding a fresh Codex session

If you're a fresh Codex session reading this for the first time, ask "how do I get started with Powerloom?" — the `powerloom-onboarding` skill auto-loads and walks you through installing loomcli, `weave login`, loading this plugin, and filing your first tracker thread.
