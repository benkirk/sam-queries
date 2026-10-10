#!/usr/bin/env python3
"""Disk quotas: each project group's storage quota and paths, per disk resource.
Cached for 5 minutes; POST .../refresh clears it."""
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

quotas = get('/api/v1/disk_quota/')

example('How many quota entries each disk resource has')
show(Counter(q['resourceName'] for q in quotas))

example('The first three entries')
show([{'projcode': q['projcode'], 'groupName': q['groupName'], 'resourceName': q['resourceName'],
       'quota': q['quota'], 'paths': len(q['paths'])} for q in quotas[:3]])

example('Refresh')
show(post('/api/v1/disk_quota/refresh'))
