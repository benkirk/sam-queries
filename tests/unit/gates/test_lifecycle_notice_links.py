"""Paths the lifecycle emails link to must be real routes: a rename would
otherwise break links in mail that has already left."""

import json
from pathlib import Path

from sam.queries.lifecycle_notices import ONBOARDING_PATHS, RESOURCE_DETAILS_PATH

SNAPSHOT = Path(__file__).parents[1] / 'snapshots' / 'dashboard_route_map.json'


def test_every_linked_path_is_a_route():
    rules = {rule for _, rule, _ in json.loads(SNAPSHOT.read_text())}
    for path in ONBOARDING_PATHS.values():
        assert f'/{path}' in rules, path
    assert f'/{RESOURCE_DETAILS_PATH}/<projcode>' in rules
