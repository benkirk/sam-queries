#!/usr/bin/env bash
# ZAP manual-explore scan for apps with 2FA / OIDC auth.
#
# Runs ZAP natively on macOS (not Docker) so it shares the host network,
# including VPN. Launches an isolated Chrome instance pointed at ZAP — no
# system proxy changes, no noise from Slack/Google/etc. ZAP's passive scanner
# is scoped to samuel.k8s.ucar.edu only.
#
# Usage:
#   ./run-zap-manual-explore.sh
#
# Prerequisites:
#   - ZAP installed:  brew install --cask zaproxy
#   - Google Chrome installed
#
# Outputs:
#   zap-manual-report.html  — passive-scan report based on your browsing session

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_HOST="samuel.k8s.ucar.edu"
TARGET_URL="https://${TARGET_HOST}/"
REPORT="zap-manual-report.html"
ZAP_PORT=8090
ZAP_API="http://localhost:${ZAP_PORT}"
CERT_PATH="${SCRIPT_DIR}/zap-root-ca.pem"
CHROME_PROFILE="/tmp/zap-chrome-profile"
ZAP_PID=""

ZAP_SH="/Applications/ZAP.app/Contents/Java/zap.sh"
if [[ ! -x "$ZAP_SH" ]]; then
    echo "ERROR: ZAP not found at ${ZAP_SH}" >&2
    echo "Install with:  brew install --cask zaproxy" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
cleanup() {
    echo ""
    echo "Stopping ZAP..."
    [[ -n "$ZAP_PID" ]] && kill "$ZAP_PID" 2>/dev/null || true
    rm -rf "$CHROME_PROFILE"
    echo "The ZAP CA cert was added to your login keychain. Remove it when done:"
    echo "  Keychain Access → search 'OWASP ZAP Root CA' → delete"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Start ZAP daemon (native, not Docker — needs host network for VPN access)
# ---------------------------------------------------------------------------
echo ""
echo "ZAP manual-explore scan"
echo "  Target : ${TARGET_URL}"
echo "  Report : ${SCRIPT_DIR}/${REPORT}"
echo ""
echo "Starting ZAP..."

"$ZAP_SH" -daemon -port "$ZAP_PORT" \
    -config api.disablekey=true \
    -dir "${SCRIPT_DIR}/.zap-session" \
    > /dev/null 2>&1 &
ZAP_PID=$!

echo -n "Waiting for ZAP to start"
until curl -sf "${ZAP_API}/JSON/core/view/version/" > /dev/null 2>&1; do
    if ! kill -0 "$ZAP_PID" 2>/dev/null; then
        echo ""; echo "ERROR: ZAP process exited unexpectedly." >&2; exit 1
    fi
    sleep 2
    echo -n "."
done
echo " ready."

# ---------------------------------------------------------------------------
# Scope ZAP to the target host — passive scanner ignores everything else
# ---------------------------------------------------------------------------
curl -sf "${ZAP_API}/JSON/context/action/newContext/?contextName=samuel" > /dev/null
curl -sf "${ZAP_API}/JSON/context/action/includeInContext/?contextName=samuel&regex=https://samuel\\.k8s\\.ucar\\.edu.*" > /dev/null
curl -sf "${ZAP_API}/JSON/pscan/action/setScanOnlyInScope/?onlyInScope=true" > /dev/null

# ---------------------------------------------------------------------------
# Install ZAP CA cert into macOS keychain (needed for Chrome to trust ZAP MITM)
# ---------------------------------------------------------------------------
echo ""
echo "Downloading ZAP CA certificate..."
curl -sf "${ZAP_API}/OTHER/core/other/rootcert/" > "$CERT_PATH"

echo "Installing CA cert in macOS login keychain (keychain password prompt expected)..."
security add-trusted-cert -r trustRoot \
    -k ~/Library/Keychains/login.keychain \
    "$CERT_PATH"
echo "CA cert installed."

# ---------------------------------------------------------------------------
# Launch isolated Chrome through ZAP — no system proxy change needed
# ---------------------------------------------------------------------------
echo ""
echo "Launching Chrome through ZAP proxy..."
echo "(Fresh profile — no extensions, no sync, no background traffic)"
echo ""

open -na "Google Chrome" --args \
    --proxy-server="http://127.0.0.1:${ZAP_PORT}" \
    --user-data-dir="$CHROME_PROFILE" \
    --no-first-run \
    --disable-sync \
    --disable-background-networking \
    "${TARGET_URL}"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Chrome has opened at ${TARGET_URL} through ZAP."
echo ""
echo "Log in (including 2FA) and navigate the app:"
echo "  - User search and profile pages"
echo "  - Project listings and detail pages"
echo "  - Allocation / charge views"
echo "  - Any admin pages you have access to"
echo ""
echo "Return here and press Enter when done."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -r -p "Press Enter when done browsing..."

# ---------------------------------------------------------------------------
# Wait for passive scan queue to drain
# ---------------------------------------------------------------------------
echo ""
echo "Waiting for passive scan queue to drain..."
while true; do
    records=$(curl -sf "${ZAP_API}/JSON/pscan/view/recordsToScan/" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['recordsToScan'])" 2>/dev/null \
        || echo "0")
    [[ "$records" == "0" ]] && break
    echo "  ${records} record(s) remaining..."
    sleep 3
done
echo "Passive scan complete."

# ---------------------------------------------------------------------------
# Generate HTML report filtered to the samuel context
# ---------------------------------------------------------------------------
echo "Generating report..."
curl -sf "${ZAP_API}/JSON/reports/action/generate/\
?title=SAM+Queries+Security+Scan\
&template=traditional-html\
&contexts=samuel\
&reportFileName=${REPORT}\
&reportDir=${SCRIPT_DIR}" > /dev/null

FILTERED_REPORT="${REPORT%.html}-filtered.html"
python3 "${SCRIPT_DIR}/filter-zap-report.py" \
    "${SCRIPT_DIR}/${REPORT}" \
    "${SCRIPT_DIR}/${FILTERED_REPORT}"

echo ""
echo "Raw report    : ${SCRIPT_DIR}/${REPORT}"
echo "Filtered report: ${SCRIPT_DIR}/${FILTERED_REPORT}"
echo ""
echo "URLs observed on ${TARGET_HOST}:"
curl -sf "${ZAP_API}/JSON/core/view/urls/" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
urls = [u for u in data.get('urls', []) if '${TARGET_HOST}' in u]
print(f'  {len(urls)} URL(s)')
for u in sorted(urls)[:20]:
    print(f'  {u}')
if len(urls) > 20:
    print(f'  ... and {len(urls) - 20} more')
" 2>/dev/null || true
