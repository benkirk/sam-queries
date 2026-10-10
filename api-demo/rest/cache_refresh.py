#!/usr/bin/env python3
"""Cache refresh: the one write in rest/. It clears SAM's server-side caches, all at
once or one category at a time. Safe (caches are derived data), but it is not
read-only: pages run slower until the caches warm again. Needs a key that holds
SYSTEM_ADMIN in production."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import ApiError, example, post, show

example('Clear every cache; the reply names each category and how many entries went')
cleared = post('/api/v1/admin/cache/refresh')['cleared']
show(cleared)

# The categories come from the reply above, so this never drifts from the server.
for category in cleared:
    example(f"Clear only the '{category}' cache")
    show(post('/api/v1/admin/cache/refresh', category=category)['cleared'])

example('An unknown category is a 400 that lists the valid ones')
try:
    post('/api/v1/admin/cache/refresh', category='no-such-cache')
    sys.exit('expected a 400')
except ApiError as err:
    assert err.code == 400, err
    print(err.body)
