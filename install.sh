#!/usr/bin/env bash

# --------------------------------------------
# Bootstrap (detect piped execution for `set -u` workaround)
# --------------------------------------------
# If run piped (e.g. `curl ... | bash`), stdin is not a TTY.
# In this case, disable `set -u` to prevent issues with potentially unset variables
# in a non-interactive shell environment. For direct execution, `set -u` remains active.
if [[ -t 0 ]]; then
    # Interactive execution: enable strict error checking
    set -euo pipefail
else
    # Piped execution: disable `set -u` for robustness
    set -eo pipefail
fi

# --------------------------------------------
# Config
# --------------------------------------------
REPO_URL="https://github.com/benkirk/sam-queries.git"
REPO_BRANCH="${REPO_BRANCH:-main}"
TARGET_DIR="${SAMQ_HOME:-${HOME}/codes/project_samuel/${REPO_BRANCH}}"

# --------------------------------------------
# Command-line arguments
# --------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--repo)
            REPO_URL="$2"
            shift 2
            ;;
        -b|--branch)
            REPO_BRANCH="$2"
            shift 2
            ;;
        -d|--dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        -h|--help)
            cat <<EOF
Usage: $0 [options]

Options:
  -r, --repo <url>          Specify the repository URL (default: ${REPO_URL})
  -b, --branch <branch>     Specify the git branch to clone (default: ${REPO_BRANCH})
  -d, --dir <directory>     Specify the installation directory (default: ${TARGET_DIR})
  -h, --help                Show this help message
EOF
            exit 0
            ;;
        *)
            # Unknown option
            shift
            ;;
    esac
done

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

# Check for curl or wget, required for remote fetching
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    abort "Neither 'curl' nor 'wget' found. One is required for remote operations."
fi

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

cat <<EOF
Install complete.
To start services"
  cd \"${TARGET_DIR}\""
  docker compose up --watch

Once all services are up, connect to http://127.0.0.1:5050/user/
EOF
