#!/usr/bin/env bash
#
# zap_probe_docker.sh — Dockerized OWASP ZAP passive/active scan of the SAM webapp.
#
# A rerunnable, install-free companion to the native
# docs/nrit-review-2026-05/run-zap-manual-explore.sh. It needs only Docker —
# no local ZAP/Java install — and pulls everything from the official image
# ghcr.io/zaproxy/zaproxy:stable (bundles zap-baseline.py / zap-full-scan.py).
#
# DEFAULT (managed) mode, no args:
#   1. Spins up a *throwaway* webdev container with dev auto-login enabled, on a
#      dedicated host port (default 5051) so it never collides with a webdev you
#      already run on 5050. Auto-login (DISABLE_AUTH / DEV_AUTO_LOGIN_USER) is a
#      RUNTIME override only — it is never written into committed compose.
#   2. Waits for health, then asserts auto-login is live (GET /admin -> 200).
#   3. Runs ZAP against it (passive baseline by default) and writes a timestamped
#      HTML + JSON report into docs/nrit-review-2026-05/.
#   4. Tears the throwaway container down on exit.
#
# EXTERNAL-target mode (-t URL): skip all lifecycle management and scan a URL you
#   already have running. NOTE: the LIVE authenticated site (samuel.k8s.ucar.edu)
#   cannot be scanned this way on macOS — a container can't share the host VPN and
#   the baseline scanner can't perform the 2FA/OIDC login. For that, use the native
#   run-zap-manual-explore.sh instead.
#
# Usage:
#   ./scripts/zap_probe_docker.sh                  # managed passive baseline (default)
#   ./scripts/zap_probe_docker.sh --scoped         # time-boxed ACTIVE scan (local only)
#   ./scripts/zap_probe_docker.sh --scoped --max-mins 5
#   ./scripts/zap_probe_docker.sh --full           # WHOLE-APP active scan (slow, local only)
#   ./scripts/zap_probe_docker.sh -u bdobbins       # auto-login as a different user
#   ./scripts/zap_probe_docker.sh -p 5055 --keep-app
#   ./scripts/zap_probe_docker.sh -t http://host.docker.internal:5050   # external target
#
# Scan modes (default = passive baseline):
#   --scoped : ACTIVE scan via ZAP's Automation Framework, scoped to the
#              parameterized endpoints and HARD-CAPPED at --max-mins (default 10).
#              Excludes the heavy read-only paths (/status, /admin/htmx, /static,
#              /auth/logout) that make --full drag for an hour. Best signal/minute.
#   --full   : ACTIVE scan of the WHOLE app (zap-full-scan.py). Thorough but slow
#              and unbounded — can run very long on heavy dashboards.
# Both active modes are HARD-GUARDED to localhost (never prod).
#
# Prerequisites: Docker running; the `webdev` image built
#   (docker compose build webdev) — managed mode builds on demand if missing.

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────
BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN}OK:${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*" >&2; }
die()   { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/compose.yaml"
WRKDIR="${REPO_ROOT}/docs/nrit-review-2026-05"   # holds gen.conf + the saved baseline

# ── Defaults / config ─────────────────────────────────────────────────────
ZAP_IMAGE="ghcr.io/zaproxy/zaproxy:stable"
CONTAINER_NAME="samuel-zap-webdev"
APP_PORT=5051
ZAP_USER="benkirk"
FULL=""
SCOPED=""
MAX_MINS=10        # --scoped: hard cap on active-scan minutes
SPIDER_MINS=3      # --scoped: cap on spider minutes
KEEP_APP=""
EXTERNAL_TARGET=""
STAMP="$(date +%Y%m%d-%H%M%S)"

# Print the leading comment header (everything from line 2 up to the first
# non-comment line), stripping the "# " prefix. Robust to header length.
usage() { awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "${BASH_SOURCE[0]}"; exit "${1:-0}"; }

# ── Arg parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target)   EXTERNAL_TARGET="${2:?-t needs a URL}"; shift 2 ;;
        -u|--user)     ZAP_USER="${2:?-u needs a username}"; shift 2 ;;
        -p|--port)     APP_PORT="${2:?-p needs a port}"; shift 2 ;;
        --full)        FULL=1; shift ;;
        --scoped)      SCOPED=1; shift ;;
        --max-mins)    MAX_MINS="${2:?--max-mins needs a number}"; shift 2 ;;
        --keep-app)    KEEP_APP=1; shift ;;
        -h|--help)     usage 0 ;;
        *)             die "Unknown argument: $1  (try -h)" ;;
    esac
