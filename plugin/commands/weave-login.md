---
description: Sign in to the Powerloom control plane. Opens the browser to the PAT-mint page, prompts for paste, verifies, writes credentials. Falls back to --dev-as on localhost or --pat for non-interactive use.
---

# /powerloom-home:weave-login

The user wants to sign in to the Powerloom control plane.

**Your job:**

1. First check if they're already signed in: run `weave auth whoami` and capture the output.
2. If already signed in, report the identity + org and ask if they want to log out first.
3. Otherwise, ask how they want to sign in:
   - **Default (browser + paste):** run `weave login` — opens their browser to `https://powerloom.org/settings/access-tokens`, they mint a PAT, paste it.
   - **With an existing token:** they may have a PAT already. Run `weave login --pat <token>`. Never include the actual token value in any output you produce.
   - **Localhost dev-mode:** if `$POWERLOOM_API_BASE_URL` points at localhost, they probably want `weave login --dev-as <email>`. Offer this.
4. After sign-in succeeds, verify with `weave auth whoami` and echo back the identity.

**Do not** store the PAT anywhere other than loomcli's credentials file (which `weave login` writes). Do not display it in output.

**If sign-in fails:**
- Network error → check if `$POWERLOOM_API_BASE_URL` is reachable; suggest the correct URL
- 401/invalid token → PAT was mis-copied or revoked; rerun from step 3
- "Not signed in" after apparent success → credentials file write failed; check `$POWERLOOM_HOME` path is writable

Use the `weave-interpreter` skill for anything non-obvious about weave's auth flow.
