# Powerloom Weave for Gemini CLI

Gemini CLI extension for operating the Powerloom `weave` CLI.

## What It Provides

- `GEMINI.md` context for Powerloom/loomcli usage.
- Custom commands:
  - `/weave:ask`
  - `/weave:chat`
  - `/weave:status`
  - `/weave:plan`

## Development Install

```bash
gemini extensions link /path/to/loomcli/plugins/gemini/powerloom-weave
```

Restart Gemini CLI or reload commands after linking.

The extension expects `weave` to be installed:

```bash
pip install -e /path/to/loomcli
weave --version
```
