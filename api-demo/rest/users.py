#!/usr/bin/env python3
"""Users: list, search, one user's record and the projects they belong to."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

example('The first page of active users (per_page caps at 100)')
page = get('/api/v1/users/', active='true', per_page=5)
show({'total': page['total'], 'usernames': [u['username'] for u in page['users']]})

example('Type-ahead search by username or name')
show([u['username'] for u in get('/api/v1/users/search', q='ben', limit=5)])

# The user to look at: SAM_DEMO_USERNAME, else the lead of the first active project.
username = (os.environ.get('SAM_DEMO_USERNAME')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['lead_username'])

example(f'One user: {username}')
user = get(f'/api/v1/users/{username}')
summary = {k: user[k] for k in ('username', 'display_name', 'active', 'locked', 'unix_uid')}
summary['institutions'] = [i['institution_acronym'] for i in user['institutions']]
show(summary)

example(f'The projects {username} leads, administers or belongs to')
mine = get(f'/api/v1/users/{username}/projects')
show({'total_projects': mine['total_projects'],
      'led': [p['projcode'] for p in mine['led_projects']],
      'admin': [p['projcode'] for p in mine['admin_projects']],
      'member': [p['projcode'] for p in mine['member_projects']]})
