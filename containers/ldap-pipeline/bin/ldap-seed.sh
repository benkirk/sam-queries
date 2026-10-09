#!/bin/sh
# Load a bin/ldif-fold snapshot into the replica's main database, with slapd stopped.
# The replica must have bootstrapped its config once (first `up ldap`, then stop it).
set -eu
f=/seed/$SEED_FILE
db=/var/lib/ldap
fail() { echo "ldap-seed: $*" >&2; exit 1; }
[ -s "$f" ] || fail "$f missing or empty; run bin/ldif-fold first"
[ -d "/etc/ldap/slapd.d/cn=config" ] || fail "no slapd config yet: up ldap once, stop it, rerun"
if slapcat -F /etc/ldap/slapd.d -n "${MAIN_DBNUM:-1}" -a '(x-ucar-upid=*)' 2>/dev/null | grep -q '^dn:' \
        && [ "${RESEED:-0}" != 1 ]; then
    fail "the replica already holds people; RESEED=1 to replace it"
fi
# The bootstrap leaves a base and admin entry; the seed brings its own base.
find "$db" -mindepth 1 -delete
slapadd -F /etc/ldap/slapd.d -n "${MAIN_DBNUM:-1}" -q -l "$f"
chown -R openldap:openldap "$db"
rm -f /var/data/auditlog.d/INITIALIZING
# Tell the transformer to rebuild from the next dump, which the ldap entrypoint writes at start.
touch /var/data/.reset
chown "$SAMUSERID:$SAMGROUPID" /var/data/.reset
echo "ldap-seed: loaded $(slapcat -F /etc/ldap/slapd.d -n "${MAIN_DBNUM:-1}" | grep -c '^dn:') entries from $f"
