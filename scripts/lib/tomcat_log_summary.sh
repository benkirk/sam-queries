#!/bin/bash
# tomcat_log_summary.sh: tab-separated records about legacy SAM (Tomcat on sam-tomcat), read
# from /tomcat/tomcat-sam/logs only. Read-only: no writes, no redirection to a file, and never
# the syslog pipes under the system log directory (a read steals their entries).
# scripts/tomcat_watch.sh sends it over ssh (`ssh HOST bash -s -- ARGS < this`); TOMCAT_LOGS
# points it at a fixture tree; TOMCAT_NOW and TOMCAT_JVM_START pin the clock and the JVM in
# tests. Times are UTC epochs.
#   args: window FROM TO BFROM BTO [days YYYYMMDD ...]   (BFROM=0 skips the week-ago window)
#   host now= up_s= jvm_pid= jvm_start= tomcat_pct= rotated=
#   api FAMILY CLIENT UA STATUS N MS_SUM MS_MAX     base FAMILY CLIENT UA STATUS N
#   lat EDGE_MS N    web FAMILY STATUS N    scan N    probe LAST N    slow EPOCH FAMILY CLIENT UA MS
#   (probe = GET / from a UA starting TOMCAT_PROBE_UA, default libwww-perl; other GET / is web)
#   err SOURCE N     errmsg SOURCE N FIRST LAST TEXT
#   day D FAMILY CLIENT UA STATUS N    dayweb D FAMILY N    dayscan D N    dayprobe D N
set -u
logs="${TOMCAT_LOGS:-/tomcat/tomcat-sam/logs}"
now="${TOMCAT_NOW:-$(date +%s)}"
T=""; command -v timeout >/dev/null 2>&1 && T="timeout 60"
probe_ua="${TOMCAT_PROBE_UA:-libwww-perl}"
export TZ=America/Denver

ldate() { date -d "@$1" "+$2" 2>/dev/null || date -r "$1" "+$2"; }
access() { if [[ -f "$logs/access.log-$1" ]]; then $T cat "$logs/access.log-$1";
           elif [[ -f "$logs/access.log-$1.gz" ]]; then $T gzip -dc "$logs/access.log-$1.gz"; fi; }

