#!/bin/bash
# clear_caches.sh — worked example + smoke test for the SAM Admin Cache
# Refresh API (POST /api/v1/admin/cache/refresh).
#
# SAM's webapp fronts four cache categories behind one facade — the Flask
# HTTP-response cache (flask), the matplotlib chart SVG caches (chart), the
# allocation-usage cache (usage), and the filesystem-scan cache (scans). This
# single endpoint invalidates any one of them, or all at once. Read it
# top-to-bottom to see exactly how an operator / cron / deploy hook is expected
# to call it, authenticating with an API key over HTTP Basic Auth.
#
# Unlike scripts/apis/systems_integration_apis.sh (read-only apart from the
# idempotent per-resource /refresh), this script's WHOLE PURPOSE is the side
# effect: it clears live caches. On a shared/production deployment that briefly
# raises cache-miss latency until the caches warm again. It is safe (caches are
# derived state) but not read-only — run it deliberately.
#
# Credentials come from the environment (keep them out of shell history):
#   SAM_API_USER   API-key username (required)
#   SAM_API_PASS   API-key password (required)
#   SAM_API_BASE   Base URL (optional; default https://samuel.k8s.ucar.edu)
#
# Generate a key with `python scripts/gen_api_key.py` and add its bcrypt hash to
# the deployment's API_KEYS config. (Note: the Basic-auth token path clears the
# cache regardless of the key's role; the SYSTEM_ADMIN permission gate applies
# only to the browser/session path — the Admin > Configuration "Clear…" button.)
#
# Usage:
#   SAM_API_USER=... SAM_API_PASS=... scripts/apis/clear_caches.sh [options] [category ...]
#
# Arguments:
#   category ...          One or more categories to clear (default: all of them,
#                         each demonstrated as a separate scoped call).
#                         Choices: all flask chart usage scans
#                         'all' issues the unscoped call that clears everything.
#
# Options:
#       --base URL        Override SAM_API_BASE
#       --no-negative     Skip the invalid-category (HTTP 400) negative test
#       --no-color        Disable ANSI color
#   -v, --verbose         Echo the (password-redacted) curl commands + bodies
#   -h, --help            Show this help
#
# Exit codes: 0 all pass · 1 ≥1 warn · 2 ≥1 fail (or a precondition error)

set -euo pipefail

# Shared presentation/control-flow helpers (colors, section/pass/warn/fail,
# require_cmd, usage_from_header, verdict_exit). Lives one level up in ../lib.
_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${_LIBDIR}/common.sh"

# Precondition failures exit 2 to match this script's documented ladder
# (0 pass / 1 warn / 2 fail); the lib's die() exits 1, which would read as "warn".
die2() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 2; }

# The four categories the facade understands, in the order stats() reports them.
CATEGORIES=(flask chart usage scans)
# The demonstration targets: 'all' (unscoped) plus each category (scoped).
ALL_TARGETS=(all "${CATEGORIES[@]}")

# --- defaults + arg parsing -------------------------------------------------
SAM_API_BASE="${SAM_API_BASE:-https://samuel.k8s.ucar.edu}"
RUN_NEGATIVE=1
SELECTED=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)         SAM_API_BASE="$2"; shift 2 ;;
        --no-negative)  RUN_NEGATIVE=0; shift ;;
        --no-color)     USE_COLOR=0; shift ;;
        -v|--verbose)   VERBOSE=1; shift ;;
        -h|--help)      usage_from_header "$0"; exit 0 ;;
        -*)             echo "Unknown option: $1" >&2; exit 2 ;;
        *)              SELECTED+=("$1"); shift ;;
    esac
done

setup_colors

