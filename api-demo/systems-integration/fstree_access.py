#!/usr/bin/env python3
"""FairShare Tree: facilities -> allocation types -> projects -> resources, with
allocation, usage and balance. PBS reads it to set fair-share. Also keyed by
project and by user. Cached for 5 minutes; POST .../refresh clears it."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

example('The tree for one resource (Derecho), counted per facility')
tree = get('/api/v1/fstree_access/Derecho')   # urllib needs 'Derecho%20GPU' spelled out too
show([{'facility': f['name'], 'fairSharePercentage': f['fairSharePercentage'],
       'projects': sum(len(t['projects']) for t in f['allocationTypes'])}
      for f in tree['facilities']])

projcode = (os.environ.get('SAM_DEMO_PROJCODE')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['projcode'])

example(f'One project ({projcode}): its resources, balance and users')
for project in get(f'/api/v1/fstree_access/projects/{projcode}').values():
    show([{'name': r['name'], 'allocationAmount': r['allocationAmount'],
           'adjustedUsage': r['adjustedUsage'], 'balance': r['balance'],
           'users': len(r['users'])} for r in project['resources']])

username = (os.environ.get('SAM_DEMO_USERNAME')
            or get(f'/api/v1/projects/{projcode}')['lead']['username'])

example(f'One user ({username}): the projects and resources they can charge')
users = get(f'/api/v1/fstree_access/users/{username}')
show({user: {p: [r['name'] for r in proj['resources']] for p, proj in entry['projects'].items()}
      for user, entry in users.items()})

example('Refresh')
show(post('/api/v1/fstree_access/refresh'))
