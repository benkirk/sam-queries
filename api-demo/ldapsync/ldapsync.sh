#!/usr/bin/env bash
# LDAP sync: the feed the identity pipeline (sam-ldap-syncd) reads, at legacy
# SAM's paths. Needs a key carrying the legacy ROLE_API_ADMIN role; set
# SAM_LDAPSYNC_USER / SAM_LDAPSYNC_PASS if that is not your default key.
# Read-only here: the PUT side is the sync daemon's alone.
source "$(dirname "$0")/../lib/connect.sh"
KEY="${SAM_LDAPSYNC_USER:-$SAM_API_USER}:${SAM_LDAPSYNC_PASS:-$SAM_API_PASS}"
LDAPSYNC="$SAM_API_BASE/api/protected/admin/ldapsync"

example "When each table last changed (milliseconds since the epoch) and the access branches"
curl -sS --fail-with-body -u "$KEY" "$LDAPSYNC/status" | jq -e '.'

example "Institutions and organizations: how many, and one of each"
curl -sS --fail-with-body -u "$KEY" "$LDAPSYNC/institution" \
  | jq -e '{institutions: length, first: (.[0] | {institutionId, name, acronym})}'
curl -sS --fail-with-body -u "$KEY" "$LDAPSYNC/organization" \
  | jq -e '{organizations: length, first: (.[0] | {organizationId, name, acronym})}'

example "Group tags"
curl -sS --fail-with-body -u "$KEY" "$LDAPSYNC/groupTag" | jq -e '.'

# One user is looked up by unix uid, read here from the v1 users API.
USERNAME=${SAM_DEMO_USERNAME:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].lead_username')}
UID_NUMBER=$(curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
                  "$SAM_API_BASE/api/v1/users/$USERNAME" | jq -er '.unix_uid')

example "One user by uid ($USERNAME, $UID_NUMBER): the field names the sync reads"
curl -sS --fail-with-body -u "$KEY" "$LDAPSYNC/user/$UID_NUMBER" | jq -e 'keys'
