#!/usr/bin/env bash
set -euo pipefail

# --------------------------------------------
# Config
# --------------------------------------------
REPO_URL="https://github.com/benkirk/sam-queries.git"
REPO_BRANCH="${REPO_BRANCH:-self-install}"
TARGET_DIR="${SAMQ_HOME:-$HOME/codes3/sam-queries-foo}"

# --------------------------------------------
# Helpers
# --------------------------------------------
abort() {
    echo "ERROR: ${*}" >&2
    exit 1
}

need_cmd() {
    command -v "${1}" >/dev/null 2>&1 || abort "Required command '${1}' not found."
}

check_docker_compose_version() {
    # Expecting "Docker Compose version v2.40.x" or similar
    local ver
    ver=$(docker compose version --short 2>/dev/null || true)
    [[ -z "${ver}" ]] && abort "'docker compose' command not available."

    # Normalize like "2.40.0"
    local major minor
    major=$(echo "${ver}" | cut -d. -f1)
    minor=$(echo "${ver}" | cut -d. -f2)

    if [[ "${major}" -lt 2 || ( "${major}" -eq 2 && "${minor}" -lt 29 ) ]]; then
        abort "docker compose version 2.40 or newer required; found ${ver}"
    fi
}

# --------------------------------------------
# Checks
# --------------------------------------------
echo "--- Checking requirements ---"
need_cmd git
need_cmd git-lfs
need_cmd docker
docker info >/dev/null 2>&1 || abort "Docker daemon not running."
check_docker_compose_version

# --------------------------------------------
# Clone or update repo
# --------------------------------------------
echo "--- Preparing repository at ${TARGET_DIR} ---"

if [[ -d "${TARGET_DIR}/.git" ]]; then
    echo "Repository already exists. Updating..."
    git -C "${TARGET_DIR}" fetch --all --quiet
    git -C "${TARGET_DIR}" checkout "${REPO_BRANCH}" --quiet
    git -C "${TARGET_DIR}" pull --ff-only --quiet
else
    echo "Cloning branch '${REPO_BRANCH}' from ${REPO_URL} ..."
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

# Ensure git-lfs files are fetched
git -C "${TARGET_DIR}" lfs pull

# --------------------------------------------
# Install / Setup step
# You can customize this section
# --------------------------------------------
echo "--- Running setup ---"

# Example: create env file if not present
if [[ ! -f "${TARGET_DIR}/.env" ]]; then
    echo "Creating default .env"
    cp "${TARGET_DIR}/.env.example" "${TARGET_DIR}/.env" 2>/dev/null || true
fi

echo "Install complete."
echo "To start services:"
echo "  cd \"${TARGET_DIR}\""
echo "  docker compose up --watch"
