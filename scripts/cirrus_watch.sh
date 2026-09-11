#!/bin/bash
# cirrus_watch.sh — recurring READ-ONLY watch tick for the sam-queries (samuel)
# release on nwc1. Reports only what CHANGED since the previous run, diffing
# against a small state file, so it can be run every ~30 min from a scheduler.
#
# What it looks at, in one pass:
#   - XRAS xras_action_log — new rows since the last tick (classify per the
#     watch-prod skill), read from the prod DB
#   - web traffic — gunicorn 2xx/3xx/4xx/5xx, latency percentiles, slow (>5s)
#     requests, probe-path hits, from the webapp pod logs
#   - pods — image sha / restart counts / phase; flags a deploy since last tick
#   - redis — memory %, hit rate, and the load-bearing evicted-keys delta
#   - samuel-tasks CronJob — suspended? heartbeat stale? failed Jobs? (report
#     only — this script NEVER touches the remote CronJob)
#
# Read-only. Never modifies cluster or DB state. Exit: 0 quiet / 1 warn / 2 fail.
#
# Usage:
#   scripts/cirrus_watch.sh [options]
#
# Options:
#   -n, --namespace NS   Namespace the release lives in    (default: sam-queries)
#   -r, --release   REL  Helm release name                 (default: samuel)
#       --context   CTX  kubectl context to target         (default: current)
#       --window    DUR  Web-log lookback window           (default: 35m)
#       --db-host   HOST Prod DB host for the XRAS read     (default: sam-sql.ucar.edu)
#       --state     FILE State file path                    (default: XDG state dir)
#       --reset-baseline Forget prior state; seed a fresh baseline this run
#       --no-color       Disable ANSI color
#   -v, --verbose        Extra detail
#   -h, --help           Show this help
#
# Credentials: the XRAS read uses mysql, which picks up ~/.my.cnf automatically;
# otherwise set SAM_DB_USERNAME / SAM_DB_PASSWORD in the environment. The prod DB
# is a read-only replica. kubectl must target the nwc1 context (pass --context
# nwc1 if it is not your current context).

set -euo pipefail

_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/cirrus_common.sh
source "${_LIBDIR}/cirrus_common.sh"

# --- defaults (overridable via flags/env) -----------------------------------
WINDOW="${WATCH_WINDOW:-35m}"
DBHOST="${WATCH_DB_HOST:-sam-sql.ucar.edu}"
DBPORT=3306
STATE="${WATCH_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/sam-watch/state}"
RESET_BASELINE=0

while [[ $# -gt 0 ]]; do
    if handle_common_arg "$@"; then shift "$_CONSUMED"; continue; fi
    case "$1" in
        --window)         WINDOW="$2"; shift 2;;
        --db-host)        DBHOST="$2"; shift 2;;
        --state)          STATE="$2"; shift 2;;
        --reset-baseline) RESET_BASELINE=1; shift;;
        *) echo "Unknown option: $1" >&2; exit 2;;
    esac
done

setup_colors
build_kctl

# --- load prior state -------------------------------------------------------
# Each field is carried forward independently so a partial-unreachable tick
# (e.g. kubectl down but DB up) never zeroes the others.
LASTID=0; LASTSHA=""; LASTEVICTED=""; LASTHITS=""; LASTMISSES=""; LASTSLOWQ=""
mkdir -p "$(dirname -- "$STATE")"
# First run (no state) or an explicit reset seeds a baseline silently rather
# than reporting every historical row as "new".
BASELINE=0
if [[ "$RESET_BASELINE" -eq 1 || ! -f "$STATE" ]]; then
    BASELINE=1
else
    # shellcheck disable=SC1090
    source "$STATE"
fi

echo "=== tick $(date -u '+%Y-%m-%d %H:%M:%SZ')  (web window ${WINDOW}) ==="

# --- 0. connectivity preflight ----------------------------------------------
# A fully-down VPN blackholes DNS/SYN and the mysql/kubectl connect timeouts do
# not cover that (a bare run took ~1000s). Fast TCP probe first; skip internal
# reads cleanly if prod's internal net is unreachable (VPN down is not a prod
# fault, so this is a clean exit 0, not a fail).
if ! python3 -c "import socket; socket.setdefaulttimeout(4); socket.create_connection(('$DBHOST',$DBPORT),4).close()" 2>/dev/null; then
    echo "OFFLINE: $DBHOST:$DBPORT unreachable in 4s (VPN down?) — skipping internal reads this tick."
    exit 0
