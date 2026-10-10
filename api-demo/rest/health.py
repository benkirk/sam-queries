#!/usr/bin/env python3
"""Health: is SAM up, and can it reach its databases? No API key needed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

example('Liveness: the web process answers')
live = get('/api/v1/health/live')
assert live['status'] == 'alive', live
show(live)

example('Readiness: each database it depends on, with latency')
ready = get('/api/v1/health/ready')
show({name: {'status': c['status'], 'latency_ms': c.get('latency_ms')}
      for name, c in ready['checks'].items()})
