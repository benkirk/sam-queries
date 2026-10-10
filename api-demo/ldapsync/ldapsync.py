#!/usr/bin/env python3
"""LDAP sync: the feed the identity pipeline (sam-ldap-syncd) reads, at legacy
SAM's paths. Needs a key carrying the legacy ROLE_API_ADMIN role; set
SAM_LDAPSYNC_USER / SAM_LDAPSYNC_PASS if that is not your default key.
Read-only here: the PUT side is the sync daemon's alone."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import credentials, example, get, show

KEY = credentials('LDAPSYNC')
LDAPSYNC = '/api/protected/admin/ldapsync'

example('When each table last changed (milliseconds since the epoch) and the access branches')
show(get(f'{LDAPSYNC}/status', auth=KEY))

example('Institutions and organizations: how many, and one of each')
institutions = get(f'{LDAPSYNC}/institution', auth=KEY)
organizations = get(f'{LDAPSYNC}/organization', auth=KEY)
show({'institutions': len(institutions),
      'first_institution': {k: institutions[0][k] for k in ('institutionId', 'name', 'acronym')},
      'organizations': len(organizations),
      'first_organization': {k: organizations[0][k] for k in ('organizationId', 'name', 'acronym')}})

example('Group tags')
show(get(f'{LDAPSYNC}/groupTag', auth=KEY))

# One user is looked up by unix uid, read here from the v1 users API.
username = (os.environ.get('SAM_DEMO_USERNAME')
            or get('/api/v1/projects/', active='true', per_page=1)['projects'][0]['lead_username'])
uid = get(f'/api/v1/users/{username}')['unix_uid']

example(f'One user by uid ({username}, {uid}): the field names the sync reads')
show(sorted(get(f'{LDAPSYNC}/user/{uid}', auth=KEY)))