done

[[ "$MAX_MINS" =~ ^[0-9]+$ && "$MAX_MINS" -ge 1 ]] || die "--max-mins must be a positive integer (got '${MAX_MINS}')"

command -v docker >/dev/null 2>&1 || die "docker not found in PATH."
docker info >/dev/null 2>&1 || die "Docker daemon not reachable — is Docker running?"
[[ -f "${WRKDIR}/gen.conf" ]] || die "Missing ruleset: ${WRKDIR}/gen.conf"

# ── Pick scan mode + report names ─────────────────────────────────────────
# Modes are mutually exclusive; --scoped wins if combined with --full.
if [[ -n "$SCOPED" ]]; then
    MODE="scoped"; SCANNER_LABEL="zap.sh -autorun (Automation Framework)"; PREFIX="zap-scoped"
    SCAN_DESC="scoped + time-boxed ACTIVE scan (active ≤${MAX_MINS}m; excludes heavy/static paths)"
    ACTIVE=1
elif [[ -n "$FULL" ]]; then
    MODE="full"; SCANNER_LABEL="zap-full-scan.py"; PREFIX="zap-fullscan"
    SCAN_DESC="WHOLE-APP active scan (sends attack payloads; slow/unbounded)"
    ACTIVE=1
else
    MODE="baseline"; SCANNER_LABEL="zap-baseline.py"; PREFIX="zap-baseline"
    SCAN_DESC="passive baseline scan"
    ACTIVE=""
fi
REPORT_HTML="${PREFIX}-rescan-${STAMP}.html"
REPORT_JSON="${PREFIX}-rescan-${STAMP}.json"
PLAN_FILE="${WRKDIR}/zap-autorun-${STAMP}.yaml"   # only used by --scoped

# ── Resolve target + safety guards ────────────────────────────────────────
MANAGED=1
if [[ -n "$EXTERNAL_TARGET" ]]; then
    MANAGED=""
    TARGET="$EXTERNAL_TARGET"
    if [[ -n "$ACTIVE" ]]; then
        # An active scan must never touch anything but a local throwaway app.
        host="$(printf '%s' "$TARGET" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')"
        case "$host" in
            localhost|127.0.0.1|host.docker.internal) : ;;
            *) die "Refusing active scan (--scoped/--full) against non-local host '${host}'.
       Active scans send real attack payloads and must never hit prod
       (e.g. samuel.k8s.ucar.edu). Drop the active flag, or target localhost." ;;
        esac
    fi
else
    TARGET="http://host.docker.internal:${APP_PORT}"
fi

# ── Cleanup ───────────────────────────────────────────────────────────────
cleanup() {
    rm -f "$PLAN_FILE" 2>/dev/null || true
    if [[ -n "$MANAGED" && -z "$KEEP_APP" ]]; then
        echo ""
        info "Stopping throwaway webapp container..."
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    elif [[ -n "$MANAGED" && -n "$KEEP_APP" ]]; then
        echo ""
        warn "Leaving '${CONTAINER_NAME}' running (--keep-app). Remove with:"
        echo "  docker rm -f ${CONTAINER_NAME}"
    fi
}
trap cleanup EXIT