fi

# --- 1. XRAS action_log (delta on LASTID) -----------------------------------
# mysql reads ~/.my.cnf on its own; fall back to SAM_DB_* if that file is absent.
MYSQL=(mysql --connect-timeout=8 -h "$DBHOST")
if [[ ! -f "$HOME/.my.cnf" && -n "${SAM_DB_USERNAME:-}" ]]; then
    MYSQL+=(-u "$SAM_DB_USERNAME" -p"${SAM_DB_PASSWORD:-}")
fi
q() { "${MYSQL[@]}" sam -N -e "$1" 2>/dev/null; }

MAXID=$(q "SELECT COALESCE(MAX(xras_action_log_id),0) FROM xras_action_log;" || true)
if [[ -z "$MAXID" ]]; then
    warn "xras: DB UNREACHABLE (mysql read failed — VPN/creds?)"
elif [[ "$BASELINE" -eq 1 ]]; then
    echo "xras: baseline seeded at #$MAXID"
elif [[ "$MAXID" -gt "$LASTID" ]]; then
    echo "xras: $((MAXID-LASTID)) new row(s)  #$((LASTID+1))..#$MAXID (classify per watch-prod skill)"
    "${MYSQL[@]}" sam --table -e "
        SELECT xras_action_log_id AS id, DATE_FORMAT(received_time,'%m-%d %H:%i') AS recv,
               action_type AS action, service, request_number AS reqno,
               projcode_result AS projcode, status, http_status AS http,
               LEFT(COALESCE(error_messages,''),80) AS err
        FROM xras_action_log WHERE xras_action_log_id > $LASTID
        ORDER BY xras_action_log_id;" 2>/dev/null
    # A new row is normal processing; only a non-2xx result is worth a flag.
    NONOK=$(q "SELECT COUNT(*) FROM xras_action_log WHERE xras_action_log_id > $LASTID AND (http_status IS NULL OR http_status NOT IN (200,201));" || true)
    [[ "${NONOK:-0}" -gt 0 ]] && warn "xras: $NONOK of $((MAXID-LASTID)) new row(s) non-2xx — classify (rechecked=ready for re-push)"
else
    echo "xras: quiet (still #$MAXID)"
fi

# --- 2. general web traffic (rolling ${WINDOW} snapshot) --------------------
# NOTE: kubectl logs -l <selector> defaults to --tail=10 PER POD — MUST pass
# --tail=-1 or the web section undercounts to ~10 lines/pod.
LOGS=$("${KCTL_NS[@]}" --request-timeout=15s logs -l "app=${WEBAPP_NAME}" \
       --since="$WINDOW" --tail=-1 --all-containers=true --timestamps=false 2>/dev/null || true)
if [[ -z "$LOGS" ]]; then
    warn "web: kubectl logs unreachable (VPN/RBAC?)"
