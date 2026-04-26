# Powerloom Weave for Codex

Codex plugin package for operating the Powerloom `weave` CLI.

## What It Provides

- `weave-interpreter` skill for agentic `weave ask` / `weave chat`.
- Manifest authoring and troubleshooting guidance.
- Provider-agnostic invocation rules: Codex should use Powerloom's control plane, not direct provider API calls.

## Development Install

Add this plugin directory to a local Codex plugin marketplace, or load it using the Codex plugin development flow available in your environment.

The plugin expects `weave` to be installed:

```bash
pip install -e /path/to/loomcli
weave --version
```