# ── Banner ────────────────────────────────────────────────────────────────
echo ""
info "Dockerized ZAP probe"
echo "  Mode    : ${MODE} — ${SCAN_DESC}"
echo "  Scanner : ${SCANNER_LABEL}"
echo "  Target  : ${TARGET}"
echo "  Report  : ${WRKDIR}/${REPORT_HTML}"
echo "  Image   : ${ZAP_IMAGE}"
echo ""

# ── Managed mode: stand up a throwaway auto-login webdev ───────────────────
if [[ -n "$MANAGED" ]]; then
    info "Starting throwaway webdev (auto-login as '${ZAP_USER}') on host port ${APP_PORT}..."
    # Idempotent: clear any stale container from a previous run first.
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    # Explicit -p only (NOT --service-ports): publish on our dedicated host port
    # so we never collide with a webdev the user already runs on 5050.
    docker compose -f "$COMPOSE_FILE" run -d \
        --name "$CONTAINER_NAME" \
        -p "${APP_PORT}:5050" \
        -e DISABLE_AUTH=1 \
        -e DEV_AUTO_LOGIN_USER="$ZAP_USER" \
        webdev >/dev/null \
        || die "Failed to start webdev. If the image is missing, build it first:
       docker compose build webdev"

    # Wait for readiness (DB-backed). webdev's dev server is python3 src/webapp/run.py.
    info "Waiting for app readiness at http://localhost:${APP_PORT}/api/v1/health/ready ..."
    deadline=$(( SECONDS + 180 ))
    until curl -sf "http://localhost:${APP_PORT}/api/v1/health/ready" >/dev/null 2>&1; do
        if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
            echo ""; docker logs --tail 40 "$CONTAINER_NAME" 2>&1 || true
            die "webdev container exited before becoming ready."
        fi
        (( SECONDS >= deadline )) && { docker logs --tail 40 "$CONTAINER_NAME" 2>&1 || true; die "Timed out waiting for app readiness."; }
        sleep 3; echo -n "."
    done
    echo ""; ok "App is ready."

    # Assert dev auto-login is actually active. Follow redirects (the app issues
    # a 308 /admin -> /admin/ trailing-slash redirect) and check where we land:
    # an authenticated request stays on the admin page (200); an unauthenticated
    # one gets bounced to /auth/login. Discriminate on the effective URL, not just
    # the status code (the login page is itself a 200).
    probe="$(curl -sL -o /dev/null \
        -w '%{http_code} %{url_effective}' "http://localhost:${APP_PORT}/admin/")"
    code="${probe%% *}"; eff_url="${probe#* }"
    if [[ "$code" != "200" || "$eff_url" == *"/auth/login"* ]]; then
        die "Auto-login check failed: GET /admin/ landed on '${eff_url}' (HTTP ${code}).
       Dev auto-login did not engage — the spider would hit a login wall.
       (Expected only when FLASK_CONFIG=development, which webdev uses by default.)"
    fi
    ok "Dev auto-login confirmed (GET /admin/ -> 200 as '${ZAP_USER}', no login bounce)."
fi

# ── Run the scan ──────────────────────────────────────────────────────────
echo ""
set +e
if [[ "$MODE" == "scoped" ]]; then
    # Scoped, time-boxed ACTIVE scan via ZAP's Automation Framework. We scope by
    # EXCLUSION (everything in-scope except the heavy read-only paths that make
    # --full drag for an hour) and HARD-CAP the active scan at --max-mins. The
    # plan + reports live in the mounted work dir (/zap/wrk == $WRKDIR).
    info "Generating Automation Framework plan (active ≤${MAX_MINS}m, spider ≤${SPIDER_MINS}m)..."
    cat > "$PLAN_FILE" <<YAML
---
env:
  contexts:
    - name: scoped
      urls:
        - "${TARGET}"
      includePaths:
        - "${TARGET}.*"
      excludePaths:
        - "${TARGET}/status.*"
        - "${TARGET}/admin/htmx/.*"
        - "${TARGET}/static/.*"
        - "${TARGET}/auth/logout.*"
  parameters:
    failOnError: false
    failOnWarning: false
    progressToStdout: true
