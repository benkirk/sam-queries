#!/usr/bin/env bash
# Users: list, search, one user's record and the projects they belong to.
source "$(dirname "$0")/../lib/connect.sh"

example "The first page of active users (per_page caps at 100)"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/users/?active=true&per_page=5" \
  | jq -e '{total, usernames: [.users[].username]}'

example "Type-ahead search by username or name"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/users/search?q=ben&limit=5" \
  | jq '[.[].username]'

# The user to look at: SAM_DEMO_USERNAME, else the lead of the first active project.
USERNAME=${SAM_DEMO_USERNAME:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].lead_username')}

example "One user: $USERNAME"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/users/$USERNAME" \
  | jq -e '{username, display_name, active, locked, unix_uid,
            institutions: [.institutions[].institution_acronym]}'

example "The projects $USERNAME leads, administers or belongs to"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/users/$USERNAME/projects" \
  | jq -e '{total_projects,
            led: [.led_projects[].projcode],
            admin: [.admin_projects[].projcode],
            member: [.member_projects[].projcode]}'