else
    # Aggregates from gunicorn access lines (they carry the quoted request field;
    # app-logger lines do not).
    printf '%s\n' "$LOGS" | awk -F'"' '
        $2 ~ /^(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS) / {
            split($2,r," "); path=r[2]; sub(/\?.*/,"",path);
            split($3,s," "); status=s[1]; size=s[2]+0;
            total++; code[substr(status,1,1)]++; bytes+=size;
            if (path ~ /^\/static\//) staticn++; else nonstatic++;
            if (path !~ /^\/static\// && path ~ /\/\.env|\/\.git|\/wp-login|\/wp-admin|\/phpmyadmin|\.php([?\/]|$)|\/actuator|\/xmlrpc/) probe++;
        }
        END{
            printf "web: %d req  2xx=%d 3xx=%d 4xx=%d 5xx=%d  static=%.1f:1  %.1f MB\n",
                total, code["2"], code["3"], code["4"], code["5"],
                (nonstatic>0?staticn/nonstatic:0), bytes/1048576;
        }'
    # Anomaly gates -> verdict counters (parse the same access lines once more).
    WEB_STATS=$(printf '%s\n' "$LOGS" | awk -F'"' '
        $2 ~ /^(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS) / {
            split($2,r," "); path=r[2]; sub(/\?.*/,"",path);
            split($3,s," "); status=s[1];
            total++; code[substr(status,1,1)]++;
            if (path !~ /^\/static\// && path ~ /\/\.env|\/\.git|\/wp-login|\/wp-admin|\/phpmyadmin|\.php([?\/]|$)|\/actuator|\/xmlrpc/) probe++;
        }
        END{ printf "%d %d %d %d", total, code["5"], (total>0?100*code["4"]/total:0), probe }')
    read -r WEB_TOTAL WEB_5XX WEB_P4 WEB_PROBE <<<"$WEB_STATS" || true
    [[ "${WEB_5XX:-0}"  -gt 0 ]]  && fail "5xx=$WEB_5XX server error(s) this window"
    [[ "${WEB_P4:-0}"   -gt 40 ]] && warn "4xx=${WEB_P4}% of requests (heavy probing/scanning)"
    [[ "${WEB_PROBE:-0}" -gt 0 ]] && warn "$WEB_PROBE probe-path hit(s)"

    # Non-static latency percentiles (ms) via sort — macOS awk has no asort.
    printf '%s\n' "$LOGS" | awk -F'"' '
        $2 ~ /^(GET|POST|PUT) / { split($2,r," "); if(r[2] ~ /^\/static\//) next;
            if(match($7,/[0-9]+µs/)) print (substr($7,RSTART,RLENGTH-2)+0)/1000.0; }' \
        | sort -n | awk '{a[NR]=$1}
            END{ if(NR){ p50=a[int(NR*.5)?int(NR*.5):1]; p95=a[int(NR*.95)?int(NR*.95):1];
                         p99=a[int(NR*.99)?int(NR*.99):1];
                         printf "  lat ms: p50=%.0f p95=%.0f p99=%.0f max=%.0f (n=%d)\n",p50,p95,p99,a[NR],NR;
                         if(p95>4000) exit 42; } }' || {
        [[ "$?" -eq 42 ]] && warn "p95 latency > 4000ms (latency regression?)"; }

    # Slow-request WARNINGs (>5s, app threshold).
    SLOW=$(printf '%s\n' "$LOGS" | grep -F 'Slow request:' || true)
    NSLOW=$(printf '%s' "$SLOW" | grep -c . || true)
    if [[ "${NSLOW:-0}" -gt 0 ]]; then
        TOP=$(printf '%s\n' "$SLOW" | sed -E 's/.*Slow request: //' | awk '{$1=$1;print $3,$4}' \
              | sort | uniq -c | sort -rn | head -1 | sed 's/^ *//')
        echo "  slow(>5s): $NSLOW  top: $TOP"
        note "known-slow (under investigation — docs/plans/FSTREE_LATENCY_INVESTIGATION.md): directory_access ~6.9s, fstree/Casper ~3s DB + app tail under load"
        # Per-endpoint split from the app's per-DB tokens. The line carries
        # cpu=, one <db>=Xms/Nq per database TOUCHED (sam/status/jobhistory/
        # fsscans), and pool= when non-zero; total ~= cpu + Σdb + pool + rest
        # (GIL/pool-not-attributed). We name the dominant DB rather than the
        # backend platform. A token whose value is not <float>ms (none in the
        # current format) is skipped. Tab-keyed so a decoded space in the path
        # ("Casper GPU") stays one key.
        SPLIT=$(printf '%s\n' "$SLOW" \
            | sed -n -E 's/.*Slow request: ([0-9.]+) ms  (.*)  \((.*)\).*/\1\t\2\t\3/p' \
            | awk -F'\t' '
                { key=$2; cnt[key]++; T[key]+=$1+0;
                  n=split($3, toks, " ");
                  for(i=1;i<=n;i++){ eq=index(toks[i],"=");
                    if(eq==0) continue;
                    name=substr(toks[i],1,eq-1); val=substr(toks[i],eq+1);
                    if(name=="who"){ if(!(key in WHO)) WHO[key]=val; else if(WHO[key]!=val) WHO[key]="mixed"; continue; }
                    s=index(val,"/"); if(s>0) val=substr(val,1,s-1);
                    if(val !~ /ms$/) continue;
                    sub(/ms$/,"",val); v=val+0;
                    if(name=="cpu") CPU[key]+=v;
                    else if(name=="pool") POOL[key]+=v;
                    else { DBSUM[key]+=v; DB[key SUBSEP name]+=v; seen[key SUBSEP name]=name; } } }
                END{ for(k in cnt){ n=cnt[k]; tt=T[k]/n;
                       best=""; bestv=-1;
                       for(kk in seen){ split(kk,pp,SUBSEP);
                         if(pp[1]==k && DB[kk]>bestv){ bestv=DB[kk]; best=seen[kk]; } }
                       cpu=CPU[k]/n; pool=POOL[k]/n; dbs=DBSUM[k]/n;
                       rest=tt-cpu-dbs-pool; if(rest<0) rest=0;
                       line=sprintf("  ↳ %s: total≈%.0fms — cpu≈%.0f%%", k, tt, tt>0?100*cpu/tt:0);
                       if(best!="") line=line sprintf(", %s≈%.0f%%", best, tt>0?100*(bestv/n)/tt:0);
                       if(pool>0) line=line sprintf(", pool≈%.0f%%", tt>0?100*pool/tt:0);
                       line=line sprintf(", rest≈%.0f%% (%dx", tt>0?100*rest/tt:0, n);
                       if(k in WHO && WHO[k]!="mixed") line=line sprintf(", who=%s", WHO[k]);
                       line=line ")";
                       printf "%.0f\t%s\n", tt, line; } }' \
            | sort -rn | head -4 | cut -f2-)
        [[ -n "$SPLIT" ]] && printf '%s\n' "$SPLIT"
    fi
    # Read-model gate verdicts (the rm= token). Report-only: a live count is
    # expected inside the hourly too-old window; a trend is what matters.
    RM=$(printf '%s\n' "$LOGS" | { grep -oE 'rm=[a-z-]+(:[a-z0-9-]+)?' || true; } | awk -F'[=:]' '
        { if($2=="served") s++; else if($2=="patched"){p++; t+=$3+0} else if($2=="live"){l++; r[$3]++} }
        END{ if(s+p+l){ line=sprintf("  read-model: served=%d patched=%d (trees=%d) live=%d", s,p,t,l);
             for(k in r) line=line sprintf(" %s=%d", k, r[k]); print line } }')
    [[ -n "$RM" ]] && printf '%s\n' "$RM"
fi

# --- 2b. load context (DB tier + app-pod CPU) -------------------------------
# The discriminator for a slow expensive endpoint: DB-bound vs app queueing.
# Fail-soft: a missing metric prints n/a, never aborts the tick.
CUR_SLOWQ=$(q "SHOW GLOBAL STATUS WHERE Variable_name='Slow_queries'" | awk '{print $2}')
TR=$(q "SHOW GLOBAL STATUS WHERE Variable_name='Threads_running'" | awk '{print $2}')
TC=$(q "SHOW GLOBAL STATUS WHERE Variable_name='Threads_connected'" | awk '{print $2}')
if [[ -n "$TR" ]]; then
    SQD="n/a"
    [[ -n "$CUR_SLOWQ" && -n "$LASTSLOWQ" ]] && SQD=$((CUR_SLOWQ-LASTSLOWQ))
    DBLOAD="dbload: threads_running=$TR conns=$TC slow_q(Δ)=$SQD"
else
    DBLOAD="dbload: n/a"
fi
PODCPU=$("${KCTL_NS[@]}" top pods -l "app=${WEBAPP_NAME}" --no-headers 2>/dev/null \
         | awk '{c=$2; sub(/m$/,"",c); s+=c+0; if(c+0>mx)mx=c+0}
                END{ if(NR) printf "podcpu: sum=%dm max=%dm (%d pods)", s, mx, NR; else print "podcpu: n/a" }')
[[ -z "$PODCPU" ]] && PODCPU="podcpu: n/a"
echo "  $DBLOAD | $PODCPU"

# --- 3. pod health ----------------------------------------------------------
PODS=$("${KCTL_NS[@]}" --request-timeout=10s get pods -l "app=${WEBAPP_NAME}" \
  -o jsonpath='{range .items[*]}{.metadata.name}={.status.phase}/r{.status.containerStatuses[0].restartCount}/{.spec.containers[0].image}{"\n"}{end}' 2>/dev/null || true)
SHA=""
if [[ -z "$PODS" ]]; then
    warn "pods: kubectl unreachable (VPN/RBAC?)"
else
    SHA=$(echo "$PODS" | grep -oE 'sha-[0-9a-f]+' | sort -u | tr '\n' ' ' | sed 's/ $//' || true)
    NONRUNNING=$(echo "$PODS" | grep -vE '=Running/' || true)
    RESTARTS=$(echo "$PODS" | grep -oE '/r[0-9]+/' | grep -vE '/r0/' || true)
    echo "pods: sha=$SHA  ($(echo "$PODS" | grep -c . | tr -d ' ') pods)"
    [[ -n "$NONRUNNING" ]] && fail "pod(s) NOT RUNNING: $NONRUNNING"
    [[ -n "$RESTARTS" ]]   && warn "pod restart count != 0"
    [[ -n "$LASTSHA" && "$SHA" != "$LASTSHA" ]] && warn "image changed: $LASTSHA -> $SHA (deploy since last tick)"
fi

# --- 3b. redis cache sizing (read-only INFO) --------------------------------
# The load-bearing signal is evicted_keys RISING between ticks: with allkeys-lru
# that means live entries are being dropped -> raise cache.maxmemoryMB.
CUR_EVICTED=""; CUR_HITS=""; CUR_MISSES=""
RPOD=$("${KCTL_NS[@]}" --request-timeout=10s get pods -o name 2>/dev/null | grep -i "$REDIS_NAME" | head -1 || true)
if [[ -z "$RPOD" ]]; then
    warn "cache: redis pod not found (VPN/RBAC?)"
else
    RINFO=$("${KCTL_NS[@]}" --request-timeout=15s exec "$RPOD" -- redis-cli INFO 2>/dev/null || true)
    if [[ -z "$RINFO" ]]; then
        warn "cache: redis unreachable"
    else
        # `|| true` on each extraction: a missing INFO field (e.g. db1 is absent
        # when the ratelimit DB is empty) fails grep, and pipefail+set -e would
        # otherwise abort the whole run on that one absent key.
        rf() { printf '%s\n' "$RINFO" | grep -m1 "^$1:" | cut -d: -f2 | tr -d '\r' || true; }
        used=$(rf used_memory); peak=$(rf used_memory_peak); maxm=$(rf maxmemory)
        CUR_EVICTED=$(rf evicted_keys); CUR_HITS=$(rf keyspace_hits); CUR_MISSES=$(rf keyspace_misses)
        db0=$(printf '%s\n' "$RINFO" | grep -m1 '^db0:' | sed -E 's/.*keys=([0-9]+).*/\1/' || true); db0=${db0:-0}
        db1=$(printf '%s\n' "$RINFO" | grep -m1 '^db1:' | sed -E 's/.*keys=([0-9]+).*/\1/' || true); db1=${db1:-0}
        ev_delta=$(( CUR_EVICTED - ${LASTEVICTED:-$CUR_EVICTED} ))
        dh=$(( CUR_HITS - ${LASTHITS:-$CUR_HITS} )); dm=$(( CUR_MISSES - ${LASTMISSES:-$CUR_MISSES} ))
        awk -v u="$used" -v pk="$peak" -v mx="$maxm" -v h="$CUR_HITS" -v ms="$CUR_MISSES" \
            -v dh="$dh" -v dm="$dm" -v evd="$ev_delta" -v evc="$CUR_EVICTED" \
            -v d0="$db0" -v d1="$db1" 'BEGIN{
          mb=1048576;
          pct=(mx>0)?100*u/mx:0; cum=(h+ms>0)?100*h/(h+ms):0;
          inter=(dh+dm>0)?sprintf("%.0f%%",100*dh/(dh+dm)):"n/a";
          printf "cache: mem %.0fM/%.0fM (%.0f%%, peak %.0fM)  hit %.1f%% (Δ %s)  keys db0=%d db1=%d  evicted %d (Δ%d)\n",
                 u/mb, mx/mb, pct, pk/mb, cum, inter, d0, d1, evc, evd;
        }'
        [[ "${ev_delta:-0}" -gt 0 ]] && warn "$ev_delta keys evicted since last tick — cache pressure (allkeys-lru); consider raising cache.maxmemoryMB"
    fi
fi

# --- 4. samuel-tasks CronJob (read-only report — never touch it) -------------
# The dispatcher wakes hourly and the ledger records occurrences, not wake-ups,
# so "is it alive?" is answerable only from the CronJob object + its Jobs. We
# REPORT problems here; remediation (kubectl create job --from=cronjob/...) is a
# human decision (Ben owns deploy mechanics), never automated by this watch.
if ! "${KCTL_NS[@]}" get cronjob "$TASKS_NAME" >/dev/null 2>&1; then
    echo "tasks: CronJob '$TASKS_NAME' not found (helm tasks.enabled=false?)"
else
    CJ_JSON=$("${KCTL_NS[@]}" get cronjob "$TASKS_NAME" -o json 2>/dev/null || echo '{}')
    CJ_SUSPEND=$(echo "$CJ_JSON" | jq -r '.spec.suspend // false')
    CJ_LAST=$(echo    "$CJ_JSON" | jq -r '.status.lastScheduleTime // ""')
    JOBS_JSON=$("${KCTL_NS[@]}" get jobs -l "$TASKS_SELECTOR" -o json 2>/dev/null || echo '{"items":[]}')
    N_FAILED=$(echo "$JOBS_JSON" | jq '[.items[] | select((.status.failed // 0) > 0)] | length' 2>/dev/null || echo 0)
    AGE_S=""; [[ -n "$CJ_LAST" ]] && AGE_S=$(seconds_since "$CJ_LAST" 2>/dev/null || echo "")
    if [[ -n "$AGE_S" ]]; then LAST_STR="$((AGE_S/60))m ago"; else LAST_STR="never"; fi
    echo "tasks: suspend=$CJ_SUSPEND  last=$LAST_STR  failedJobs=$N_FAILED"
    if [[ "$CJ_SUSPEND" == "true" ]]; then
        fail "samuel-tasks CronJob is SUSPENDED — nothing is being dispatched"
    elif [[ -n "$AGE_S" && "$AGE_S" -gt "$TASKS_MAX_SILENCE_S" ]]; then
        fail "dispatcher silent $((AGE_S/60))m (>$((TASKS_MAX_SILENCE_S/60))m) — it has stopped waking"
    fi
    [[ "${N_FAILED:-0}" -gt 0 ]] && warn "$N_FAILED retained samuel-tasks Job(s) failed — inspect: kubectl -n $NAMESPACE logs job/<name>"
    if [[ "$CJ_SUSPEND" == "true" || ( -n "$AGE_S" && "$AGE_S" -gt "$TASKS_MAX_SILENCE_S" ) ]]; then
        note "manual remedy (human decision, NOT run here): kubectl -n $NAMESPACE create job --from=cronjob/$TASKS_NAME ${TASKS_NAME}-manual"
    fi
fi

# --- 5. persist state -------------------------------------------------------
NEW_LASTID="$MAXID"; [[ -z "$NEW_LASTID" || "$NEW_LASTID" == 0 ]] && NEW_LASTID="$LASTID"
{
    echo "LASTID=$NEW_LASTID"
    echo "LASTSHA=\"${SHA:-$LASTSHA}\""
    echo "LASTEVICTED=${CUR_EVICTED:-$LASTEVICTED}"
    echo "LASTHITS=${CUR_HITS:-$LASTHITS}"
    echo "LASTMISSES=${CUR_MISSES:-$LASTMISSES}"
    echo "LASTSLOWQ=${CUR_SLOWQ:-$LASTSLOWQ}"
} > "$STATE"

# --- verdict (compact; exit 0 quiet / 1 warn / 2 fail) ----------------------
if [[ $FAIL_COUNT -gt 0 || $WARN_COUNT -gt 0 ]]; then
    echo "  (${WARN_COUNT} warn, ${FAIL_COUNT} fail this tick)"
fi
if   [[ $FAIL_COUNT -gt 0 ]]; then exit 2
elif [[ $WARN_COUNT -gt 0 ]]; then exit 1
else exit 0
fi