jobs:
  - type: spider
    parameters:
      context: scoped
      url: "${TARGET}"
      maxDuration: ${SPIDER_MINS}
  - type: passiveScan-wait
    parameters:
      maxDuration: 2
  - type: activeScan
    parameters:
      context: scoped
      maxScanDurationInMins: ${MAX_MINS}
  - type: report
    parameters:
      template: traditional-html
      reportDir: /zap/wrk
      reportFile: "${PREFIX}-rescan-${STAMP}"
      reportTitle: "SAM scoped active scan (${STAMP})"
  - type: report
    parameters:
      template: traditional-json
      reportDir: /zap/wrk
      reportFile: "${PREFIX}-rescan-${STAMP}"
YAML
    info "Running scoped active scan (excludes /status, /admin/htmx, /static, /auth/logout)..."
    docker run --rm \
        --add-host=host.docker.internal:host-gateway \
        -v "${WRKDIR}:/zap/wrk/:rw" \
        "$ZAP_IMAGE" \
        zap.sh -cmd -autorun "/zap/wrk/$(basename "$PLAN_FILE")"
    zap_rc=$?
else
    # Passive baseline (zap-baseline.py) or whole-app active (zap-full-scan.py).
    info "Running ${SCANNER_LABEL} (this can take a few minutes${ACTIVE:+ — active scan is slow})..."
    # -c gen.conf : reuse repo ruleset (NOT -g, which would overwrite it)
    # -j          : also run the AJAX spider (HTMX/JS app coverage)
    # -I          : do not return non-zero merely because passive rules WARN
    # --add-host  : make host.docker.internal resolvable (Docker Desktop sets it on
    #               macOS; harmless-but-helpful elsewhere)
    SCAN_BIN=$([[ "$MODE" == "full" ]] && echo zap-full-scan.py || echo zap-baseline.py)
    docker run --rm \
        --add-host=host.docker.internal:host-gateway \
        -v "${WRKDIR}:/zap/wrk/:rw" \
        "$ZAP_IMAGE" \
        "$SCAN_BIN" \
            -t "$TARGET" \
            -c gen.conf \
            -j \
            -I \
            -r "$REPORT_HTML" \
            -J "$REPORT_JSON"
    zap_rc=$?
fi
set -e

echo ""
# Locate the produced report. The Automation Framework may name it slightly
# differently than the wrapper, so fall back to a glob on the stem.
report_html="${WRKDIR}/${REPORT_HTML}"
[[ -f "$report_html" ]] || report_html="$(ls -1t "${WRKDIR}/${PREFIX}-rescan-${STAMP}"*.html 2>/dev/null | head -1 || true)"
if [[ -n "$report_html" && -f "$report_html" ]]; then
    report_json="$(ls -1t "${WRKDIR}/${PREFIX}-rescan-${STAMP}"*.json 2>/dev/null | head -1 || true)"
    ok "Report written:"
    echo "    HTML : ${report_html}"
    [[ -n "$report_json" ]] && echo "    JSON : ${report_json}"
    echo ""
    echo "  Compare against the saved 2026-05 baseline:"
    echo "    open ${report_html} ${WRKDIR}/zap-basic-report.html"
else
    warn "ZAP exited (rc=${zap_rc}) without producing a ${PREFIX}-rescan-${STAMP} report."
fi

# Wrappers: 0 ok / 1 WARN / 2 FAIL / 3 internal error (-I suppresses the WARN
# exit). Automation Framework: 0 ok / non-zero on error (failOnError=false keeps
# findings from failing it). Surface only genuine wrapper internal errors.
[[ "$MODE" != "scoped" && "$zap_rc" -ge 3 ]] && die "ZAP reported an internal error (rc=${zap_rc})."
exit 0