# Validate/normalize the requested target list (default: all).
if [[ ${#SELECTED[@]} -eq 0 ]]; then
    SELECTED=("${ALL_TARGETS[@]}")
else
    for want in "${SELECTED[@]}"; do
        ok=0
        for known in "${ALL_TARGETS[@]}"; do [[ "$want" == "$known" ]] && ok=1 && break; done
        [[ $ok -eq 1 ]] || die2 "unknown category '$want' (choices: ${ALL_TARGETS[*]})"
    done
fi

SAM_API_BASE="${SAM_API_BASE%/}"   # trim trailing slash for clean concatenation
REFRESH_URL="${SAM_API_BASE}/api/v1/admin/cache/refresh"

# ============================================================================
section "0. Prerequisites"
# ============================================================================

require_cmd curl
require_cmd jq

[[ -n "${SAM_API_USER:-}" ]] || die2 "SAM_API_USER is not set (export your API-key username)"
[[ -n "${SAM_API_PASS:-}" ]] || die2 "SAM_API_PASS is not set (export your API-key password)"

info "base URL : $SAM_API_BASE"
info "endpoint : POST /api/v1/admin/cache/refresh"
info "auth     : Basic (-u \"\$SAM_API_USER:***\") as '${SAM_API_USER}'"
info "targets  : ${SELECTED[*]}"
pass "prerequisites OK — curl + jq present, credentials set"
explain "Each call authenticates with HTTP Basic Auth; curl's -u sends the
  Authorization: Basic header. The password is read from \$SAM_API_PASS and never
  printed. An unscoped POST clears everything; ?category=X scopes the clear."

# --------------------------------------------------------------------------
# http_post URL   -> prints "<body> <code>" (JSON body, then space, then the
# HTTP status). Credentials go via curl -u; the password never appears in argv
# echoes. Trailing `|| true` keeps a hard curl failure (timeout, DNS) from
# aborting under `set -e` — it yields a "000" status the caller treats as FAIL.
# --------------------------------------------------------------------------
http_post() {
    local url="$1"
    local errsink=/dev/null; [[ $VERBOSE -eq 1 ]] && errsink=/dev/stderr
    [[ $VERBOSE -eq 1 ]] && note "curl -sS -X POST -u \"\$SAM_API_USER:***\" \"$url\"" >&2
    curl -sS -m 60 -X POST -u "${SAM_API_USER}:${SAM_API_PASS}" \
         -H 'Accept: application/json' \
         -w ' %{http_code}' "$url" 2>"$errsink" || true
}

# --------------------------------------------------------------------------
# clear_one TARGET — issue one clear ('all' -> unscoped; else ?category=TARGET),
# validate {"status":"ok"} + that .cleared holds exactly the expected keys, and
# print a per-category count summary. Never aborts the whole run.
# --------------------------------------------------------------------------
clear_one() {
    local target="$1" url expected
    if [[ "$target" == "all" ]]; then
        url="$REFRESH_URL"
        expected="$(printf '%s\n' "${CATEGORIES[@]}" | sort | paste -sd, -)"
    else
        url="${REFRESH_URL}?category=${target}"
        expected="$target"
    fi

    section "clear: ${target}"
    explain "POST /api/v1/admin/cache/refresh$([[ "$target" != all ]] && echo "?category=${target}")"

    local resp code body
    resp="$(http_post "$url")"
    code="${resp##* }"     # trailing " <code>"
    body="${resp% *}"

    if [[ "$code" != "200" ]]; then
        fail "${target}: refresh POST returned HTTP ${code}"
        [[ $VERBOSE -eq 1 && -n "$body" ]] && run bash -c "echo '$body' | head -c 300"
        return
    fi
    if [[ "$(echo "$body" | jq -r '.status // empty' 2>/dev/null)" != "ok" ]]; then
        fail "${target}: did not return {\"status\":\"ok\"} (got: ${body})"
        return
    fi

    local got
    got="$(echo "$body" | jq -r '.cleared | keys | sort | join(",")' 2>/dev/null || echo '')"
    if [[ "$got" != "$expected" ]]; then
        fail "${target}: cleared categories {${got}} != expected {${expected}}"
        return
    fi

    # One-line per-category count summary, e.g. "flask=12 chart=3 usage=0 scans=1".
    local counts
    counts="$(echo "$body" | jq -r '.cleared | to_entries | map("\(.key)=\(.value)") | join(" ")')"
    pass "cleared OK — HTTP 200, {${counts}}"
}

for target in "${SELECTED[@]}"; do
    clear_one "$target"
done

# --------------------------------------------------------------------------
# Negative test: an unrecognized category must be rejected with HTTP 400
# rather than silently clearing everything. Demonstrates the endpoint's input
# validation. Skip with --no-negative.
# --------------------------------------------------------------------------
if [[ $RUN_NEGATIVE -eq 1 ]]; then
    section "invalid category (negative test)"
    explain "POST /api/v1/admin/cache/refresh?category=bogus  →  expect HTTP 400"
    resp="$(http_post "${REFRESH_URL}?category=bogus")"
    code="${resp##* }"
    if [[ "$code" == "400" ]]; then
        pass "rejected as expected — HTTP 400 on unknown category"
    else
        fail "unknown category returned HTTP ${code} (expected 400)"
    fi
fi

# ============================================================================
section "Summary"
# ============================================================================
verdict_exit
