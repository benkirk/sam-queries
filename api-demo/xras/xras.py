#!/usr/bin/env python3
"""XRAS: the endpoints NSF's XRAS allocation system calls in SAM. Needs a key
carrying the legacy ROLE_XRAS role; set SAM_XRAS_USER / SAM_XRAS_PASS if that is
not your default key. Read-only here: XRAS's POSTs create real allocations."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import credentials, example, get, show

KEY = credentials('XRAS')
XRAS = '/api/xras/v1'

username = (os.environ.get('SAM_DEMO_USERNAME')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['lead_username'])

example(f'A person as XRAS sees them ({username}): the field names')
show(sorted(get(f'{XRAS}/people/{username}', auth=KEY)))

example('Their allocation requests, per request number')
masters = get(f'{XRAS}/requests/user/{username}', auth=KEY)['result']['masters']
show([{'requestNumber': m['requestNumber'],
       'requests': [{k: r[k] for k in ('requestType', 'allocationType', 'projectId')}
                    for r in m['requests']]} for m in masters])
