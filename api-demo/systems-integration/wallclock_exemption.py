#!/usr/bin/env python3
"""Wallclock exemptions: users allowed to run past a queue's wallclock limit, per
resource and queue. Cached for 5 minutes; POST .../refresh clears it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

example('Exemptions per resource and queue')
show([{'resource': r['resourceName'],
       'queues': [{'queue': q['queueName'], 'exemptions': len(q['limits'])} for q in r['queues']]}
      for r in get('/api/v1/wallclock_exemption/')['resources']])

example('One resource (Derecho): who is exempt, and to how many hours')
show([{'queue': q['queueName'], 'limits': q['limits']}
      for r in get('/api/v1/wallclock_exemption/Derecho')['resources'] for q in r['queues']])

example('Refresh')
show(post('/api/v1/wallclock_exemption/refresh'))
