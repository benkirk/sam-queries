#!/bin/bash
# cirrus_weblog_audit.sh — read-only traffic / rate-limit / security-failure
# audit for the public sam-queries (samuel) release on nwc1.
#
# A companion to cirrus_healthcheck.sh (same idioms, same exit codes). Where the
# health check answers "is the cluster healthy?", this answers "who is hitting
# the public site, and is anything abusive getting through?". It only reads —
# `kubectl logs`, `kubectl get`, and a read-only `redis-cli ZREVRANGE`. It never
# mutates cluster state.
#
# Everything it inspects already exists on the webapp's stdout (≈45-day cluster
# retention) and in Redis; this script just harvests and summarizes it:
#   - gunicorn access logs   (containers/webapp/gunicorn_config.py)
#   - the app request logger  ("METHOD path → status (ms) rid=…")
#   - rate-limit 429 events   (log line + Redis set 'ratelimit:events', DB 1)
#   - auth/CSRF failure logs   (auth/blueprint.py, run.py CSRF handler)
#
# Usage:
#   scripts/cirrus_weblog_audit.sh [options]
#
# Options:
#       --since DURATION  Log look-back window, e.g. 30m, 6h, 2d  (default: 1h)
#       --top N           How many rows in each "top N" list       (default: 15)
#   -n, --namespace NS    Namespace the release lives in   (default: sam-queries)
#   -r, --release    REL  Helm release name                (default: samuel)
#       --context    CTX  kubectl context to target        (default: current)
#       --no-color        Disable ANSI color
#   -v, --verbose         Extra detail per section
#   -h, --help            Show this help
#
# Exit codes:  0 = all PASS · 1 = at least one WARN · 2 = at least one FAIL.
#
# ── Hardening recommendations (NOT enforced by this script) ──────────────────
# This audit only surfaces signals; the layered defenses below are follow-ups
# tracked in docs/plans (see the approved plan). Quick reference:
#   R1  Edge rate limiting — configurable via webapp.ingress.rateLimit in
#       helm/values.yaml (limit-rps / limit-connections / limit-burst-multiplier
#       on the ingress): a per-client-IP guardrail ahead of Flask-Limiter that
#       sheds floods before they cost a worker. Tune to expected burst; rps: 0
#       disables it.
#   R2  Real client IP — the access log now carries xff="…" and the ProxyFix
#       hop depth is set by PROXYFIX_X_FOR (helm webapp.proxyFixForwardedHops).
#       Confirm the count against the logged X-Forwarded-For chain — too high
#       lets clients spoof their IP. Section 2 warns if xff is missing or still
#       collapses to ≤2 IPs.
#   R3  Scheduling — run on a cron/CI runner with --no-color and alert on a
#       non-zero exit (wire into scripts/cron/).
#   R4  CSP reporting — add a report-uri so injection attempts become a
#       server-side signal.
#   R5  Durable logs — stdout (~45d) is the only sink; confirm cluster log
#       shipping for forensics beyond --since reach.

set -euo pipefail

# Load shared helpers: generic presentation/control-flow (common.sh) plus the
# cirrus/k8s layer (cirrus_common.sh) — release/object names, KCTL builders,
# the common-arg parser.
_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/cirrus_common.sh
source "${_LIBDIR}/cirrus_common.sh"

SINCE="1h"
TOP=15

while [[ $# -gt 0 ]]; do
    if handle_common_arg "$@"; then shift "$_CONSUMED"; continue; fi
    case "$1" in
        --since) SINCE="$2"; shift 2;;
        --top)   TOP="$2";   shift 2;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done

[[ "$TOP" =~ ^[0-9]+$ && "$TOP" -ge 1 ]] || { echo "--top must be a positive integer (got '$TOP')" >&2; exit 2; }

setup_colors
build_kctl

# Scanner/abuse fingerprints. Probe PATHS first, then USER-AGENTS.
PROBE_PATH_RE='/\.env|/\.git|/wp-login|/wp-admin|/phpmyadmin|/\.aws|/actuator|/vendor/|/cgi-bin/|\.php([?/]|$)|/xmlrpc|/config\.(json|php)|/\.ssh|/server-status|/solr/|/owa/|/boaform|/\.well-known/security'
# Aggressive scanners / exploitation tools — flagged as WARN.
SCANNER_UA_RE='nikto|sqlmap|nmap|masscan|zgrab|nuclei|wpscan|dirbuster|gobuster|hydra|fimap|acunetix'
# Generic CLI / library / crawler agents — usually benign automation (legit
# uptime monitors and this repo's own scripts use curl), so reported as info.
CLI_UA_RE='python-requests|curl/|wget/|go-http-client|libwww-perl|semrush|censys'

