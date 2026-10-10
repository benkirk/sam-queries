#!/usr/bin/env python3
"""Directory Access: the unix accounts and groups SAM provisions on each access
branch (hpc, hpc-data, hpc-dev). LDAP and provisioning tooling read this.
Responses are cached for 5 minutes; POST .../refresh clears that cache."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import example, get, post, show

example('Every access branch, with how many accounts and groups it carries')
branches = get('/api/v1/directory_access/')['accessBranchDirectories']
show([{'branch': b['accessBranchName'], 'accounts': len(b['unixAccounts']),
       'groups': len(b['unixGroups'])} for b in branches])

example('One branch (hpc): the first three accounts')
hpc = get('/api/v1/directory_access/hpc')['accessBranchDirectories']
show([{k: a[k] for k in ('username', 'uid', 'gid', 'homeDirectory')}
      for b in hpc for a in b['unixAccounts'][:3]])

example('Refresh: drop the cached response so the next read is rebuilt')
show(post('/api/v1/directory_access/refresh'))
