#!/bin/sh
# shellcheck disable=SC2218  # 0.11 misreports fail/host/need as defined later
# Fail-closed checks run before ldap / syncd start. Usage: guard.sh ldap|syncd|test
# Nothing leaves this testbed: LDAP staging is always stubbed, and SAM writes are
# stubbed unless SAM_URL is our own webapp (a laptop server or samuel-dev).
mode=${1:?"usage: guard.sh ldap|syncd|test"}
S=${SECRETS_VOL:-/run/secrets}

fail() { echo "guard($mode): $*" >&2; exit 1; }
host() { echo "$1" | sed -E 's#^[A-Za-z]+://([^/:]+).*#\1#'; }
need() { [ -s "$S/$1" ] || fail "missing or empty secret $1 (see README, Secrets)"; }

check_ldap() {
    # A wrong password is a failed bind as citldapsam on fdb; refuse rather than try.
    if [ "$PIPELINE_MODE" = hybrid ] && [ "$SYNCREPL_PROVIDER_AUTHCID" != anonymous ]; then
        fail "hybrid mode needs SYNCREPL_PROVIDER_AUTHCID=anonymous"
    fi
    auth="LDAP_AUTH_${SYNCREPL_PROVIDER_AUTHCID:-citldapsam}"
    need "$auth"
    # `ldapsearch -y` sends the whole file, so a trailing newline is part of the password.
    [ -n "$(tail -c1 "$S/$auth")" ] || fail "$auth ends in a newline; strip it (perl -pi -e 'chomp if eof')"
    need sam-idms-ldap/admin_password
    need sam-idms-ldap/config_password
    need sam-idms-ldap/readonly_user_password
    who="as $SYNCREPL_PROVIDER_AUTHCID"
    [ "$PIPELINE_MODE" = hybrid ] && who="(startup lookup only; hybrid mode, no syncrepl)"
    echo "guard(ldap): ok, replicating from $SYNCREPL_PROVIDER_URL $who, RID $SYNCREPL_CONSUMER_RID"
}

check_syncd() {
    [ -n "$LDAPSTAGING_UPDATES_STUB" ] || fail "LDAPSTAGING_UPDATES_STUB is empty"
    case $(host "$LDAPSTAGING_URL") in
        *.invalid) ;;
        *) fail "LDAPSTAGING_URL must name a .invalid host, not $LDAPSTAGING_URL" ;;
    esac
    need "LDAPSTAGING_AUTH_${LDAPSTAGING_USER:-citldapsam}"

    sam=$(host "$SAM_URL")
    # Writes may reach only our own webapp: a laptop server, or samuel-dev by exact name
    # (docs/plans/LDAPSYNC_DEV_SHADOW.md). Prod stays refused by the case below.
    if [ -z "$SAM_UPDATES_STUB" ] && [ "$sam" != host.docker.internal ] \
            && [ "$sam" != localhost ] && [ "$sam" != 127.0.0.1 ] \
            && [ "$sam" != samuel-dev.k8s.ucar.edu ]; then
        fail "SAM_UPDATES_STUB is empty and SAM_URL ($sam) is not our webapp"
    fi
    case $sam in
        sam.ucar.edu)
            [ "$SAM_READ_PROD" = 1 ] || fail "SAM_URL is prod; set SAM_READ_PROD=1 to read from it"
            # Legacy's ldapsync/status answers 500 in prod, and every 500 is mailed to SWEG.
            [ "$mode" != test ] || fail "--test-connections calls ldapsync/status, which 500s on prod"
            ;;
    esac
    grep -q "^SAM_AUTH_${SAM_USER:-admin}=." "$S/sam.parm" 2>/dev/null \
        || fail "sam.parm has no SAM_AUTH_${SAM_USER:-admin}= line"

    echo "guard($mode): ok, SAM reads from $sam; SAM writes to ${SAM_UPDATES_STUB:-$SAM_URL};" \
         "staging writes to $LDAPSTAGING_UPDATES_STUB"
}

case $mode in
    ldap) check_ldap ;;
    syncd|test) check_syncd ;;
    *) fail "unknown mode" ;;
esac
