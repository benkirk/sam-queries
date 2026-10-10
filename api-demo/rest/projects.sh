#!/usr/bin/env bash
# Projects: list, one project's record, its members, its allocations with usage,
# and the expiration views.
source "$(dirname "$0")/../lib/connect.sh"

example "Active projects, first page"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/?active=true&per_page=5" \
  | jq -e '{total, projects: [.projects[] | {projcode, title, lead_username}]}'

# The project to look at: SAM_DEMO_PROJCODE, else the first active project.
PROJCODE=${SAM_DEMO_PROJCODE:-$(
  curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
       "$SAM_API_BASE/api/v1/projects/?active=true&per_page=1" \
    | jq -r '.projects[0].projcode')}

example "One project: $PROJCODE"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/$PROJCODE" \
  | jq -e '{projcode, title, active, panel, lead: .lead.username, admin: .admin.username,
            parent_path: .breadcrumb_path}'

example "Its lead, admin and members"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/$PROJCODE/members" \
  | jq -e '{total_members, lead: .lead.username, first_members: [.members[:10][].username]}'

example "Its allocations, with usage computed per resource"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/$PROJCODE/allocations" \
  | jq '[.allocations[] | {resource: .resource.resource_name, amount, used,
                           percent_used, start_date, end_date}]'

example "Projects with an allocation ending in the next 30 days"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/expiring?days=30" \
  | jq -e '{total, first: [.expiring_projects[:5][] | {projcode, resource_name, days_remaining}]}'

example "Projects whose allocation ended 90-180 days ago"
curl -sS --fail-with-body -u "$SAM_API_USER:$SAM_API_PASS" \
     "$SAM_API_BASE/api/v1/projects/recently_expired?min_days=90&max_days=180" \
  | jq -e '{total, first: [.expired_projects[:5][] | {projcode, resource_name}]}'
