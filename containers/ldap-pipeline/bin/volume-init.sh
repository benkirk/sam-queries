#!/bin/sh
# Lay out /var/data as sam-app's data dir (its reset.sh), as root in the ldap image.
# RESET=1 also empties the replica, audit log and spool, which forces a full re-sync.
set -eu
cd /var/data
mkdir -p auditlog.d ldap slapd.d syncd
if [ "${RESET:-0}" = 1 ]; then
    rm -rf auditlog.d/* ldap/* slapd.d/* syncd/*
    rm -f log-*syncd.*
fi
chown "$SAMUSERID:$SAMGROUPID" . syncd
chown openldap auditlog.d ldap slapd.d
chgrp "$SAMGROUPID" auditlog.d
chmod 775 auditlog.d
touch .reset
chown "$SAMUSERID:$SAMGROUPID" .reset
ls -la /var/data
