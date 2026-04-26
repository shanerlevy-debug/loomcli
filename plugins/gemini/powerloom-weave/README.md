# Powerloom Weave for Gemini CLI

Gemini CLI extension for operating the Powerloom `weave` CLI.

## What It Provides

- `GEMINI.md` context for Powerloom/loomcli usage.
- Custom commands:
  - `/weave:ask`
  - `/weave:chat`
  - `/weave:status`
  - `/weave:plan`
  - `/weave:agent-status`
  - `/weave:session-tail`
  - `/weave:thread:create`
  - `/weave:thread:pluck`
  - `/weave:thread:reply`
  - `/weave:thread:done`
  - `/weave:thread:list`

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
