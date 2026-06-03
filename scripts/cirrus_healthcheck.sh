#!/bin/bash
# cirrus_healthcheck.sh — opinionated probe for the sam-queries (samuel) release on nwc1.
#
# Inspects the Helm chart in ./helm/ (Deployment 'samuel' webapp +
# Deployment 'samuel-redis' cache, in namespace 'sam-queries' by default).
# Designed to be read top-to-bottom by someone new to Kubernetes: each section
# prints what it's checking, the raw kubectl/helm output, a short explanation,
# and a PASS / WARN / FAIL line.
#
# Read-only. Never modifies cluster state.
#
# Usage:
#   scripts/cirrus_healthcheck.sh [options]
#
# Options:
#   -n, --namespace NS    Namespace the release lives in   (default: sam-queries)
#   -r, --release    REL  Helm release name                (default: samuel)
#       --context    CTX  kubectl context to target       (default: current)
#       --no-color        Disable ANSI color
#   -v, --verbose         Extra detail per section
#   -h, --help            Show this help

set -euo pipefail

NAMESPACE="sam-queries"
RELEASE="samuel"
CONTEXT=""
USE_COLOR=1
VERBOSE=0

# Names baked into helm/templates/*.yaml. If you rename objects in the chart,
# update these in lockstep. Resource limits below are read live from the pod
# spec, not hard-coded.
WEBAPP_NAME="samuel"
WEBAPP_PORT=5050
REDIS_NAME="samuel-redis"
REDIS_PORT=6379
INGRESS_HOST="samuel.k8s.ucar.edu"
TLS_SECRET="incommon-cert-samuel"
# ExternalSecret resource names carry the '-esos' suffix per
# helm/templates/external_secret.yaml; the produced Secret (consumed
# via env secretKeyRef in the Deployment) drops the suffix.
EXTERNAL_SECRETS=(
    "samuel-db-credentials-esos"
    "samuel-sam-db-credentials-esos"
    "samuel-jh-db-credentials-esos"
    "samuel-jh-credentials-esos"
    "samuel-oidc-credentials-esos"
)
HEALTH_PATH="/api/v1/health/ready"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
TUNING_HINTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--namespace) NAMESPACE="$2"; shift 2;;
        -r|--release)   RELEASE="$2";   shift 2;;
        --context)      CONTEXT="$2";   shift 2;;
        --no-color)     USE_COLOR=0;    shift;;
        -v|--verbose)   VERBOSE=1;      shift;;
        -h|--help)      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done

if [[ $USE_COLOR -eq 1 && -t 1 ]]; then
    BLUE=$'\033[0;34m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    RED=$'\033[0;31m';  CYAN=$'\033[0;36m';  BOLD=$'\033[1m'; NC=$'\033[0m'
else
    BLUE=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; BOLD=""; NC=""
fi

KCTL=(kubectl)
[[ -n "$CONTEXT" ]] && KCTL+=(--context "$CONTEXT")
KCTL_NS=("${KCTL[@]}" -n "$NAMESPACE")

# --- helpers ----------------------------------------------------------------

section() {
    echo
    echo -e "${BOLD}${BLUE}═══ $* ═══${NC}"
}

explain() {
    echo -e "  ${CYAN}↳ $*${NC}"
}

pass() { echo -e "  ${GREEN}✔ PASS${NC} — $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn() { echo -e "  ${YELLOW}⚠ WARN${NC} — $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail() { echo -e "  ${RED}✘ FAIL${NC} — $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
info() { echo -e "  ${CYAN}ℹ${NC} $*"; }
hint() { TUNING_HINTS+=("$1"); }

# Run a command and indent its output for readability.
run() {
    "$@" 2>&1 | sed 's/^/    /' || true
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { fail "$1 not found in PATH"; exit 1; }
}

human_bytes() {
    awk -v b="$1" 'BEGIN{
        split("B KB MB GB TB PB",u);
        i=1; while (b>=1024 && i<6){ b/=1024; i++ }
        printf "%.1f%s", b, u[i]
    }'
}

# Strip K8s resource units to a plain number.
# CPU: 16 -> 16 cores, 250m -> 0.25, 100000000n -> 0.1
# Mem: 128Gi -> 128*1024^3, 4096Mi -> 4096*1024^2, 4096M -> 4096*1e6
to_cores() {
    local v="$1"
    case "$v" in
        *n) awk -v x="${v%n}" 'BEGIN{printf "%.3f", x/1e9}';;
        *u) awk -v x="${v%u}" 'BEGIN{printf "%.3f", x/1e6}';;
        *m) awk -v x="${v%m}" 'BEGIN{printf "%.3f", x/1000}';;
        *)  awk -v x="$v"     'BEGIN{printf "%.3f", x+0}';;
    esac
}
to_bytes() {
    local v="$1"
    case "$v" in
        *Ki) awk -v x="${v%Ki}" 'BEGIN{printf "%.0f", x*1024}';;
        *Mi) awk -v x="${v%Mi}" 'BEGIN{printf "%.0f", x*1024*1024}';;
        *Gi) awk -v x="${v%Gi}" 'BEGIN{printf "%.0f", x*1024*1024*1024}';;
        *Ti) awk -v x="${v%Ti}" 'BEGIN{printf "%.0f", x*1024*1024*1024*1024}';;
        *K)  awk -v x="${v%K}"  'BEGIN{printf "%.0f", x*1000}';;
        *M)  awk -v x="${v%M}"  'BEGIN{printf "%.0f", x*1000000}';;
        *G)  awk -v x="${v%G}"  'BEGIN{printf "%.0f", x*1000000000}';;
        *T)  awk -v x="${v%T}"  'BEGIN{printf "%.0f", x*1000000000000}';;
        *)   awk -v x="$v"      'BEGIN{printf "%.0f", x+0}';;
    esac
}

