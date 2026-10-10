#!/usr/bin/env python3
"""Charges: what a project has used, rolled up per resource, and the detailed
daily charge records behind it."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

projcode = (os.environ.get('SAM_DEMO_PROJCODE')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['projcode'])

example(f'{projcode}: allocated vs used per resource, for the current allocation')
summary = get(f'/api/v1/projects/{projcode}/charges/summary')
show({name: {k: r[k] for k in ('allocated', 'used', 'percent_used', 'charges_by_type')}
      for name, r in summary['resources'].items()})

example(f'{projcode}: daily charge records over the last 30 days, counted by type')
start = (date.today() - timedelta(days=30)).isoformat()
charges = get(f'/api/v1/projects/{projcode}/charges', start_date=start)
show({'start_date': charges['start_date'], 'end_date': charges['end_date'],
      'records': {kind: len(rows) for kind, rows in charges['charges'].items()}})
