# shellcheck shell=bash
#
# common.sh — generic, sourceable helpers shared by SAM operational scripts.
#
# This is the LOWEST layer: pure presentation + control-flow helpers with no
# Kubernetes / cirrus knowledge (that lives in cirrus_common.sh). Source it
# from any script:
#
#   _LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
#   # shellcheck source=lib/common.sh
#   source "${_LIBDIR}/common.sh"
#
# Provides:
#   - setup_colors            TTY- and --no-color-aware ANSI palette
#   - say/info/ok/note/die     plain log primitives (no verdict counters)
#   - section/explain/pass/    verdict primitives (maintain PASS/WARN/FAIL
#     warn/fail/run/require_cmd  counters)
#   - verdict_exit             print the tally + exit 2/1/0
#   - usage_from_header         print the script's leading comment block
#   - repo_paths               set SCRIPT_DIR / REPO_ROOT
#
# Sourcing has no side effects beyond defining functions and initializing the
# shared defaults below; callers parse args, then call setup_colors.

# --- shared defaults (callers may override via args) ------------------------
USE_COLOR="${USE_COLOR:-1}"
VERBOSE="${VERBOSE:-0}"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

# Color vars are empty until setup_colors runs (so unset-color is the default).
BLUE=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; BOLD=""; NC=""

# --------------------------------------------------------------------------
# setup_colors
#
# Enable ANSI color only when all of: USE_COLOR=1, the de-facto NO_COLOR env
# var is unset, and stdout is a TTY. Uses $'...' ANSI-C quoting so the escapes
# are literal — `echo`/`echo -e`/`printf '%s'` all render them correctly.
# Call AFTER argument parsing so --no-color can take effect.
# --------------------------------------------------------------------------
setup_colors() {
    if [[ "${USE_COLOR:-1}" -eq 1 && -z "${NO_COLOR:-}" && -t 1 ]]; then
        BLUE=$'\033[0;34m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
        RED=$'\033[0;31m';  CYAN=$'\033[0;36m';  BOLD=$'\033[1m'; NC=$'\033[0m'
    else
        BLUE=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; BOLD=""; NC=""
    fi
}

# --- plain log primitives (no counters) -------------------------------------
say()  { echo -e "$*"; }
note() { echo -e "  ${CYAN}↳ $*${NC}"; }
die()  { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

# --- verdict primitives (maintain PASS/WARN/FAIL counters) ------------------
section() { echo; echo -e "${BOLD}${BLUE}═══ $* ═══${NC}"; }
explain() { echo -e "  ${CYAN}↳ $*${NC}"; }
pass() { echo -e "  ${GREEN}✔ PASS${NC} — $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn() { echo -e "  ${YELLOW}⚠ WARN${NC} — $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail() { echo -e "  ${RED}✘ FAIL${NC} — $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
info() { echo -e "  ${CYAN}ℹ${NC} $*"; }

# Run a command and indent its output for readability (never aborts the run).
run() { "$@" 2>&1 | sed 's/^/    /' || true; }

# Verdict-style hard dependency check: FAIL + exit 1 when missing.
require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { fail "$1 not found in PATH"; exit 1; }
}

# --------------------------------------------------------------------------
# verdict_exit
#
# Print "  Results: N PASS  N WARN  N FAIL" and exit with the conventional
# code: 2 if any FAIL, 1 if any WARN, else 0. Convenience for check scripts
# that want the standard summary; scripts with a bespoke summary can inline
# the same exit ladder instead.
# --------------------------------------------------------------------------
verdict_exit() {
    echo "  Results: ${GREEN}${PASS_COUNT} PASS${NC}  ${YELLOW}${WARN_COUNT} WARN${NC}  ${RED}${FAIL_COUNT} FAIL${NC}"
    if   [[ $FAIL_COUNT -gt 0 ]]; then exit 2
    elif [[ $WARN_COUNT -gt 0 ]]; then exit 1
    else                               exit 0
    fi
}

# --------------------------------------------------------------------------
# usage_from_header [file]
#
# Print the leading comment block (everything after the shebang up to the
# first non-comment line), stripping the leading "# " / "#". Lets every script
# keep its usage text in one place — the header comment.
# --------------------------------------------------------------------------
usage_from_header() {
    local f="${1:-$0}"
    awk 'NR==1{next} /^#/{sub(/^#[ ]?/,""); print; next} {exit}' "$f"
}

# --------------------------------------------------------------------------
# repo_paths
#
# Resolve and export SCRIPT_DIR (dir of the calling script) and REPO_ROOT
# (git toplevel, falling back to SCRIPT_DIR/..). Safe to call once near the
# top of a script that needs repo-relative paths.
# --------------------------------------------------------------------------
repo_paths() {
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")" && pwd)"
    REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"
    [[ -n "$REPO_ROOT" ]] || REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
    export SCRIPT_DIR REPO_ROOT
}