# Convert RFC3339 timestamp to "N days/hours ago" via portable date(1).
# Echoes a single integer in seconds; "" if the date is unparseable.
seconds_since() {
    awk -v d="$1" 'BEGIN{
        cmd="date -u +%s"; cmd | getline now; close(cmd);
        cmd="date -u -d \"" d "\" +%s 2>/dev/null || date -u -j -f %Y-%m-%dT%H:%M:%SZ \"" d "\" +%s 2>/dev/null"
        cmd | getline t; close(cmd);
        if (t=="" || t==0) exit 1;
        printf "%.0f", (now-t)
    }'
}

# ============================================================================
section "0. Preflight"
# ============================================================================

require_cmd kubectl
require_cmd helm
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
explain "kubectl needs a 'context' to know which cluster to talk to. We expect 'nwc1'."

if "${KCTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    pass "namespace '$NAMESPACE' exists"
else
    fail "namespace '$NAMESPACE' not found"
    exit 1
fi

if helm status -n "$NAMESPACE" "$RELEASE" >/dev/null 2>&1; then
    pass "helm release '$RELEASE' present in '$NAMESPACE'"
else
    # Try auto-detect: pick the first release in the namespace.
    DETECTED=$(helm list -n "$NAMESPACE" -o json 2>/dev/null | jq -r '.[0].name // empty')
    if [[ -n "$DETECTED" ]]; then
        warn "helm release '$RELEASE' not found; auto-detected '$DETECTED' in '$NAMESPACE'"
        explain "Re-run with --release $DETECTED to silence this warning."
        RELEASE="$DETECTED"
    else
        warn "no helm release found in '$NAMESPACE' — chart may be applied as raw manifests or via ArgoCD"
    fi
fi

# ============================================================================
section "1. Helm release summary"
# ============================================================================

if helm status -n "$NAMESPACE" "$RELEASE" >/dev/null 2>&1; then
    run helm status -n "$NAMESPACE" "$RELEASE"
    explain "Lists the chart version, release status, and revision count. STATUS should be 'deployed'."

    STATUS=$(helm status -n "$NAMESPACE" "$RELEASE" -o json 2>/dev/null | jq -r '.info.status // "unknown"')
    if [[ "$STATUS" == "deployed" ]]; then
        pass "helm release status='deployed'"
    else
        fail "helm release status='$STATUS' (expected 'deployed')"
    fi

    if [[ $VERBOSE -eq 1 ]]; then
        echo
        echo "  Resources (helm get manifest | summarize):"
        run helm status -n "$NAMESPACE" "$RELEASE" --show-resources
        echo
        echo "  Non-default values (helm get values):"
        run helm get values -n "$NAMESPACE" "$RELEASE"
    fi
else
    info "no helm release named '$RELEASE' — skipping helm checks"
fi

# ============================================================================
section "2. Webapp Deployment & pods"
# ============================================================================

if ! "${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" >/dev/null 2>&1; then
    fail "Deployment '$WEBAPP_NAME' not found"
