#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu / Amazon Linux EC2 instance to run the
# Powerloom reconciler daemon as a docker-compose service under
# systemd.
#
# Usage (on the EC2 box, as root or via sudo):
#
#   curl -sSL https://raw.githubusercontent.com/shanerlevy-debug/loomcli/main/deploy/reconciler/setup-ec2.sh | sudo bash
#
# OR (after scp'ing this file + the rest of deploy/reconciler/):
#
#   sudo bash setup-ec2.sh
#
# What it does:
#   1. Installs docker + docker-compose-plugin (if missing).
#   2. Creates the `powerloom` system user + adds to `docker` group.
#   3. Installs the deploy/ directory contents to /opt/powerloom-reconciler.
#   4. Drops .env.example to .env if .env doesn't exist (operator fills
#      in POWERLOOM_ACCESS_TOKEN before starting the service).
#   5. Installs the systemd unit + reloads systemd. Does NOT enable the
#      service yet — operator confirms .env is populated, then runs
#      `sudo systemctl enable --now powerloom-reconciler`.
#
# Re-running this script is safe; each step checks idempotency.
#
# See docs/operating-self-hosted-agents.md (in the Powerloom repo) for
# the full operator runbook.

set -euo pipefail

INSTALL_DIR="/opt/powerloom-reconciler"
SERVICE_USER="powerloom"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Powerloom reconciler EC2 bootstrap"
echo "    Install dir:   ${INSTALL_DIR}"
echo "    Service user:  ${SERVICE_USER}"
echo "    Script dir:    ${SCRIPT_DIR}"
echo

# ---------------------------------------------------------------------------
# 1. Docker + compose plugin
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "[1/5] docker + compose: already installed."
else
  echo "[1/5] Installing docker + compose-plugin..."
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = "ubuntu" ] || [ "${ID:-}" = "debian" ]; then
      # Ubuntu / Debian — official Docker apt repo.
      apt-get update
      apt-get install -y ca-certificates curl gnupg
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/${ID}/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} \
$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}") stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    elif [ "${ID:-}" = "amzn" ] || [ "${ID_LIKE:-}" = "fedora" ]; then
      # Amazon Linux / Fedora-family — yum/dnf. AL2023 ships docker;
      # compose plugin gets pulled separately.
      yum install -y docker || dnf install -y docker
      systemctl enable --now docker
      DOCKER_CONFIG=${DOCKER_CONFIG:-/usr/local/lib/docker}
      mkdir -p "$DOCKER_CONFIG/cli-plugins"
      curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
        -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
      chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
    else
      echo "ERROR: Unsupported distro ${ID}. Install docker + compose-plugin manually and re-run."
      exit 1
    fi
  fi
  systemctl enable --now docker
fi

# ---------------------------------------------------------------------------
# 2. Service user
# ---------------------------------------------------------------------------
if id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "[2/5] Service user ${SERVICE_USER}: exists."
else
  echo "[2/5] Creating service user ${SERVICE_USER}..."
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
# Ensure user is in the docker group regardless of when it was created.
usermod -aG docker "${SERVICE_USER}"

# ---------------------------------------------------------------------------
# 3. Install dir + asset copy
# ---------------------------------------------------------------------------
echo "[3/5] Installing assets to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp -f "${SCRIPT_DIR}/docker-compose.yml" "${INSTALL_DIR}/"
cp -f "${SCRIPT_DIR}/Dockerfile"        "${INSTALL_DIR}/"
cp -f "${SCRIPT_DIR}/.env.example"      "${INSTALL_DIR}/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# 4. .env scaffolding
# ---------------------------------------------------------------------------
if [ -f "${INSTALL_DIR}/.env" ]; then
  echo "[4/5] .env: already populated, leaving alone."
else
  echo "[4/5] Seeding .env from .env.example -- you MUST edit it before starting the service:"
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}/.env"
  chmod 600 "${INSTALL_DIR}/.env"
  echo "       sudo -u ${SERVICE_USER} nano ${INSTALL_DIR}/.env"
fi

# ---------------------------------------------------------------------------
# 5. systemd unit
# ---------------------------------------------------------------------------
echo "[5/5] Installing systemd unit..."
cp -f "${SCRIPT_DIR}/powerloom-reconciler.service" /etc/systemd/system/
systemctl daemon-reload

cat <<'POSTINSTALL'

==> Bootstrap complete.

Next steps (manual):

  1. Populate the PAT in /opt/powerloom-reconciler/.env:
       sudo -u powerloom nano /opt/powerloom-reconciler/.env
     Replace POWERLOOM_ACCESS_TOKEN=pat_REPLACE_ME with a real
     `pat_...` value minted at https://app.powerloom.org/settings/access-tokens.

  2. Smoke test (one tick, dry-run, no service):
       sudo -u powerloom bash -c 'cd /opt/powerloom-reconciler && docker compose run --rm reconciler weave agent run reconciler --dry-run --once'
     Expected: "0 item(s)" or one or more decision lines + exit 0.

  3. Enable the service (foreground daemon, restarts on failure):
       sudo systemctl enable --now powerloom-reconciler
       sudo systemctl status powerloom-reconciler

  4. Watch logs:
       sudo journalctl -u powerloom-reconciler -f

For the full operator runbook see:
  https://github.com/shanerlevy-debug/Powerloom/blob/main/docs/operating-self-hosted-agents.md

POSTINSTALL
