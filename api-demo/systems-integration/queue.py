#!/usr/bin/env python3
"""Queues: the batch queues SAM knows for each HPC resource, with their dates and
wallclock limits. Cached for 5 minutes; POST .../refresh clears it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

example('Every resource and its queues')
show([{'resource': r['resourceName'], 'queues': [q['queueName'] for q in r['queues']]}
      for r in get('/api/v1/queue/')['resources']])

example("One resource (Derecho), with each queue's wallclock limit")
show([{k: q[k] for k in ('queueName', 'wallClockHoursLimit', 'startDate', 'endDate')}
      for r in get('/api/v1/queue/Derecho')['resources'] for q in r['queues']])

example('Refresh')
show(post('/api/v1/queue/refresh'))
