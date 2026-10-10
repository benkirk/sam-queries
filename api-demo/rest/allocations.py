#!/usr/bin/env python3
"""Allocations: one allocation by id, with its usage. Ids come from a project's
allocation list."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, show

projcode = (os.environ.get('SAM_DEMO_PROJCODE')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['projcode'])
allocation_id = get(f'/api/v1/projects/{projcode}/allocations')['allocations'][0]['allocation_id']

example(f'Allocation {allocation_id} (the first one on {projcode})')
a = get(f'/api/v1/allocations/{allocation_id}')
show({'allocation_id': a['allocation_id'], 'resource': a['resource']['resource_name'],
      'amount': a['amount'], 'used': a['used'], 'remaining': a['remaining'],
      'percent_used': a['percent_used'], 'charges_by_type': a['charges_by_type'],
      'start_date': a['start_date'], 'end_date': a['end_date'], 'is_active': a['is_active']})