# Convert a kubectl --since duration (e.g. 30m, 6h, 2d, 90s) to minutes;
# echoes "" if unparseable (rate line is then skipped).
since_to_min() {
    local s="$1"
    [[ "$s" =~ ^([0-9]+)([smhd])$ ]] || { echo ""; return; }
    local n="${BASH_REMATCH[1]}" unit="${BASH_REMATCH[2]}"
    case "$unit" in
        s) awk -v n="$n" 'BEGIN{printf "%.3f", n/60}';;
        m) echo "$n";;
        h) echo $((n * 60));;
        d) echo $((n * 1440));;
    esac
}

# Render "count<TAB>value" rows (from sort|uniq -c) as an indented top-N list.
top_list() {
    sort | uniq -c | sort -rn | head -n "$TOP" \
        | awk '{ c=$1; $1=""; sub(/^ /,""); printf "    %8d  %s\n", c, $0 }'
}

# ============================================================================
section "0. Preflight"
# ============================================================================

require_cmd kubectl
require_cmd awk
require_cmd jq

CUR_CTX=$("${KCTL[@]}" config current-context 2>/dev/null || echo "")
echo "  kubectl context: ${CUR_CTX:-<none>}"
if [[ -z "$CUR_CTX" ]]; then
    fail "no kubectl context selected"
    exit 1
elif [[ "$CUR_CTX" != "nwc1" && -z "$CONTEXT" ]]; then
    warn "current context is '$CUR_CTX' (expected 'nwc1'); pass --context to override"
else
    pass "kubectl reachable, context='$CUR_CTX'"
fi

if "${KCTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    pass "namespace '$NAMESPACE' exists"
else
    fail "namespace '$NAMESPACE' not found"
    exit 1
fi

if ! "${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" >/dev/null 2>&1; then
    fail "Deployment '$WEBAPP_NAME' not found — nothing to read logs from"
    exit 1
fi
pass "Deployment '$WEBAPP_NAME' present"
explain "Reading the last '$SINCE' of webapp stdout across all '$WEBAPP_NAME' pods."

# --- harvest once -----------------------------------------------------------
# All signals (gunicorn access + app logger + 429 warnings + auth failures)
# share one stdout stream, so a single fetch feeds every section below.
LOGS=$("${KCTL_NS[@]}" logs -l "app=$WEBAPP_NAME" --since="$SINCE" \
       --all-containers=true --timestamps=false 2>/dev/null || true)

