#!/usr/bin/env python3
"""Project Access: per access branch, each project's unix group, its status and
when its access expires. Cached for 5 minutes; POST .../refresh clears it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

example('Project groups per branch')
show({branch: len(groups) for branch, groups in get('/api/v1/project_access/').items()})

example('One branch (hpc): the first three project groups')
show([{'groupName': g['groupName'], 'status': g['status'], 'expiration': g['expiration'],
       'resources': [r['resourceName'] for r in g['resourceGroupStatuses']]}
      for g in get('/api/v1/project_access/hpc')['hpc'][:3]])

example('Refresh')
show(post('/api/v1/project_access/refresh'))
