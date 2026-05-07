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
EXPLICIT_DIR=""
EXPLICIT_REPO=""
EXPLICIT_BRANCH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--repo)
            REPO_URL="$2"
            EXPLICIT_REPO=1
            shift 2
            ;;
        -b|--branch)
            REPO_BRANCH="$2"
            EXPLICIT_BRANCH=1
            shift 2
            ;;
        -d|--dir)
            TARGET_DIR="$2"
            EXPLICIT_DIR=1
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

Run modes:
  curl … | bash             Clones to <default-dir>; ideal for one-shot install.
  ./install.sh              When invoked from a valid checkout (compose.yaml,
                            .env.example, .git/ all present alongside this
                            script), and no -d/-r/-b is given, the script
                            installs IN-PLACE — it does not re-clone or change
                            the branch. Pass any flag to force the legacy
                            clone-to-target behaviour.
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
# In-place detection — running from a checkout?
# --------------------------------------------
# When invoked as `./install.sh` from inside a tree that already
# looks like sam-queries, treat that tree as the install dir and
# skip the clone/fetch/checkout/pull dance. `curl … | bash` falls
# through to the legacy clone path because BASH_SOURCE[0] in piped
# mode points at /dev/fd/* or is empty — `[[ -f … ]]` rejects it.
# Any explicit -d/-r/-b also disables in-place detection (the user
# has signalled they want a specific clone target).
IN_PLACE=""
if [[ -z "${EXPLICIT_DIR}" && -z "${EXPLICIT_REPO}" && -z "${EXPLICIT_BRANCH}" ]]; then
    SCRIPT_PATH="${BASH_SOURCE[0]:-}"
    if [[ -n "${SCRIPT_PATH}" && -f "${SCRIPT_PATH}" ]]; then
        SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" >/dev/null 2>&1 && pwd)"
        # `.git` may be a directory (normal clone), a file (worktree, where
        # it points at the real gitdir), or absent. `git rev-parse
        # --is-inside-work-tree` is the canonical "is this a git working
        # tree?" check and handles all three cases. Marker files
        # (compose.yaml, .env.example) make sure it's THIS project, not
        # some unrelated repo that happens to wrap us.
        if [[ -f "${SCRIPT_DIR}/compose.yaml" \
           && -f "${SCRIPT_DIR}/.env.example" ]] \
           && git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            TARGET_DIR="${SCRIPT_DIR}"
            IN_PLACE=1
        fi
    fi
fi

# --------------------------------------------
# Helpers
# --------------------------------------------

# Colour helpers — only emit ANSI escapes when stdout is a TTY and `tput`
# is available with a colour-capable terminfo entry. Falls back to empty
# strings otherwise, so `curl … | bash`, log redirects, and dumb terminals
# all see plain ASCII. Wrapped in a parameter-expansion guard so this is
# safe under both `set -u` (interactive) and the relaxed piped mode above.
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
    C_RESET=$(tput sgr0)
    C_BOLD=$(tput bold)
    C_DIM=$(tput dim)
    C_RED=$(tput setaf 1)
    C_GREEN=$(tput setaf 2)
    C_YELLOW=$(tput setaf 3)
    C_BLUE=$(tput setaf 4)
    C_CYAN=$(tput setaf 6)
else
    C_RESET=""
    C_BOLD=""
    C_DIM=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_BLUE=""
    C_CYAN=""
fi

abort() {
    echo "${C_RED}${C_BOLD}ERROR:${C_RESET} ${*}" >&2
    exit 1
}

need_cmd() {
    command -v "${1}" >/dev/null 2>&1 || abort "Required command '${1}' not found."
}

step() {
    echo "${C_BOLD}${C_CYAN}==>${C_RESET} ${C_BOLD}${*}${C_RESET}"
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
step "Checking requirements"
need_cmd git
need_cmd git-lfs
need_cmd docker
docker info >/dev/null 2>&1 || abort "Docker daemon not running."
check_docker_compose_version

# --------------------------------------------
# Clone or update repo
# --------------------------------------------
if [[ -n "${IN_PLACE}" ]]; then
    step "Using existing checkout at ${TARGET_DIR} (in-place mode)"
    echo "  Detected sam-queries tree alongside this script — skipping clone."
    echo "  Branch and remote are left as-is; run 'git pull' yourself if needed."
elif [[ -d "${TARGET_DIR}/.git" ]]; then
    step "Preparing repository at ${TARGET_DIR}"
    echo "Repository already exists. Updating..."
    git -C "${TARGET_DIR}" fetch --all --quiet
    git -C "${TARGET_DIR}" checkout "${REPO_BRANCH}" --quiet
    git -C "${TARGET_DIR}" pull --ff-only --quiet
else
    step "Preparing repository at ${TARGET_DIR}"
    echo "Cloning branch '${REPO_BRANCH}' from ${REPO_URL} ..."
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

# Ensure git-lfs files are fetched (cheap no-op if already present)
git -C "${TARGET_DIR}" lfs pull

# --------------------------------------------
# Install / Setup step
# You can customize this section
# --------------------------------------------
step "Running setup"

# Example: create env file if not present
if [[ ! -f "${TARGET_DIR}/.env" ]]; then
    echo "Creating default .env"
    cp "${TARGET_DIR}/.env.example" "${TARGET_DIR}/.env" 2>/dev/null || true
fi

# --------------------------------------------
# Final summary
# --------------------------------------------
# Build a fixed-width ASCII banner. Width is hard-coded so the box stays
# aligned regardless of terminal capabilities — `tput cols` would give a
# nicer fit on wide terminals but breaks under `curl … | bash` (no TTY)
# and on systems without ncurses installed.
RULE="------------------------------------------------------------"

echo
echo "${C_GREEN}${C_BOLD}${RULE}${C_RESET}"
echo "${C_GREEN}${C_BOLD}  ✓ Install complete${C_RESET}"
echo "${C_GREEN}${C_BOLD}${RULE}${C_RESET}"
echo
echo "${C_BOLD}Next steps:${C_RESET}"
echo
echo "  ${C_DIM}# 1. Move into the source tree${C_RESET}"
echo "  ${C_CYAN}cd${C_RESET} ${C_YELLOW}\"${TARGET_DIR}\"${C_RESET}"
echo
echo "  ${C_DIM}# 2. Build & launch the stack (waits until every service is healthy)${C_RESET}"
echo "  ${C_DIM}#    First run is slow: image build (~2-3 min) + MySQL backup restore (~30 s).${C_RESET}"
echo "  ${C_CYAN}make${C_RESET} docker-up"
echo
echo "  ${C_DIM}# 3. (optional) Run the test suite + coverage inside docker${C_RESET}"
echo "  ${C_CYAN}make${C_RESET} docker-pytest"
echo
echo "  ${C_DIM}# 4. (optional) Live-sync source changes into the running webdev${C_RESET}"
echo "  ${C_CYAN}make${C_RESET} docker-watch"
echo
echo "${C_BOLD}Once 'make docker-up' reports healthy, open:${C_RESET}"
echo "  ${C_BLUE}http://127.0.0.1:5050/${C_RESET}   ${C_DIM}# webdev (Flask debug, hot-reload)${C_RESET}"
echo "  ${C_BLUE}http://127.0.0.1:7050/${C_RESET}   ${C_DIM}# webapp (gunicorn, prod-like)${C_RESET}"
echo
echo "${C_DIM}See \`make help\` from inside the source tree for other targets.${C_RESET}"
echo
