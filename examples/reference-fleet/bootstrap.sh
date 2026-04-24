#!/usr/bin/env bash
# bootstrap.sh — deploy the Powerloom reference fleet end-to-end.
#
# This script idempotently deploys:
#   - 2 child OUs (studio + fleet-demo) under the configured root OU
#   - 22 skills + their archive content (uploaded + activated)
#   - 20 agents referencing the skills
#
# Prerequisites:
#   1. `pip install loomcli>=0.5.2` (requires weave skill upload commands)
#   2. `weave login` — signed into your target control plane
#   3. An existing root OU on your account (env OU_ROOT, default /bespoke-technology)
#
# Usage:
#   ./bootstrap.sh                    # deploys to the default /bespoke-technology root
#   OU_ROOT=/my-org ./bootstrap.sh    # deploys under a different root OU
#   SCHEMA_VERSION=v1.2.0 ./bootstrap.sh  # force the older schema (default: v2.0.0)
#   DRY_RUN=1 ./bootstrap.sh          # show what would happen, don't apply
#
# Idempotent: safe to run multiple times. Existing OUs / skills / agents are
# skipped (no-op for apply, archive re-upload produces a new version).

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OU_ROOT="${OU_ROOT:-/bespoke-technology}"
SCHEMA_VERSION="${SCHEMA_VERSION:-v2.0.0}"
DRY_RUN="${DRY_RUN:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLEET_DIR="${SCRIPT_DIR}/${SCHEMA_VERSION}"
ARCHIVES_DIR="${SCRIPT_DIR}/skill-archives"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "error: required command '$1' not found in PATH" >&2
        exit 1
    }
}

require_cmd weave
require_cmd zip

echo "==> Reference-fleet bootstrap"
echo "    OU root:          ${OU_ROOT}"
echo "    Schema version:   ${SCHEMA_VERSION}"
echo "    Fleet manifests:  ${FLEET_DIR}"
echo "    Skill archives:   ${ARCHIVES_DIR}"
echo "    Dry run:          ${DRY_RUN}"

if ! weave auth whoami >/dev/null 2>&1; then
    echo "error: not signed in. Run 'weave login' first." >&2
    exit 1
fi

echo
echo "==> Signed in as: $(weave auth whoami 2>/dev/null | head -1)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
run() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "    [dry-run] $*"
    else
        "$@"
    fi
}

weave_apply_if_missing() {
    # Apply a manifest file. Idempotent — weave apply is a no-op if the
    # resource already matches.
    local manifest="$1"
    local label="$2"
    echo "  ${label}"
    if [[ "${DRY_RUN}" == "1" ]]; then
        weave plan -f "${manifest}" 2>&1 | sed 's/^/    /' || true
    else
        weave apply -f "${manifest}" 2>&1 | sed 's/^/    /'
    fi
}

# Build a .zip archive from a skill-archives/<name>/ directory.
# Output: /tmp/<name>-<timestamp>.zip
build_archive() {
    local skill_name="$1"
    local src="${ARCHIVES_DIR}/${skill_name}"
    if [[ ! -d "${src}" ]]; then
        echo "    error: archive source ${src} not found" >&2
        return 1
    fi
    if [[ ! -f "${src}/SKILL.md" ]]; then
        echo "    error: ${src}/SKILL.md missing (required by archive format)" >&2
        return 1
    fi
    local out
    out="/tmp/${skill_name}-$(date +%s).zip"
    (cd "${src}" && zip -q -r "${out}" .)
    echo "${out}"
}

# ---------------------------------------------------------------------------
# Step 1: Apply OU manifests
# ---------------------------------------------------------------------------
echo
echo "==> Step 1/4: Apply OU manifests"
for ou_file in "${FLEET_DIR}"/ous/*.yaml; do
    weave_apply_if_missing "${ou_file}" "$(basename "${ou_file}" .yaml)"
done

# ---------------------------------------------------------------------------
# Step 2: Apply skill shells
# ---------------------------------------------------------------------------
echo
echo "==> Step 2/4: Apply Skill manifests (shells with current_version_id: null)"
for skill_file in "${FLEET_DIR}"/skills/*.yaml; do
    weave_apply_if_missing "${skill_file}" "$(basename "${skill_file}" .yaml)"
done

# ---------------------------------------------------------------------------
# Step 3: Upload-and-activate archives
# ---------------------------------------------------------------------------
echo
echo "==> Step 3/4: Upload + activate skill archives"

# Map of skill-name -> ou-path (parsed from manifest file path)
for skill_file in "${FLEET_DIR}"/skills/*.yaml; do
    skill_name="$(basename "${skill_file}" .yaml)"
    # Extract ou_path from the manifest
    ou_path=$(grep -E "^\s+ou_path:" "${skill_file}" | head -1 | awk '{print $2}')
    address="${ou_path}/${skill_name}"

    archive_path=$(build_archive "${skill_name}")
    echo "  ${address} <- ${archive_path}"
    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "    [dry-run] weave skill upload-and-activate ${address} ${archive_path}"
    else
        weave skill upload-and-activate "${address}" "${archive_path}" 2>&1 | sed 's/^/    /'
    fi
    rm -f "${archive_path}"
done

# ---------------------------------------------------------------------------
# Step 4: Apply agent manifests
# ---------------------------------------------------------------------------
echo
echo "==> Step 4/4: Apply Agent manifests (reference skills by name)"
for agent_file in "${FLEET_DIR}"/agents/*.yaml; do
    weave_apply_if_missing "${agent_file}" "$(basename "${agent_file}" .yaml)"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "==> Done. Fleet summary:"
echo "    OUs:    $(ls -1 "${FLEET_DIR}/ous/"*.yaml 2>/dev/null | wc -l | tr -d ' ')"
echo "    Skills: $(ls -1 "${FLEET_DIR}/skills/"*.yaml 2>/dev/null | wc -l | tr -d ' ')"
echo "    Agents: $(ls -1 "${FLEET_DIR}/agents/"*.yaml 2>/dev/null | wc -l | tr -d ' ')"
echo
echo "Verify:"
echo "    weave get ou"
echo "    weave get skill"
echo "    weave get agent"
