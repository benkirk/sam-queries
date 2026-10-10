#!/usr/bin/env python3
"""Projects: list, one project's record, its members, its allocations with usage,
and the expiration views."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

example('Active projects, first page')
page = get('/api/v1/projects/', active='true', per_page=5)
show({'total': page['total'],
      'projects': [{k: p[k] for k in ('projcode', 'title', 'lead_username')}
                   for p in page['projects']]})

# The project to look at: SAM_DEMO_PROJCODE, else the first active project.
projcode = (os.environ.get('SAM_DEMO_PROJCODE')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['projcode'])

example(f'One project: {projcode}')
project = get(f'/api/v1/projects/{projcode}')
show({'projcode': project['projcode'], 'title': project['title'],
      'active': project['active'], 'panel': project['panel'],
      'lead': (project['lead'] or {}).get('username'),
      'admin': (project['admin'] or {}).get('username'),
      'parent_path': project['breadcrumb_path']})

example('Its lead, admin and members')
members = get(f'/api/v1/projects/{projcode}/members')
show({'total_members': members['total_members'],
      'first_members': [m['username'] for m in members['members'][:10]]})

example('Its allocations, with usage computed per resource')
allocations = get(f'/api/v1/projects/{projcode}/allocations')['allocations']
show([{'resource': a['resource']['resource_name'], 'amount': a['amount'], 'used': a['used'],
       'percent_used': a['percent_used'], 'end_date': a['end_date']} for a in allocations])

example('Projects with an allocation ending in the next 30 days')
expiring = get('/api/v1/projects/expiring', days=30)
show({'total': expiring['total'],
      'first': [{k: p[k] for k in ('projcode', 'resource_name', 'days_remaining')}
                for p in expiring['expiring_projects'][:5]]})

example('Projects whose allocation ended 90-180 days ago')
expired = get('/api/v1/projects/recently_expired', min_days=90, max_days=180)
show({'total': expired['total'],
      'first': [{k: p[k] for k in ('projcode', 'resource_name')}
                for p in expired['expired_projects'][:5]]})