# Shared awk: request epoch from the bracketed time, the path family, the record fields.
AWKLIB='
BEGIN { split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " "); for (i = 1; i <= 12; i++) mon[m[i]] = i }
function civil(y, mo, d,   era, yoe, doy) {
    if (mo <= 2) y -= 1
    era = int((y >= 0 ? y : y - 399) / 400); yoe = y - era * 400
    doy = int((153 * (mo + (mo > 2 ? -3 : 9)) + 2) / 5) + d - 1
    return era * 146097 + yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy - 719468
}
function epoch(ts, off,   e, o) {
    e = civil(substr(ts, 9, 4) + 0, mon[substr(ts, 5, 3)], substr(ts, 2, 2) + 0) * 86400 \
        + substr(ts, 14, 2) * 3600 + substr(ts, 17, 2) * 60 + substr(ts, 20, 2)
    o = substr(off, 2, 2) * 3600 + substr(off, 4, 2) * 60
    return substr(off, 1, 1) == "-" ? e + o : e - o
}
function clean(s) { gsub(/[^A-Za-z0-9_.\/:+-]/, "_", s); return substr(s, 1, 48) }
function family(p,   s, n) {
    sub(/[?#].*/, "", p); gsub(/\/\/+/, "/", p)
    if (p == "/" || p == "") return "root"
    n = split(p, s, "/")
    if (s[2] != "api") return s[2] == "app" ? "web:app" : (s[2] == "saml" ? "web:saml" : "web:other")
    if (s[3] == "xras") return "xras/" clean(s[4])
    if (s[3] != "protected") return "scan"
    if (s[4] == "amie") return "ignore"
    if (s[4] == "admin") {
        if (s[5] ~ /^(ssg|mssg|dasg|sysacct|ldapsync|peoplesearch)$/) return s[5] "/" clean(s[6])
        return "admin/" clean(s[5])
    }
    if (s[5] ~ /^v[0-9]+$/) return clean(s[4]) "/" clean(s[6])
    return clean(s[4]) "/" clean(s[5])
}
# Locates the bracketed time so a multi-address X-Forwarded-For cannot shift the fields.
function parse(   i) {
    for (i = 6; i <= NF; i++) if ($i ~ /^\[[0-9][0-9]\/[A-Z][a-z][a-z]\/[0-9][0-9][0-9][0-9]:/) break
    if (i > NF || $(i + 5) !~ /^[1-5][0-9][0-9]$/) return 0
    T_EP = epoch($i, $(i + 1)); FAM = family($(i + 3)); ST = $(i + 5)
    MS = $(i + 7) + 0; UA = $(i + 8); gsub(/"/, "", UA); UA = clean(UA)
    CL = $5; sub(/,$/, "", CL); CL = clean(CL)
    if (FAM == "root" && index(UA, probe_ua) != 1) FAM = "web:other"
    return 1
}
'

window() {
    local from=$1 to=$2 bfrom=$3 bto=$4 d f
    local dates; dates=$(printf '%s\n' "$(ldate "$from" %Y%m%d)" "$(ldate $((to + 120)) %Y%m%d)" | sort -u)
    local bdates=""
    [[ "$bfrom" != 0 ]] && bdates=$(printf '%s\n' "$(ldate "$bfrom" %Y%m%d)" "$(ldate $((bto + 120)) %Y%m%d)" | sort -u)
    {
        for d in $dates; do access "$d" | sed 's/^/W /'; done
        for d in $bdates; do access "$d" | sed 's/^/B /'; done
    } | $T awk -v from="$from" -v to="$to" -v bfrom="$bfrom" -v bto="$bto" -v probe_ua="$probe_ua" "$AWKLIB"'
        { w = $1; $1 = ""; $0 = substr($0, 2); if (!parse()) { if (w == "W") badreq++; next } }
        w == "B" { if (T_EP >= bfrom && T_EP < bto && FAM != "ignore" && FAM !~ /^(scan|root|web:)/)
                       base[FAM "\t" CL "\t" UA "\t" ST]++
                   next }
        T_EP < from || T_EP >= to { next }
        FAM == "ignore" { next }
        FAM == "root" { pn++; if (T_EP > plast) plast = T_EP; next }
        FAM == "scan" { scan++; next }
        FAM ~ /^web:/ { web[substr(FAM, 5) "\t" ST]++; next }
        { k = FAM "\t" CL "\t" UA "\t" ST; n[k]++; sum[k] += MS; if (MS > mx[k]) mx[k] = MS
          if (ST < 400) { e = 1; while (e <= ne && MS > edge[e]) e++; lat[e]++ }
          if (MS > 4000) { ns++; slow[ns] = T_EP "\t" FAM "\t" CL "\t" UA "\t" MS } }
        BEGIN { ne = split("100 250 500 1000 2000 4000 8000", edge, " ") }
        END {
            c = 0; for (k in n) if (c++ < 200) printf "api\t%s\t%d\t%d\t%d\n", k, n[k], sum[k], mx[k]
            c = 0; for (k in base) if (c++ < 200) printf "base\t%s\t%d\n", k, base[k]
            for (e = 1; e <= ne + 1; e++) if (lat[e]) printf "lat\t%s\t%d\n", (e <= ne ? edge[e] : "inf"), lat[e]
            for (k in web) printf "web\t%s\t%d\n", k, web[k]
            printf "scan\t%d\n", scan + badreq
            printf "probe\t%d\t%d\n", plast, pn
            for (i = (ns > 5 ? ns - 4 : 1); i <= ns; i++) printf "slow\t%s\n", slow[i]
        }'
    errors "$from" "$to" "$dates"
}

# sam.log ERROR and catalina.out SEVERE lines in the window, digits and quoted values masked.
errors() {
    local from=$1 to=$2 dates=$3 d f off today year
    off=$(ldate "$from" %z); today=$(ldate "$now" %Y%m%d); year=$(ldate "$to" %Y)
    {
        for d in $dates; do
            f="$logs/sam.log.${d:0:4}-${d:4:2}-${d:6:2}"
            [[ -f "$f" ]] || { [[ "$d" == "$today" ]] && f="$logs/sam.log"; }
            [[ -f "$f" ]] && $T grep -h ' ERROR ' "$f" | sed 's/^/S /'
        done
        [[ -f "$logs/catalina.out" ]] && $T grep -h ' SEVERE' "$logs/catalina.out" | sed 's/^/C /'
    } | $T awk -v from="$from" -v to="$to" -v off="$off" -v year="$year" -v sq="'" "$AWKLIB"'
        $1 == "S" { ts = "[" substr($2, 9, 2) "/" m[substr($2, 6, 2) + 0] "/" substr($2, 1, 4) ":" substr($3, 1, 8)
                    src = "sam.log"; msg = $0; sub(/^S [^ ]+ [^ ]+ ERROR /, "", msg) }
        $1 == "C" { ts = "[" sprintf("%02d", $3) "/" $2 "/" year ":" $4
                    src = "catalina"; msg = $0; sub(/^.* SEVERE:? */, "", msg) }
        { e = epoch(ts, off); if ($1 == "C" && e > to + 172800) e -= 31536000
          if (e < from || e >= to) next
          gsub(/"[^"]*"/, "Q", msg); gsub(sq "[^" sq "]*" sq, "Q", msg); gsub(/[0-9]+/, "N", msg); gsub(/\t/, " ", msg)
          msg = substr(msg, 1, 100); cnt[src]++; k = src "\t" msg
          mc[k]++; if (!(k in first)) first[k] = e; last[k] = e }
        END {
            printf "err\tsam.log\t%d\nerr\tcatalina\t%d\n", cnt["sam.log"], cnt["catalina"]
            for (r = 0; r < 6; r++) { best = ""; for (k in mc) if (best == "" || mc[k] > mc[best]) best = k
                if (best == "") break
                split(best, b, "\t"); printf "errmsg\t%s\t%d\t%d\t%d\t%s\n", b[1], mc[best], first[best], last[best], b[2]
                delete mc[best] }
        }'
}

days() {
    local d
    for d in "$@"; do
        access "$d" | $T awk -v d="$d" -v probe_ua="$probe_ua" "$AWKLIB"'
            !parse() { scan++; next }
            FAM == "ignore" { next }
            FAM == "root" { pn++; next }
            FAM == "scan" { scan++; next }
            FAM ~ /^web:/ { web[substr(FAM, 5)]++; next }
            { n[FAM "\t" CL "\t" UA "\t" ST]++; any = 1 }
            END { if (!any && !scan && !pn) exit
                  c = 0; for (k in n) if (c++ < 400) printf "day\t%s\t%s\t%d\n", d, k, n[k]
                  for (k in web) printf "dayweb\t%s\t%s\t%d\n", d, k, web[k]
                  printf "dayscan\t%s\t%d\ndayprobe\t%s\t%d\n", d, scan, d, pn }'
    done
}

host() {
    local pid et jstart pct rot=- up f
    read -r pid et < <(ps -o pid=,etimes= -C java 2>/dev/null | head -1)
    jstart=0; [[ -n "${et:-}" ]] && jstart=$(( now - et ))
    jstart="${TOMCAT_JVM_START:-$jstart}"
    pct=$(df -P "$logs" 2>/dev/null | awk 'NR == 2 { sub(/%/, "", $5); print $5 }')
    for f in "$logs"/catalina.out.*; do [[ -e "$f" ]] && rot=${f##*/}; done
    up=$(awk '{ print int($1) }' /proc/uptime 2>/dev/null)
    printf 'host\tnow=%s\tup_s=%s\tjvm_pid=%s\tjvm_start=%s\ttomcat_pct=%s\trotated=%s\n' \
        "$now" "${up:-0}" "${pid:-0}" "$jstart" "${pct:-0}" "$rot"
}

{
    if [[ "${1:-}" == window ]]; then
        host
        window "$2" "$3" "$4" "$5"
        shift 5
    fi
    if [[ "${1:-}" == days ]]; then shift; days "$@"; fi
} | head -n 5000
