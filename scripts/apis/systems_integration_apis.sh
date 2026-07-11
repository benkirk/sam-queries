#!/bin/bash
# systems_integration_apis.sh — worked example + smoke test for the five SAM
# Systems Integration APIs (directory_access, project_access, fstree_access,
# queue, wallclock_exemption).
#
# For each endpoint it runs the real consumer workflow — download → refresh →
# re-download — authenticating with an API key over HTTP Basic Auth. Read it
# top-to-bottom to see exactly how a scheduler / LDAP / provisioning client is
# expected to call these APIs. It doubles as a post-deploy smoke test: every
# endpoint must authenticate, return valid JSON, and honor cache refresh.
#
# The only side effect is POSTing to each endpoint's idempotent /refresh (clears
# the 5-minute response cache). Otherwise read-only.
#
# Credentials come from the environment (keep them out of shell history):
#   SAM_API_USER   API-key username (required)
#   SAM_API_PASS   API-key password (required)
#   SAM_API_BASE   Base URL (optional; default https://samuel.k8s.ucar.edu)
#
# Generate a key with `python scripts/gen_api_key.py` and add its bcrypt hash to
# the deployment's API_KEYS config.
#
# Usage:
#   SAM_API_USER=... SAM_API_PASS=... scripts/apis/systems_integration_apis.sh [options] [api ...]
#
# Arguments:
#   api ...               One or more API names to test (default: all five).
#                         Choices: directory_access project_access fstree_access
#                                  queue wallclock_exemption
#
# Options:
#       --base URL        Override SAM_API_BASE
#   -o, --outdir DIR      Save downloaded JSON here (retained). Default: a temp
#                         dir removed on exit.
#       --no-color        Disable ANSI color
#   -v, --verbose         Echo the (password-redacted) curl commands + summaries
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

# --- All five Systems Integration APIs, in doc order ------------------------
ALL_APIS=(directory_access project_access fstree_access queue wallclock_exemption)

# jq expression per API: prints a one-line human summary of a valid response,
# or exits non-zero (→ FAIL) if the top-level shape is wrong. Keeps the sanity
# check and the summary in one place.
api_summary_expr() {
    case "$1" in
        directory_access)
            echo '(.accessBranchDirectories | length) as $n
                  | if $n > 0 then "\($n) access branch(es)"
                    else error("no accessBranchDirectories") end' ;;
        project_access)
            # Branch-keyed object (hpc/hpc-data/hpc-dev); sum project counts.
            echo 'if type=="object" and has("hpc")
                  then "\([.[] | length] | add) project group(s) across \(keys | length) branch(es)"
                  else error("missing hpc branch key") end' ;;
        fstree_access)
            echo 'if .name=="fairShareTree"
                  then "\(.facilities | length) facilit(ies)"
                  else error("name != fairShareTree") end' ;;
        queue)
            echo 'if .name=="queues"
                  then "\(.resources | length) resource(s), \([.resources[].queues | length] | add // 0) queue(s)"
                  else error("name != queues") end' ;;
        wallclock_exemption)
            echo 'if .name=="exemptions"
                  then "\(.resources | length) resource(s), \([.resources[].queues[].limits | length] | add // 0) exemption(s)"
                  else error("name != exemptions") end' ;;
        *) echo 'error("unknown api")' ;;
    esac
}

# --- defaults + arg parsing -------------------------------------------------
SAM_API_BASE="${SAM_API_BASE:-https://samuel.k8s.ucar.edu}"
OUTDIR=""
KEEP_OUTDIR=0
SELECTED=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)       SAM_API_BASE="$2"; shift 2 ;;
        -o|--outdir)  OUTDIR="$2"; KEEP_OUTDIR=1; shift 2 ;;
        --no-color)   USE_COLOR=0; shift ;;
        -v|--verbose) VERBOSE=1; shift ;;
        -h|--help)    usage_from_header "$0"; exit 0 ;;
        -*)           echo "Unknown option: $1" >&2; exit 2 ;;
        *)            SELECTED+=("$1"); shift ;;
    esac
done

setup_colors

