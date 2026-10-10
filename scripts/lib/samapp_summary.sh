#!/bin/bash
# samapp_summary.sh: tab-separated records about the sam-app container zoo (the
# sam-ldap-syncd client of legacy SAM), read as a plain login: ps, df, git heads and the
# dated syncd logs. Read-only. Never reads a credential file, and needs no container or
# service manager access. scripts/tomcat_watch.sh sends it over ssh like
# tomcat_log_summary.sh; SAMAPP_DATA / SAMAPP_HOME / SAMAPP_NOW point it at a fixture.
#   args: window FROM TO   (UTC epochs)
#   app now= up_s= data_pct=    aproc NAME PID START    arepo NAME SHA COMMIT_EPOCH SUBJECT
#   astate NAME NEWEST_MTIME    async files= applied= defer= errors= last=
#   aput METHOD STATUS N        aerr N FIRST LAST TEXT
set -u
data="${SAMAPP_DATA:-/data/tomcat-sam/prod}"
home="${SAMAPP_HOME:-/home/swes}"
now="${SAMAPP_NOW:-$(date +%s)}"
T=""; command -v timeout >/dev/null 2>&1 && T="timeout 30"
export TZ=America/Denver
REPOS="sam-app sam-ldap-syncd sam-idms-ldap ucarldap sam-ldap-transformer amie-sam-mediator acct-sam-etl"

ldate() { date -d "@$1" "+$2" 2>/dev/null || date -r "$1" "+$2"; }
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null; }

app() {
    local pct up
    pct=$(df -P "$data" 2>/dev/null | awk 'NR == 2 { sub(/%/, "", $5); print $5 }')
    up=$(awk '{ print int($1) }' /proc/uptime 2>/dev/null)
    printf 'app\tnow=%s\tup_s=%s\tdata_pct=%s\n' "$now" "${up:-0}" "${pct:-0}"
    ${SAMAPP_PS:-ps -eo pid=,etimes=,args=} 2>/dev/null | awk -v now="$now" '
        $3 ~ /awk$/ { next }
        $3 ~ /slapd$/ { n = "slapd" }
        /sam-ldap-sync[d]\/bin\/syncd/ { n = "syncd" }
        /ldif_log_process[o]r/ { n = "ldif" }
        n != "" && !(n in seen) { seen[n] = 1; printf "aproc\t%s\t%s\t%d\n", n, $1, now - $2 }
        { n = "" }'
    local r out
    for r in $REPOS; do
        [[ -d "$home/$r/.git" ]] || continue
        out=$($T git -c safe.directory='*' -C "$home/$r" log -1 --format='%h%x09%ct%x09%s' 2>/dev/null) || continue
        printf 'arepo\t%s\t%s\n' "$r" "$(cut -c1-90 <<<"$out")"
    done
    local d newest f
    for d in auditlog.d syncd; do
        # shellcheck disable=SC2012  # ls -t is the portable newest-mtime pick (GNU and BSD)
        newest=$(ls -t "$data/sam-ldap-syncd/$d" 2>/dev/null | head -1)
        [[ -n "$newest" ]] && f=$(mtime "$data/sam-ldap-syncd/$d/$newest") && printf 'astate\t%s\t%s\n' "$d" "$f"
    done
}

# The syncd log lines in [FROM, TO): `2026-10-10T07:11:32-06  msg`, one file per Mountain day.
syncd() {
    local from=$1 to=$2 d
    for d in $(printf '%s\n' "$(ldate "$from" %F)" "$(ldate "$to" %F)" | sort -u); do
        [[ -f "$data/sam-ldap-syncd/log-$d-syncd.e" ]] && $T cat "$data/sam-ldap-syncd/log-$d-syncd.e"
    done | $T awk -v from="$from" -v to="$to" -v sq="'" '
        function civil(y, mo, d,   era, yoe, doy) {
            if (mo <= 2) y -= 1
            era = int((y >= 0 ? y : y - 399) / 400); yoe = y - era * 400
            doy = int((153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5) + d - 1
            return era * 146097 + yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy - 719468
        }
        $1 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][-+][0-9][0-9]/ { next }
        { ts = $1
          e = civil(substr(ts, 1, 4) + 0, substr(ts, 6, 2) + 0, substr(ts, 9, 2) + 0) * 86400 \
              + substr(ts, 12, 2) * 3600 + substr(ts, 15, 2) * 60 + substr(ts, 18, 2)
          o = substr(ts, 21, 2) * 3600; e = substr(ts, 20, 1) == "-" ? e + o : e - o
          if (e >= to) next
          if (e > last) last = e
          if (e < from) next
          msg = $0; sub(/^[^ \t]+[ \t]+/, "", msg) }
        msg ~ /update files found/ { files++ }
        msg ~ /^File .* processed/ { applied++ }
        msg ~ /^Deferring propagation/ { defer++ }
        match(msg, /^[A-Z]+ status: [0-9][0-9][0-9]/) { split(substr(msg, 1, RLENGTH), p, " "); put[p[1] "\t" p[3]]++ }
        msg !~ /^[A-Z]+ status: / && tolower(msg) ~ /error|exception|fatal|died|failed/ {
            errs++
            gsub(/"[^"]*"/, "Q", msg); gsub(sq "[^" sq "]*" sq, "Q", msg)
            gsub(/(user|userName|username|uid|upid|key)( => |=|: | )[^ ,;)]+/, "ID", msg)
            gsub(/[0-9]+/, "N", msg); msg = substr(msg, 1, 100)
            mc[msg]++; if (!(msg in first)) first[msg] = e; lastm[msg] = e }
        END {
            printf "async\tfiles=%d\tapplied=%d\tdefer=%d\terrors=%d\tlast=%d\n", files, applied, defer, errs, last
            for (k in put) printf "aput\t%s\t%d\n", k, put[k]
            for (r = 0; r < 3; r++) { best = ""; for (k in mc) if (best == "" || mc[k] > mc[best]) best = k
                if (best == "") break
                printf "aerr\t%d\t%d\t%d\t%s\n", mc[best], first[best], lastm[best], best; delete mc[best] }
        }'
}

{
    if [[ "${1:-}" == window ]]; then app; syncd "$2" "$3"; fi
} | head -n 200
