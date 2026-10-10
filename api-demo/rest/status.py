#!/usr/bin/env python3
"""System status: the latest snapshot the collectors posted for each system, plus
outages and reservations."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

# A "latest" route answers 404 until a collector has posted for that system.
for system in ('derecho', 'casper', 'jupyterhub'):
    example(f'Latest {system} snapshot')
    latest = get(f'/api/v1/status/{system}/latest', missing_ok=True)
    if latest is None:
        print(f'(no {system} snapshot yet: no collector has posted)')
    else:
        show({k: v for k, v in latest.items() if not isinstance(v, (list, dict))})

example('Current and upcoming outages')
outages = get('/api/v1/status/outages')
show({'outages': len(outages), 'titles': [o.get('title') for o in outages]})

example('Reservations')
show({'reservations': len(get('/api/v1/status/reservations'))})