# Validate/normalize the requested API list (default: all).
if [[ ${#SELECTED[@]} -eq 0 ]]; then
    SELECTED=("${ALL_APIS[@]}")
else
    for want in "${SELECTED[@]}"; do
        ok=0
        for known in "${ALL_APIS[@]}"; do [[ "$want" == "$known" ]] && ok=1 && break; done
        [[ $ok -eq 1 ]] || die2 "unknown api '$want' (choices: ${ALL_APIS[*]})"
    done
fi

SAM_API_BASE="${SAM_API_BASE%/}"   # trim trailing slash for clean concatenation

# ============================================================================
section "0. Prerequisites"
# ============================================================================

require_cmd curl
require_cmd jq

[[ -n "${SAM_API_USER:-}" ]] || die2 "SAM_API_USER is not set (export your API-key username)"
[[ -n "${SAM_API_PASS:-}" ]] || die2 "SAM_API_PASS is not set (export your API-key password)"

# Downloads land in a temp dir (cleaned on exit) unless --outdir was given.
if [[ -n "$OUTDIR" ]]; then
    mkdir -p "$OUTDIR"
else
    OUTDIR="$(mktemp -d)"
    trap 'rm -rf "$OUTDIR"' EXIT
fi

info "base URL : $SAM_API_BASE"
info "auth     : Basic (-u \"\$SAM_API_USER:***\") as '${SAM_API_USER}'"
info "outdir   : $OUTDIR$([[ $KEEP_OUTDIR -eq 1 ]] && echo '' || echo ' (temporary)')"
info "apis     : ${SELECTED[*]}"
pass "prerequisites OK — curl + jq present, credentials set"
explain "Every call authenticates with HTTP Basic Auth; curl's -u sends the
  Authorization: Basic header. The password is read from \$SAM_API_PASS and never
  printed. POST .../refresh clears the server's 5-minute response cache."

# --------------------------------------------------------------------------
# http_get URL OUTFILE   → prints the HTTP status code; body written to OUTFILE.
# http_post URL          → prints "<code> <body>" for a refresh POST.
# Credentials are passed via curl -u; the password never appears in argv echoes.
# --------------------------------------------------------------------------
# Verbose command echoes and curl's own error text go to stderr so command
# substitution captures only the curl -w output (HTTP code / body). Each helper
# ends with `|| true` so a hard curl failure (timeout, DNS) still yields just the
# "000" status code under `set -e` rather than aborting the run.
http_get() {
    local url="$1" out="$2"
    local errsink=/dev/null; [[ $VERBOSE -eq 1 ]] && errsink=/dev/stderr
    [[ $VERBOSE -eq 1 ]] && note "curl -sS -u \"\$SAM_API_USER:***\" \"$url\"" >&2
    curl -sS -m 60 -u "${SAM_API_USER}:${SAM_API_PASS}" \
         -H 'Accept: application/json' \
         -w '%{http_code}' -o "$out" "$url" 2>"$errsink" || true
}

http_post() {
    local url="$1"
    local errsink=/dev/null; [[ $VERBOSE -eq 1 ]] && errsink=/dev/stderr
    [[ $VERBOSE -eq 1 ]] && note "curl -sS -X POST -u \"\$SAM_API_USER:***\" \"$url\"" >&2
    curl -sS -m 60 -X POST -u "${SAM_API_USER}:${SAM_API_PASS}" \
         -H 'Accept: application/json' \
         -w ' %{http_code}' "$url" 2>"$errsink" || true
}

# --------------------------------------------------------------------------
# smoke_one API — run download → refresh → re-download for a single endpoint.
# Increments PASS/WARN/FAIL via the lib primitives; never aborts the whole run.
# --------------------------------------------------------------------------
smoke_one() {
    local api="$1"
    local path="/api/v1/${api}/"
    local url="${SAM_API_BASE}${path}"
    local refresh_url="${SAM_API_BASE}${path}refresh"
    local f1="${OUTDIR}/${api}.1.json"
    local f2="${OUTDIR}/${api}.2.json"
    local expr; expr="$(api_summary_expr "$api")"

    section "${api}"
    explain "GET ${path}  →  POST ${path}refresh  →  GET ${path} (recompute)"

    # 1) Download ------------------------------------------------------------
    local code summary
    code="$(http_get "$url" "$f1")"
    if [[ "$code" != "200" ]]; then
        fail "${api}: download GET returned HTTP ${code}"
        [[ $VERBOSE -eq 1 && -s "$f1" ]] && run head -c 300 "$f1"
        return
    fi
    if ! summary="$(jq -er "$expr" "$f1" 2>/dev/null)"; then
        fail "${api}: response is not valid/expected JSON (shape check failed)"
        [[ $VERBOSE -eq 1 ]] && run head -c 300 "$f1"
        return
    fi
    local size1; size1="$(wc -c < "$f1" | tr -d ' ')"
    pass "download OK — HTTP 200, ${size1} bytes, ${summary}"

    # 2) Refresh -------------------------------------------------------------
    local resp rcode rbody
    resp="$(http_post "$refresh_url")"
    rcode="${resp##* }"        # trailing "<space><code>"
    rbody="${resp% *}"
    if [[ "$rcode" != "200" ]]; then
        fail "${api}: refresh POST returned HTTP ${rcode}"
        return
    fi
    if [[ "$(echo "$rbody" | jq -r '.status // empty' 2>/dev/null)" != "ok" ]]; then
        fail "${api}: refresh POST did not return {\"status\":\"ok\"} (got: ${rbody})"
        return
    fi
    pass "refresh OK — HTTP 200, {\"status\":\"ok\"}"

    # 3) Re-download ---------------------------------------------------------
    code="$(http_get "$url" "$f2")"
    if [[ "$code" != "200" ]]; then
        fail "${api}: re-download GET returned HTTP ${code}"
        return
    fi
    if ! summary="$(jq -er "$expr" "$f2" 2>/dev/null)"; then
        fail "${api}: re-download response failed shape check"
        return
    fi
    local size2; size2="$(wc -c < "$f2" | tr -d ' ')"
    pass "re-download OK — HTTP 200, ${size2} bytes, ${summary}"

    # Post-refresh the recomputed body should match the first (data rarely
    # changes in the sub-second window). A diff is informational, not a failure.
    if diff -q <(jq -S -c . "$f1") <(jq -S -c . "$f2") >/dev/null 2>&1; then
        pass "recomputed response identical to the pre-refresh download"
    else
        warn "response changed after refresh — likely a concurrent data update, not a defect"
    fi
}

for api in "${SELECTED[@]}"; do
    smoke_one "$api"
done

# ============================================================================
section "Summary"
# ============================================================================
if [[ $KEEP_OUTDIR -eq 1 ]]; then
    info "downloaded JSON retained in: $OUTDIR"
    explain "Inspect a payload, e.g.:  jq . $OUTDIR/queue.1.json"
fi
verdict_exit