# Normalize gunicorn access lines to "ip<TAB>status<TAB>method<TAB>path<TAB>ua".
# An access line is identified by its quoted request field ("METHOD path HTTP…"),
# which app-logger lines never carry — robust against stray quotes in messages.
ACCESS_TSV=$(printf '%s\n' "$LOGS" | awk -F'"' '
    $2 ~ /^(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS|CONNECT|TRACE) / {
        split($2, r, " "); method = r[1]; path = r[2];
        sub(/\?.*/, "", path);                 # drop query string for grouping
        if (path ~ /^\/api\/v[0-9]+\/health(\/|$)/) next;   # infra probe noise, not traffic
        # Client IP: prefer the leftmost X-Forwarded-For entry (the real client)
        # when the access log carries the xff="…" field; else the socket peer
        # in %(h)s (which behind the ingress is the proxy, not the client).
        split($1, a, " "); ip = a[1];
        xff = (NF >= 8) ? $8 : "";
        if (xff != "" && xff != "-") { split(xff, xa, ","); ip = xa[1]; gsub(/[ \t]/, "", ip) }
        split($3, s, " "); status = s[1];
        ua = $6; if (ua == "") ua = "-";
        printf "%s\t%s\t%s\t%s\t%s\n", ip, status, method, path, ua
    }')
# Guard: grep -c over an empty string still returns 1 line; count real rows.
N_ACCESS=$(printf '%s' "$ACCESS_TSV" | grep -c . || true)

# ============================================================================
section "1. Traffic volume & status mix"
# ============================================================================

if [[ "$N_ACCESS" -eq 0 ]]; then
    info "no access-log lines in the last $SINCE (quiet window, or logs rotated)"
    pass "nothing to analyze"
else
    MIN=$(since_to_min "$SINCE")
    if [[ -n "$MIN" ]]; then
        RATE=$(awk -v n="$N_ACCESS" -v m="$MIN" 'BEGIN{ printf "%.1f", (m>0)? n/m : 0 }')
        echo "  $N_ACCESS requests over $SINCE  (~${RATE}/min)"
    else
        echo "  $N_ACCESS requests over $SINCE"
    fi

    # Status histogram by leading digit.
    read -r C2 C3 C4 C5 CX < <(printf '%s\n' "$ACCESS_TSV" | awk -F'\t' '
        $2 ~ /^[0-9]/ { c[substr($2,1,1)]++ }
        END { printf "%d %d %d %d %d\n", c["2"]+0, c["3"]+0, c["4"]+0, c["5"]+0, (c["1"]+0) }')
    PCT4=$(awk -v a="$C4" -v t="$N_ACCESS" 'BEGIN{ printf "%.1f", (t>0)? 100*a/t : 0 }')
    PCT5=$(awk -v a="$C5" -v t="$N_ACCESS" 'BEGIN{ printf "%.1f", (t>0)? 100*a/t : 0 }')
    echo "    2xx=$C2  3xx=$C3  4xx=$C4 (${PCT4}%)  5xx=$C5 (${PCT5}%)  1xx=$CX"

    if awk -v p="$PCT5" 'BEGIN{exit !(p>5)}'; then
        fail "5xx rate ${PCT5}% (> 5%) — the app is erroring, not just being probed"
    else
        pass "5xx rate ${PCT5}% within tolerance"
    fi
    if awk -v p="$PCT4" 'BEGIN{exit !(p>40)}'; then
        warn "4xx rate ${PCT4}% (> 40%) — heavy probing / scanning likely"
    else
        pass "4xx rate ${PCT4}% within tolerance"
    fi
fi

# ============================================================================
section "2. Top talkers"
# ============================================================================

if [[ "$N_ACCESS" -eq 0 ]]; then
    info "no access lines to rank"
else
    N_IPS=$(printf '%s\n' "$ACCESS_TSV" | cut -f1 | sort -u | grep -c . || true)
    echo "  Distinct source IPs: $N_IPS"
    XFF_SEEN=$(printf '%s\n' "$LOGS" | grep -cE 'xff="[0-9a-fA-F]' || true)
    if [[ "$XFF_SEEN" -eq 0 ]]; then
        warn "access log carries no X-Forwarded-For — IPs above are the in-cluster proxy, not real clients"
        explain "Deploy the R2 gunicorn change (xff logging) so this attributes real clients; see R2 in --help."
    elif [[ "$N_IPS" -le 2 && "$N_ACCESS" -ge 50 ]]; then
        warn "only $N_IPS distinct client IP(s) across $N_ACCESS requests despite X-Forwarded-For"
        explain "Either genuinely few clients, or PROXYFIX_X_FOR hop depth is still mis-set (see R2 in --help)."
    fi

    echo
    echo "  Top $TOP client IPs:"
    printf '%s\n' "$ACCESS_TSV" | cut -f1 | top_list
    echo
    echo "  Top $TOP paths:"
    printf '%s\n' "$ACCESS_TSV" | cut -f4 | top_list
    echo
    echo "  Top $TOP user-agents:"
    printf '%s\n' "$ACCESS_TSV" | cut -f5 | top_list

    SCAN_UA=$(printf '%s\n' "$ACCESS_TSV" | cut -f5 | grep -ciE "$SCANNER_UA_RE" || true)
    CLI_UA=$(printf '%s\n'  "$ACCESS_TSV" | cut -f5 | grep -ciE "$CLI_UA_RE" || true)
    if [[ "$SCAN_UA" -gt 0 ]]; then
        warn "$SCAN_UA request(s) from known exploitation/scanner tools"
        if [[ $VERBOSE -eq 1 ]]; then
            printf '%s\n' "$ACCESS_TSV" | awk -F'\t' -v re="$SCANNER_UA_RE" 'tolower($5) ~ re {print $1"\t"$5}' \
                | sort | uniq -c | sort -rn | head -n "$TOP" | sed 's/^/      /'
        fi
    else
        pass "no exploitation/scanner-tool user-agents"
    fi
    if [[ "$CLI_UA" -gt 0 ]]; then
        info "$CLI_UA request(s) from generic CLI/crawler agents (curl, python-requests, …) — usually benign"
    fi
fi

# ============================================================================
section "3. Scanner / probe path signatures"
# ============================================================================

if [[ "$N_ACCESS" -eq 0 ]]; then
    info "no access lines to scan for probes"
else
    # Exclude the app's own /static/ assets — /static/vendor/* legitimately
    # matches the /vendor/ probe signature but is served, not a probe.
    PROBES=$(printf '%s\n' "$ACCESS_TSV" | awk -F'\t' -v re="$PROBE_PATH_RE" '$4 !~ /^\/static\// && $4 ~ re {print}')
    N_PROBE=$(printf '%s' "$PROBES" | grep -c . || true)
    if [[ "$N_PROBE" -eq 0 ]]; then
        pass "no known vulnerability-probe paths requested"
    else
        warn "$N_PROBE request(s) for known probe paths (triage; not auto-blocked)"
        echo "  Top probe paths:"
        printf '%s\n' "$PROBES" | cut -f4 | top_list
        echo "  Source IPs hitting probe paths:"
        printf '%s\n' "$PROBES" | cut -f1 | top_list
    fi
fi

# ============================================================================
section "4. Rate-limit events (429)"
# ============================================================================

RL_LINES=$(printf '%s\n' "$LOGS" | grep -F 'rate_limit_exceeded' || true)
N_429=$(printf '%s' "$RL_LINES" | grep -c . || true)
if [[ "$N_429" -eq 0 ]]; then
    pass "no rate_limit_exceeded events logged in the last $SINCE"
else
    info "$N_429 rate_limit_exceeded event(s) in the last $SINCE"
    echo "  Top offenders (from app logs):"
    OFFENDERS=$(printf '%s\n' "$RL_LINES" | grep -oE 'actor=[^ ]+' | sed 's/^actor=//')
    printf '%s\n' "$OFFENDERS" | top_list
    # Concentration check: a single actor dominating 429s is a likely single abuser.
    TOPN=$(printf '%s\n' "$OFFENDERS" | sort | uniq -c | sort -rn | head -1 | awk '{print $1}')
    if [[ "${TOPN:-0}" -ge 10 ]] && awk -v a="$TOPN" -v t="$N_429" 'BEGIN{exit !(a > t/2)}'; then
        warn "one actor accounts for $TOPN/$N_429 of the 429s — inspect /admin/htmx/rate-limits"
    fi
fi

# Best-effort Redis enrichment: authoritative offenders straight from the
# 'ratelimit:events' set (survives log rotation). Read-only; skip on any error.
REDIS_POD=$("${KCTL_NS[@]}" get pods -l "app=$REDIS_NAME" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -n "$REDIS_POD" ]]; then
    EVENTS=$("${KCTL_NS[@]}" exec "$REDIS_POD" -- \
             redis-cli -n 1 ZREVRANGE ratelimit:events 0 -1 2>/dev/null || true)
    N_EV=$(printf '%s' "$EVENTS" | grep -c . || true)
    if [[ "$N_EV" -gt 0 ]]; then
        echo
        echo "  Redis 'ratelimit:events' set: $N_EV event(s) retained — top offenders:"
        printf '%s\n' "$EVENTS" \
            | jq -r 'select(length>0) | fromjson | .actor' 2>/dev/null \
            | top_list
    else
        explain "Redis 'ratelimit:events' set is empty (no recent 429s, or memory:// fallback in use)."
    fi
else
    explain "Redis pod not found via app=$REDIS_NAME — skipped authoritative event read."
fi

# ============================================================================
section "5. Auth & security failures"
# ============================================================================

N_LOGIN=$(printf '%s\n' "$LOGS" | grep -cE 'Login failed' || true)
N_OIDC=$(printf '%s\n'  "$LOGS" | grep -cF 'OIDC callback failed' || true)
N_CSRF=$(printf '%s\n'  "$LOGS" | grep -cF 'CSRF failure' || true)
if [[ "$N_ACCESS" -gt 0 ]]; then
    N_401=$(printf '%s\n' "$ACCESS_TSV" | awk -F'\t' '$2=="401"' | grep -c . || true)
    N_403=$(printf '%s\n' "$ACCESS_TSV" | awk -F'\t' '$2=="403"' | grep -c . || true)
else
    N_401=0; N_403=0
fi
echo "  login failures=$N_LOGIN  OIDC failures=$N_OIDC  CSRF failures=$N_CSRF  401=$N_401  403=$N_403"

if [[ "$N_LOGIN" -gt 20 ]]; then
    warn "$N_LOGIN login failures in the last $SINCE — possible credential stuffing"
elif [[ "$N_LOGIN" -gt 0 ]]; then
    info "$N_LOGIN login failure(s) — within normal range"
else
    pass "no login failures"
fi
if [[ "$N_CSRF" -gt 0 ]]; then
    warn "$N_CSRF CSRF failure(s) — usually stale tabs, but watch for forgery attempts"
else
    pass "no CSRF failures"
fi

# ============================================================================
section "Summary"
# ============================================================================

echo "  Results: ${GREEN}${PASS_COUNT} PASS${NC}  ${YELLOW}${WARN_COUNT} WARN${NC}  ${RED}${FAIL_COUNT} FAIL${NC}"
echo
echo "  Next steps:"
echo "    • Rate-limit admin UI: https://${INGRESS_HOST}/admin/htmx/rate-limits"
echo "    • Follow live logs:"
echo "        kubectl -n $NAMESPACE logs -l app=$WEBAPP_NAME -f --all-containers=true"
echo "    • Widen the window with --since (e.g. --since 24h) for a broader picture."

if   [[ $FAIL_COUNT -gt 0 ]]; then exit 2
elif [[ $WARN_COUNT -gt 0 ]]; then exit 1
else                               exit 0
fi
