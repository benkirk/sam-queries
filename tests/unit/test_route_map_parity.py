"""Route-map parity gate for the form-layer OO refactor.

The refactor rewrites how dashboard routes are *registered* (handler
classes, the CRUD registrar) while promising that the registered surface
— every ``(endpoint, rule, methods)`` triple — is untouched, so template
``url_for`` calls and htmx attributes keep working.

This test pins that promise to a checked-in snapshot. A legitimate route
addition/removal regenerates it:

    ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py

then review the snapshot diff in the commit like any other code change.
"""

import json
import os
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parent / 'snapshots' / 'dashboard_route_map.json'

#: Blueprints whose registration mechanics the refactor touches.
DASHBOARD_BLUEPRINTS = (
    'admin_dashboard',
    'allocations_dashboard',
    'project_members',
    'status_dashboard',
    'user_dashboard',
)


def _current_route_map(app):
    rows = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.split('.')[0] in DASHBOARD_BLUEPRINTS:
            methods = sorted(rule.methods - {'HEAD', 'OPTIONS'})
            rows.append([rule.endpoint, rule.rule, methods])
    rows.sort()
    return rows


def test_dashboard_route_map_matches_snapshot(app):
    current = _current_route_map(app)

    if os.environ.get('ROUTE_MAP_REGEN'):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=2) + '\n')
        pytest.skip(f'snapshot regenerated: {len(current)} routes')

    assert SNAPSHOT.exists(), 'snapshot missing — run with ROUTE_MAP_REGEN=1'
    recorded = json.loads(SNAPSHOT.read_text())

    current_map = {(e, r): m for e, r, m in current}
    recorded_map = {(e, r): m for e, r, m in recorded}

    missing = sorted(set(recorded_map) - set(current_map))
    added = sorted(set(current_map) - set(recorded_map))
    changed = sorted(
        key for key in set(current_map) & set(recorded_map)
        if current_map[key] != recorded_map[key]
    )
    assert not (missing or added or changed), (
        'Dashboard route map drifted from snapshot.\n'
        f'  missing: {missing}\n'
        f'  added:   {added}\n'
        f'  methods changed: {changed}\n'
        'If intentional, regenerate with ROUTE_MAP_REGEN=1 and commit the diff.'
    )