else
    run "${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" -o wide
    explain "READY x/y should be x==y; AVAILABLE should match DESIRED. UP-TO-DATE < replicas means a rollout is in progress."

    DEPLOY_JSON=$("${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" -o json)
    DESIRED=$(echo "$DEPLOY_JSON" | jq -r '.spec.replicas // 0')
    READY=$(echo "$DEPLOY_JSON"   | jq -r '.status.readyReplicas // 0')
    UPDATED=$(echo "$DEPLOY_JSON" | jq -r '.status.updatedReplicas // 0')
    AVAIL=$(echo "$DEPLOY_JSON"   | jq -r '.status.availableReplicas // 0')

    # Diagnose not-Ready pods up front so the verdict can tell a genuinely
    # STUCK rollout (a pod wedged in ImagePullBackOff/CrashLoop for many
    # minutes) apart from one that is healthily mid-roll. We print the *why*
    # (waiting reason + message + node), not just the ready count.
    STUCK_AFTER_MIN=10
    PODS_JSON=$("${KCTL_NS[@]}" get pods -l "app=$WEBAPP_NAME" -o json 2>/dev/null)
    notready_diag=""     # human-readable lines, one block per not-Ready pod
    stuck_pods=0         # not-Ready pods in a back-off/error state past threshold
    # Fields are joined with US (0x1f), NOT tab: tab is an IFS-whitespace char,
    # so empty optional fields (message, terminated.*) would collapse and shift
    # every column. US is non-whitespace, so empty fields are preserved. The
    # message is also stripped of CR/LF/TAB so it can't break line/field parsing.
    while IFS=$'\x1f' read -r pname pready pphase wreason wmsg treason texit pnode pstart; do
        [[ -z "$pname" ]] && continue
        [[ "$pready" == "true" ]] && continue
        detail="${wreason:-phase=$pphase}"
        [[ -n "$treason" ]] && detail="$detail (last exit: ${treason}/${texit})"
        sec=$(seconds_since "$pstart" 2>/dev/null || echo "")
        if [[ -n "$sec" ]]; then
            age_min=$(awk -v s="$sec" 'BEGIN{printf "%.0f", s/60}')
            age_str="${age_min}m"
        else
            age_min=0; age_str="?"
        fi
        notready_diag+="pod $pname: not Ready — $detail on ${pnode:-<unscheduled>} (age ${age_str})"$'\n'
        [[ -n "$wmsg" ]] && notready_diag+="    ↳ ${wmsg}"$'\n'
        # A back-off/error waiting reason that has persisted is stuck, not in-flight.
        case "$wreason" in
            ImagePullBackOff|ErrImagePull|CrashLoopBackOff|CreateContainerConfigError|InvalidImageName|ErrImageNeverPull)
                [[ "${age_min:-0}" -ge "$STUCK_AFTER_MIN" ]] && stuck_pods=$((stuck_pods+1))
                ;;
        esac
    done < <(echo "$PODS_JSON" | jq -r '.items[] | [
        .metadata.name,
        ((.status.containerStatuses[0].ready // false) | tostring),
        (.status.phase // ""),
        (.status.containerStatuses[0].state.waiting.reason // ""),
        (.status.containerStatuses[0].state.waiting.message // "" | gsub("[\\n\\r\\t]"; " ")),
        (.status.containerStatuses[0].lastState.terminated.reason // ""),
        ((.status.containerStatuses[0].lastState.terminated.exitCode // "") | tostring),
        (.spec.nodeName // ""),
        (.metadata.creationTimestamp // "")
    ] | join("")' 2>/dev/null)

    if [[ "$READY" == "$DESIRED" && "$AVAIL" == "$DESIRED" && "$UPDATED" == "$DESIRED" ]]; then
        pass "Deployment $WEBAPP_NAME: $READY/$DESIRED ready, all updated and available"
    elif [[ "$stuck_pods" -gt 0 ]]; then
        fail "Deployment $WEBAPP_NAME: $READY/$DESIRED ready — $stuck_pods pod(s) STUCK >${STUCK_AFTER_MIN}m (see diagnosis below), not a healthy rollout"
    elif [[ "$UPDATED" != "$DESIRED" ]]; then
        warn "Deployment $WEBAPP_NAME: rollout in progress ($UPDATED/$DESIRED pods updated)"
    else
        fail "Deployment $WEBAPP_NAME: $READY/$DESIRED ready, $AVAIL/$DESIRED available"
    fi

    # Pod table
    echo
    run "${KCTL_NS[@]}" get pods -l "app=$WEBAPP_NAME" \
        -o 'custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp,NODE:.spec.nodeName'

    # Per-pod "why not Ready" — the detail that previously required `kubectl describe`
    if [[ -n "$notready_diag" ]]; then
        echo
        echo "  Not-Ready pod diagnosis:"
        printf '%s' "$notready_diag" | sed 's/^/    /'
    fi

    # Recent-restart scan
    restarts=$("${KCTL_NS[@]}" get pods -l "app=$WEBAPP_NAME" \
                -o jsonpath='{range .items[*]}{.metadata.name}={.status.containerStatuses[0].restartCount};{.status.containerStatuses[0].lastState.terminated.finishedAt}{"\n"}{end}' \
                2>/dev/null)
    bad_restart=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        pod="${line%%=*}"
        rest="${line#*=}"; n="${rest%%;*}"; when="${rest#*;}"
        [[ "${n:-0}" -eq 0 ]] && continue
        if [[ -n "$when" ]]; then
            sec=$(seconds_since "$when" || echo "")
            if [[ -n "$sec" ]]; then
                age_days=$(awk -v s="$sec" 'BEGIN{printf "%.0f", s/86400}')
                if [[ "${age_days:-0}" -lt 7 ]]; then
                    warn "pod $pod restarted $n time(s); last restart ${age_days}d ago — check logs"
                    bad_restart=1
                else
                    info "pod $pod has $n restart(s); most recent ${age_days}d ago (old, likely benign)"
                fi
            else
                warn "pod $pod has $n restart(s) — could not parse timestamp '$when'"
                bad_restart=1
            fi
        else
            warn "pod $pod has $n restart(s) — could not determine when"
            bad_restart=1
        fi
    done <<< "$restarts"
    [[ $bad_restart -eq 0 ]] && pass "no recent (<7d) restarts on webapp pods"

    # Image SHA consistency across replicas
    IMAGES=$("${KCTL_NS[@]}" get pods -l "app=$WEBAPP_NAME" \
              -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}' | sort -u)
    n_distinct=$(echo "$IMAGES" | grep -c .)
    if [[ "$n_distinct" -le 1 ]]; then
        info "image: $(echo "$IMAGES" | head -1)"
        pass "all webapp pods on the same image"
    else
        warn "webapp pods running mixed images (rollout in progress?):"
        echo "$IMAGES" | sed 's/^/      /'
    fi
fi

# ============================================================================
section "3. Rollout safety (probes, strategy, PDB, spread)"
# ============================================================================

explain "These guard the *next* rollout, not the current moment: a readiness
  probe gates traffic + rollout promotion, a PDB survives node drains, topology
  spread avoids single-node concentration. Gaps here are advisory (WARN)."

if ! "${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" >/dev/null 2>&1; then
    info "Deployment '$WEBAPP_NAME' not found — skipping rollout-safety checks"
else
    SAFE_JSON=$("${KCTL_NS[@]}" get deploy "$WEBAPP_NAME" -o json)
    REPLICAS=$(echo "$SAFE_JSON" | jq -r '.spec.replicas // 1')
    CTR=$(echo "$SAFE_JSON" | jq -c --arg n "$WEBAPP_NAME" \
            '.spec.template.spec.containers[] | select(.name==$n)')

    # --- Probes: readiness/liveness gate rollouts; startup gives slow boots air ---
    rprobe=0; lprobe=0
    for probe in readinessProbe livenessProbe startupProbe; do
        if echo "$CTR" | jq -e --arg p "$probe" '.[$p] != null' >/dev/null 2>&1; then
            ppath=$(echo "$CTR" | jq -r --arg p "$probe" \
                      '.[$p].httpGet.path // .[$p].tcpSocket.port // "set"')
            info "$probe → ${ppath}"
            [[ "$probe" == "readinessProbe" ]] && rprobe=1
            [[ "$probe" == "livenessProbe"  ]] && lprobe=1
        else
            case "$probe" in
                readinessProbe) warn "no readinessProbe — rollouts can't gate on real health; a broken build promotes anyway" ;;
                livenessProbe)  warn "no livenessProbe — a wedged process won't be auto-restarted" ;;
                startupProbe)   info "no startupProbe (optional; a slow boot can trip liveness)" ;;
            esac
        fi
    done
    [[ "$rprobe" -eq 1 && "$lprobe" -eq 1 ]] && pass "webapp has readiness + liveness probes"

    # --- Rollout strategy: can it drop below 1 Ready? (maxUnavailable rounds DOWN) ---
    echo
    STYPE=$(echo "$SAFE_JSON" | jq -r '.spec.strategy.type // "RollingUpdate"')
    MU=$(echo "$SAFE_JSON"    | jq -r '.spec.strategy.rollingUpdate.maxUnavailable // "25%"')
    MS=$(echo "$SAFE_JSON"    | jq -r '.spec.strategy.rollingUpdate.maxSurge // "25%"')
    echo "  strategy: $STYPE (maxUnavailable=$MU, maxSurge=$MS, replicas=$REPLICAS)"
    case "$MU" in
        *%) mu_n=$(awk -v p="${MU%\%}" -v t="$REPLICAS" 'BEGIN{printf "%d", int(p*t/100)}') ;;
        *)  mu_n="$MU" ;;
    esac
    min_avail=$(( REPLICAS - mu_n ))
    if [[ "$min_avail" -lt 1 ]]; then
        warn "strategy allows 0 Ready mid-rollout (maxUnavailable=$MU → $mu_n of $REPLICAS)"
    else
        pass "strategy keeps ≥${min_avail} Ready mid-rollout"
    fi

    # --- PodDisruptionBudget: survives node drains? ---
    echo
    PDB_JSON=$("${KCTL_NS[@]}" get pdb -o json 2>/dev/null || echo '{"items":[]}')
    pdb_name=$(echo "$PDB_JSON" | jq -r --arg n "$WEBAPP_NAME" \
                 '.items[] | select(.spec.selector.matchLabels.app==$n) | .metadata.name' | head -1)
    if [[ -z "$pdb_name" ]]; then
        warn "no PodDisruptionBudget selects app=$WEBAPP_NAME — a node drain can evict all replicas at once"
    else
        pdb_min=$(echo "$PDB_JSON" | jq -r --arg p "$pdb_name" \
                    '.items[] | select(.metadata.name==$p) | (.spec.minAvailable // .spec.maxUnavailable // "—")')
        pdb_allowed=$(echo "$PDB_JSON" | jq -r --arg p "$pdb_name" \
                    '.items[] | select(.metadata.name==$p) | .status.disruptionsAllowed // 0')
        echo "  PDB $pdb_name: minAvailable=$pdb_min, allowedDisruptions=$pdb_allowed"
        if [[ "$REPLICAS" -gt 1 && "${pdb_allowed:-0}" -eq 0 ]]; then
            warn "PDB allows 0 disruptions — at the floor (or below minAvailable); a drain will block"
        else
            pass "PDB present ($pdb_name), allowedDisruptions=$pdb_allowed"
        fi
    fi

    # --- Spread: config (topologySpread/anti-affinity) + runtime (distinct nodes) ---
    echo
    has_tsc=$(echo "$SAFE_JSON" | jq -e '(.spec.template.spec.topologySpreadConstraints // []) | length > 0' >/dev/null 2>&1 && echo 1 || echo 0)
    has_paa=$(echo "$SAFE_JSON" | jq -e '.spec.template.spec.affinity.podAntiAffinity != null' >/dev/null 2>&1 && echo 1 || echo 0)
    if [[ "$has_tsc" -eq 1 || "$has_paa" -eq 1 ]]; then
        info "spread configured (topologySpreadConstraints=$has_tsc, podAntiAffinity=$has_paa)"
    elif [[ "$REPLICAS" -gt 1 ]]; then
        warn "no topologySpreadConstraints/podAntiAffinity — replicas may co-locate on one node"
    fi
    nodes_ready=$(echo "$PODS_JSON" | jq -r \
        '[.items[] | select(.status.containerStatuses[0].ready==true) | .spec.nodeName] | unique | length' 2>/dev/null)
    nodes_ready=${nodes_ready:-0}
    echo "  Ready webapp pods span ${nodes_ready} distinct node(s)"
    if [[ "$REPLICAS" -gt 1 && "$nodes_ready" -le 1 ]]; then
        warn "all Ready webapp pods are on a single node — one node failure takes the app down"
    elif [[ "$nodes_ready" -ge 2 ]]; then
        pass "Ready pods spread across ${nodes_ready} nodes"
    fi

    # --- imagePullSecrets: anonymous ghcr pulls are rate-limit-prone (today's failure class) ---
    has_ips=$(echo "$SAFE_JSON" | jq -r '(.spec.template.spec.imagePullSecrets // []) | length')
    img=$(echo "$SAFE_JSON" | jq -r '.spec.template.spec.containers[0].image // ""')
    if [[ "${has_ips:-0}" -eq 0 && "$img" == ghcr.io/* ]]; then
        hint "webapp pulls $img anonymously (no imagePullSecrets) — anonymous ghcr.io pulls are rate-limited per node IP and can wedge a pod in ImagePullBackOff; consider an authenticated pull secret"
    fi
fi

# ============================================================================
section "4. Redis cache Deployment & pod"
# ============================================================================

if ! "${KCTL_NS[@]}" get deploy "$REDIS_NAME" >/dev/null 2>&1; then
    warn "Deployment '$REDIS_NAME' not found — cache may be disabled (helm cache.enabled=false)"
else
    run "${KCTL_NS[@]}" get deploy "$REDIS_NAME" -o wide
    DEPLOY_JSON=$("${KCTL_NS[@]}" get deploy "$REDIS_NAME" -o json)
    DESIRED=$(echo "$DEPLOY_JSON" | jq -r '.spec.replicas // 0')
    READY=$(echo "$DEPLOY_JSON"   | jq -r '.status.readyReplicas // 0')
    if [[ "$READY" == "$DESIRED" && "$DESIRED" -ge 1 ]]; then
        pass "Redis $REDIS_NAME: $READY/$DESIRED ready"
    else
        fail "Redis $REDIS_NAME: $READY/$DESIRED ready"
    fi

    REDIS_POD=$("${KCTL_NS[@]}" get pod -l "app=$REDIS_NAME" \
                -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [[ -z "$REDIS_POD" ]]; then
        warn "no Redis pod found via selector app=$REDIS_NAME"
    else
        echo
        echo "  redis-cli ping (via kubectl exec):"
        if PONG=$("${KCTL_NS[@]}" exec "$REDIS_POD" -- redis-cli -p "$REDIS_PORT" ping 2>&1); then
            echo "    $PONG"
            if [[ "$PONG" == "PONG" ]]; then
                pass "Redis responds to PING"
            else
                fail "Redis returned '$PONG' instead of PONG"
            fi
        else
            fail "redis-cli ping failed: $PONG"
        fi

        if [[ $VERBOSE -eq 1 ]]; then
            echo
            echo "  redis-cli INFO memory:"
            run "${KCTL_NS[@]}" exec "$REDIS_POD" -- redis-cli -p "$REDIS_PORT" info memory
        fi

        # Memory usage vs maxmemory
        MAX=$("${KCTL_NS[@]}" exec "$REDIS_POD" -- redis-cli -p "$REDIS_PORT" config get maxmemory 2>/dev/null | tail -1 | tr -d '\r')
        USED=$("${KCTL_NS[@]}" exec "$REDIS_POD" -- redis-cli -p "$REDIS_PORT" info memory 2>/dev/null \
                | awk -F: '/^used_memory:/{gsub(/\r/,""); print $2; exit}')
        if [[ -n "$MAX" && -n "$USED" && "$MAX" -gt 0 ]]; then
            pct=$(awk -v u="$USED" -v m="$MAX" 'BEGIN{printf "%.1f", 100*u/m}')
            echo "  redis memory: $(human_bytes "$USED") used / $(human_bytes "$MAX") max (${pct}%)"
            if   awk -v p="$pct" 'BEGIN{exit !(p>85)}'; then warn "redis memory at ${pct}% of maxmemory — eviction pressure"
            else                                              pass "redis memory ${pct}% of maxmemory"
            fi
        fi
    fi
fi

# ============================================================================
section "5. Resource usage vs limits"
# ============================================================================

if ! "${KCTL_NS[@]}" top pods --no-headers >/dev/null 2>&1; then
    warn "kubectl top unavailable (metrics-server not installed?) — skipping live usage"
else
    echo "  live usage snapshot (one-shot — re-run for trend):"
    run "${KCTL_NS[@]}" top pods --containers

    # Build per-container limits map from current pods, then compare to live usage.
    LIMITS_TSV=$("${KCTL_NS[@]}" get pods \
        -o jsonpath='{range .items[*]}{range .spec.containers[*]}{..metadata.name}{"\t"}{.name}{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}' 2>/dev/null \
        | awk -F'\t' 'NF==4 && $3!="" {print}')

    # webapp container memory peak across replicas (for overprovisioning hint)
    webapp_mem_peak=0
    webapp_mem_limit=0

    while read -r pod ctr cpu mem; do
        [[ -z "$pod" ]] && continue
        # Look up limits for this pod/container
        lim=$(echo "$LIMITS_TSV" | awk -F'\t' -v p="$pod" -v c="$ctr" '$1==p && $2==c {print $3" "$4; exit}')
        [[ -z "$lim" ]] && continue
        read -r lim_cpu_raw lim_mem_raw <<< "$lim"
        lim_cpu=$(to_cores "$lim_cpu_raw")
        lim_mem=$(to_bytes "$lim_mem_raw")
        u_cpu=$(to_cores "$cpu")
        u_mem=$(to_bytes "$mem")
        if awk -v a="$lim_cpu" 'BEGIN{exit !(a>0)}' && awk -v a="$lim_mem" 'BEGIN{exit !(a>0)}'; then
            cpu_pct=$(awk -v u="$u_cpu" -v l="$lim_cpu" 'BEGIN{printf "%.1f", 100*u/l}')
            mem_pct=$(awk -v u="$u_mem" -v l="$lim_mem" 'BEGIN{printf "%.1f", 100*u/l}')
            echo "  $pod/$ctr: cpu=${u_cpu}c (${cpu_pct}% of $lim_cpu) | mem=$(human_bytes "$u_mem") (${mem_pct}% of $(human_bytes "$lim_mem"))"

            band_cpu=$(awk -v p="$cpu_pct" 'BEGIN{
                if (p<20) print "low"; else if (p<70) print "ok"; else if (p<85) print "warm"; else print "hot"
            }')
            band_mem=$(awk -v p="$mem_pct" 'BEGIN{
                if (p<20) print "low"; else if (p<70) print "ok"; else if (p<85) print "warm"; else print "hot"
            }')
            [[ "$band_cpu" == "hot" ]] && warn "$pod/$ctr CPU at ${cpu_pct}% of limit — close to ceiling"
            [[ "$band_mem" == "hot" ]] && warn "$pod/$ctr memory at ${mem_pct}% of limit — close to ceiling"

            # Track webapp memory peak for overprovisioning analysis
            if [[ "$ctr" == "$WEBAPP_NAME" ]]; then
                webapp_mem_limit=$lim_mem
                if awk -v a="$u_mem" -v b="$webapp_mem_peak" 'BEGIN{exit !(a>b)}'; then
                    webapp_mem_peak=$u_mem
                fi
            fi
        fi
    done < <("${KCTL_NS[@]}" top pods --containers --no-headers 2>/dev/null)

    # Overprovisioning hint: webapp peak < 25% of mem limit
    if [[ "$webapp_mem_limit" -gt 0 ]]; then
        peak_pct=$(awk -v u="$webapp_mem_peak" -v l="$webapp_mem_limit" 'BEGIN{printf "%.1f", 100*u/l}')
        if awk -v p="$peak_pct" 'BEGIN{exit !(p<25)}'; then
            hint "webapp peak memory $(human_bytes "$webapp_mem_peak") is ${peak_pct}% of $(human_bytes "$webapp_mem_limit") limit — consider lowering webapp.container.limits.memory in helm/values.yaml"
        fi
    fi
    pass "live usage sampled"
fi

# ============================================================================
section "6. Service, Ingress, TLS"
# ============================================================================

run "${KCTL_NS[@]}" get svc "$WEBAPP_NAME" "$REDIS_NAME" 2>/dev/null
explain "Webapp service exposes port $WEBAPP_PORT to the cluster. Redis service exposes port $REDIS_PORT."

# Endpoints vs Ready pods sanity check
EP_COUNT=$("${KCTL_NS[@]}" get endpoints "$WEBAPP_NAME" \
            -o jsonpath='{range .subsets[*].addresses[*]}1{"\n"}{end}' 2>/dev/null | grep -c . || echo 0)
READY_PODS=$("${KCTL_NS[@]}" get pods -l "app=$WEBAPP_NAME" \
              -o jsonpath='{range .items[?(@.status.containerStatuses[0].ready==true)]}1{"\n"}{end}' 2>/dev/null | grep -c . || echo 0)
echo "  webapp endpoints: $EP_COUNT  |  ready pods: $READY_PODS"
if [[ "$EP_COUNT" == "$READY_PODS" && "$EP_COUNT" -gt 0 ]]; then
    pass "Service endpoints match ready pod count"
elif [[ "$EP_COUNT" -eq 0 ]]; then
    fail "Service has 0 endpoints — clients will get connection refused"
else
    warn "Service has $EP_COUNT endpoint(s) but $READY_PODS pod(s) Ready — stale Endpoints?"
fi

echo
if "${KCTL_NS[@]}" get ingress "$WEBAPP_NAME" >/dev/null 2>&1; then
    run "${KCTL_NS[@]}" get ingress "$WEBAPP_NAME" -o wide
    HOST=$("${KCTL_NS[@]}" get ingress "$WEBAPP_NAME" -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
    if [[ "$HOST" == "$INGRESS_HOST" ]]; then
        pass "Ingress host = $INGRESS_HOST"
    else
        warn "Ingress host '$HOST' != expected '$INGRESS_HOST'"
    fi
else
    fail "Ingress '$WEBAPP_NAME' not found"
fi

echo
# TLS cert (cert-manager Certificate resource if present, otherwise raw Secret)
if "${KCTL_NS[@]}" get certificate "$TLS_SECRET" >/dev/null 2>&1; then
    NOTAFTER=$("${KCTL_NS[@]}" get certificate "$TLS_SECRET" -o jsonpath='{.status.notAfter}' 2>/dev/null)
    echo "  $TLS_SECRET notAfter: $NOTAFTER"
    if [[ -n "$NOTAFTER" ]]; then
        sec=$(seconds_since "$NOTAFTER" || echo "")
        if [[ -n "$sec" ]]; then
            days_left=$(awk -v s="$sec" 'BEGIN{printf "%.0f", -s/86400}')
            if   [[ ${days_left:-0} -lt 7  ]]; then fail "TLS cert expires in ${days_left}d"
            elif [[ ${days_left:-0} -lt 30 ]]; then warn "TLS cert expires in ${days_left}d"
            else                                    pass "TLS cert valid for ${days_left}d"
            fi
        fi
    fi
elif "${KCTL_NS[@]}" get secret "$TLS_SECRET" >/dev/null 2>&1; then
    info "Secret '$TLS_SECRET' present (not a cert-manager Certificate; cannot read expiry without openssl)"
else
    warn "TLS resource '$TLS_SECRET' not found (neither Certificate nor Secret)"
fi

# ============================================================================
section "7. ExternalSecrets (OpenBao sync)"
# ============================================================================

if ! "${KCTL_NS[@]}" get externalsecret >/dev/null 2>&1; then
    warn "ExternalSecret CRD not present or not readable — skipping"
else
    run "${KCTL_NS[@]}" get externalsecret -o wide
    explain "ExternalSecrets sync DB / OIDC / JupyterHub credentials from OpenBao. STATUS should be 'SecretSynced' and Ready=True."

    any_missing=0
    any_not_ready=0
    for es in "${EXTERNAL_SECRETS[@]}"; do
        ready=$("${KCTL_NS[@]}" get externalsecret "$es" \
                  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
        if [[ -z "$ready" ]]; then
            if ! "${KCTL_NS[@]}" get externalsecret "$es" >/dev/null 2>&1; then
                fail "ExternalSecret '$es' not found"
                any_missing=1
            else
                warn "ExternalSecret '$es' has no Ready condition yet"
                any_not_ready=1
            fi
            continue
        fi
        if [[ "$ready" == "True" ]]; then
            if [[ $VERBOSE -eq 1 ]]; then
                rt=$("${KCTL_NS[@]}" get externalsecret "$es" -o jsonpath='{.status.refreshTime}' 2>/dev/null)
                info "$es Ready=True (last refresh: ${rt:-unknown})"
            fi
        else
            fail "ExternalSecret '$es' Ready=$ready"
            any_not_ready=1
        fi
    done
    if [[ $any_missing -eq 0 && $any_not_ready -eq 0 ]]; then
        pass "all ${#EXTERNAL_SECRETS[@]} expected ExternalSecrets Ready=True"
    fi
fi

# ============================================================================
section "8. In-pod HTTP health probe"
# ============================================================================

WEBAPP_POD=$("${KCTL_NS[@]}" get pod -l "app=$WEBAPP_NAME" \
              -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' 2>/dev/null \
              | awk '{print $1}')
if [[ -z "$WEBAPP_POD" ]]; then
    WEBAPP_POD=$("${KCTL_NS[@]}" get pod -l "app=$WEBAPP_NAME" \
                  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
fi

if [[ -z "$WEBAPP_POD" ]]; then
    warn "no webapp pod found — skipping HTTP health probe"
else
    info "probing http://localhost:${WEBAPP_PORT}${HEALTH_PATH} inside ${WEBAPP_POD}"
    # Use python (always present in the container) — matches compose.yaml healthcheck.
    PROBE_OUT=$("${KCTL_NS[@]}" exec "$WEBAPP_POD" -- python -c '
import urllib.request, urllib.error, json, sys
try:
    r = urllib.request.urlopen("http://localhost:'"$WEBAPP_PORT$HEALTH_PATH"'", timeout=10)
    body = r.read().decode("utf-8", errors="replace")
    print("HTTP", r.status)
    print(body)
    sys.exit(0)
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    try:
        print(e.read().decode("utf-8", errors="replace"))
    except Exception:
        pass
    sys.exit(1)
except Exception as e:
    print("ERROR", type(e).__name__, str(e))
    sys.exit(2)
' 2>&1) || true

    echo "$PROBE_OUT" | sed 's/^/    /'

    http_code=$(echo "$PROBE_OUT" | awk '/^HTTP/{print $2; exit}')
    body=$(echo "$PROBE_OUT" | awk 'BEGIN{p=0} /^HTTP/{p=1; next} p{print}')

    if [[ "$http_code" == "200" ]]; then
        status=$(echo "$body" | jq -r '.status // "?"' 2>/dev/null || echo "?")
        if [[ "$status" == "healthy" ]]; then
            pass "health endpoint returned 200 status='healthy'"
        else
            warn "health endpoint returned 200 but status='$status'"
        fi
        # Per-DB latency surfacing
        for db in sam system_status; do
            lat=$(echo "$body" | jq -r --arg d "$db" '.checks[$d].latency_ms // empty' 2>/dev/null)
            dbst=$(echo "$body" | jq -r --arg d "$db" '.checks[$d].status // empty' 2>/dev/null)
            [[ -z "$lat" ]] && continue
            if [[ "$dbst" != "healthy" ]]; then
                fail "$db DB check status='$dbst' (latency ${lat}ms)"
            elif awk -v l="$lat" 'BEGIN{exit !(l>500)}'; then
                warn "$db DB latency ${lat}ms (>500ms threshold)"
            else
                info "$db DB latency ${lat}ms"
            fi
        done
    elif [[ "$http_code" == "503" ]]; then
        fail "health endpoint returned 503 — one or more DB checks failed"
        for db in sam system_status; do
            dbst=$(echo "$body" | jq -r --arg d "$db" '.checks[$d].status // empty' 2>/dev/null)
            [[ -n "$dbst" && "$dbst" != "healthy" ]] && info "  $db: $dbst"
        done
    else
        fail "health endpoint unreachable or non-200/503 response"
    fi
fi

# ============================================================================
section "9. Recent logs"
# ============================================================================

echo "  webapp (last 500 lines, scanning for ERROR/CRITICAL/Exception/Traceback):"
WEBAPP_LOGS=$("${KCTL_NS[@]}" logs -l "app=$WEBAPP_NAME" --tail=500 --prefix --all-containers=true 2>/dev/null \
              | grep -E -i 'ERROR|CRITICAL|Exception|Traceback' || true)
if [[ -z "$WEBAPP_LOGS" ]]; then
    pass "no error-level lines in last 500 webapp log lines"
else
    n_hits=$(echo "$WEBAPP_LOGS" | grep -c .)
    if [[ $VERBOSE -eq 1 ]]; then
        echo "$WEBAPP_LOGS" | tail -20 | sed 's/^/    /'
    else
        echo "$WEBAPP_LOGS" | tail -5 | sed 's/^/    /'
    fi
    if [[ "$n_hits" -gt 10 ]]; then
        fail "$n_hits error-level line(s) in last 500 webapp log lines"
    else
        warn "$n_hits error-level line(s) in last 500 webapp log lines"
    fi
fi

echo
echo "  redis (last 200 lines, scanning for WARNING/MISCONF/LOADING):"
REDIS_LOGS=$("${KCTL_NS[@]}" logs -l "app=$REDIS_NAME" --tail=200 2>/dev/null \
              | grep -E -i 'WARNING|MISCONF|LOADING|Failed' || true)
if [[ -z "$REDIS_LOGS" ]]; then
    pass "no notable lines in last 200 redis log lines"
else
    n_hits=$(echo "$REDIS_LOGS" | grep -c .)
    echo "$REDIS_LOGS" | tail -5 | sed 's/^/    /'
    warn "$n_hits notable line(s) in last 200 redis log lines"
fi

# ============================================================================
section "10. Recent namespace events"
# ============================================================================

EV=$("${KCTL_NS[@]}" get events --sort-by=.lastTimestamp 2>/dev/null | tail -20 || echo "")
if [[ -z "$EV" ]]; then
    info "no events"
else
    echo "$EV" | sed 's/^/    /'
    warn_lines=$(echo "$EV" | awk '$2=="Warning"' | wc -l | tr -d ' ')
    if [[ "$warn_lines" -gt 0 ]]; then
        warn "$warn_lines Warning event(s) in last 20"
    else
        pass "no Warning events in last 20"
    fi
fi
explain "Events are short-lived (~1h). 'Warning' rows are worth scanning."

# ============================================================================
section "Summary"
# ============================================================================

echo "  Results: ${GREEN}${PASS_COUNT} PASS${NC}  ${YELLOW}${WARN_COUNT} WARN${NC}  ${RED}${FAIL_COUNT} FAIL${NC}"
echo
if [[ ${#TUNING_HINTS[@]} -gt 0 ]]; then
    echo "  ${BOLD}Tuning hints${NC} (review helm/values.yaml on next chart bump):"
    for h in "${TUNING_HINTS[@]}"; do
        echo "    • $h"
    done
else
    echo "  No tuning hints from this run."
fi
echo
echo "  Re-run later (cpu/mem are one-shot samples). For continuous logs:"
echo "    kubectl logs -n $NAMESPACE -l app=$WEBAPP_NAME -f --all-containers=true"

if   [[ $FAIL_COUNT -gt 0 ]]; then exit 2
elif [[ $WARN_COUNT -gt 0 ]]; then exit 1
else                               exit 0
fi
