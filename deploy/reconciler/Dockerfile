# Powerloom reconciler daemon — operator-host container.
#
# Runs `weave agent run reconciler` against the configured Powerloom
# control plane. Stateless between ticks; the platform DB is the source
# of truth. Singleton enforcement is honest-mistake (per loomcli PR #59
# / Powerloom audit thread aad43ba0) — heartbeats into agent_sessions,
# two daemons fighting would both surface in `weave agent sessions`.
#
# See the operator runbook (docs/operating-self-hosted-agents.md in the
# Powerloom repo) for the full setup story. The 30-second version:
#
#   docker compose -f deploy/reconciler/docker-compose.yml up -d
#
# with a ``.env`` file at the same level providing
# ``POWERLOOM_ACCESS_TOKEN`` (PAT minted via /settings/access-tokens)
# and (optionally) ``POWERLOOM_API_BASE_URL`` if you're not pointed at
# https://api.powerloom.org.

FROM python:3.12-slim

# By default tracks the PyPI 'latest' so a `docker compose build --pull
# --no-cache` always pulls the newest release. Operators that want a
# pinned build (e.g. for staged rollouts or to avoid surprise upgrades)
# can override at build time:
#
#   docker compose build --build-arg LOOMCLI_VERSION=0.7.11
#
# v0.7.10 is the floor — earlier releases lack POWERLOOM_ACCESS_TOKEN
# env-var auth, which this Dockerfile relies on for credential injection.
# v0.7.11 is recommended (fixes the systemd-unit `compose pull` bug for
# locally-built images + version-string desync; see CHANGELOG).
ARG LOOMCLI_VERSION=""

# Slim runtime — no build tools needed since loomcli ships pure-Python
# wheels. ca-certificates so HTTPS to api.powerloom.org works.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. The container has no host bind-mounts that
# need root; daemon state is stateless-tick-loop only.
RUN useradd --create-home --shell /bin/bash --uid 1001 powerloom
USER powerloom
WORKDIR /home/powerloom

# Single dependency. Pin so re-pulling the image is reproducible —
# upgrades happen by bumping the ARG above and rebuilding.
RUN pip install --no-cache-dir --user "loomcli${LOOMCLI_VERSION:+==${LOOMCLI_VERSION}}"
ENV PATH="/home/powerloom/.local/bin:${PATH}"

# Default API target. Operators override at compose / run time:
#   docker run -e POWERLOOM_API_BASE_URL=... -e POWERLOOM_ACCESS_TOKEN=...
ENV POWERLOOM_API_BASE_URL=https://api.powerloom.org

# Default agent slug. Override with the desired runtime_type='self_hosted'
# agent's slug or UUID (e.g. an agent provisioned by an operator other
# than Powerloom-the-org's own reconciler row).
ENV POWERLOOM_AGENT=reconciler

# Healthcheck — the daemon doesn't expose a port, so we shell out to
# `weave doctor --quiet` every 5 minutes to confirm the binary works
# and the API is reachable. Failures get logged to the container's
# stderr; Docker's restart policy handles wedge recovery.
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=2 \
    CMD weave doctor --quiet || exit 1

# Foreground daemon. Override CMD to add flags:
#   command: ["weave", "agent", "run", "reconciler", "--dry-run", "--once"]
# (smoke-test on startup) or
#   command: ["weave", "agent", "run", "reconciler", "--interval", "30"]
# (slower poll cadence).
CMD ["sh", "-c", "weave agent run \"${POWERLOOM_AGENT}\""]
