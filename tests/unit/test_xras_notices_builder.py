"""The XRAS message builder, unit-tested away from Flask.

`sam.queries.xras_notices` is what the operator's Notify button and the hourly
`xras_notices` task both call, so it is where the dedup key, the subject and
the per-kind payload are decided *once*. Everything here used to live inside
`webapp/dashboards/allocations/blueprint.py` and could only be reached through
a request context; the tests below take a plain session, which is the visible
half of what the extraction bought.

Route-level behavior (the preview modal, the send, the activation event) stays
in `test_xras_notify.py` — it did not move and passes unedited.
"""

import json
from types import SimpleNamespace

import pytest

from sam.queries.xras_notices import action_increments

pytestmark = pytest.mark.unit


class TestSignedIncrements:
    """`action_increments(signed=True)` — the Adjustment mail's only number.

    The allocation now holds the NEW TOTAL and `allocation_transaction`
    records the delta without naming the XRAS action, so this payload read is
    the only place the per-resource change survives. A dropped or flipped sign
    here tells a PI their allocation grew when it shrank, and nothing
    downstream could catch it.
    """

    def _action(self, *, amount, key):
        return SimpleNamespace(raw_payload=json.dumps({
            'resources': [{'resourceRepositoryKey': key,
                           'awardedAmount': str(amount)}]}))

    @pytest.fixture
    def resource_key(self, session):
        """Any real mapping row — the helper resolves the key through it.

        A Layer-1 "any row of this shape" pick, deliberately: the sign logic
        is what is under test, not which resource carries it.
        """
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        row = session.query(XrasResourceRepositoryKeyResource).first()
        if row is None:
            pytest.skip('no xras_resource_repository_key_resource rows')
        return row.resource_repository_key

    def test_a_positive_amount_is_shown_with_an_explicit_plus(
            self, session, resource_key):
        out = action_increments(
            session, self._action(amount=50000.0, key=resource_key),
            signed=True)
        assert out and out[0]['amount'].startswith('+')

    def test_a_negative_amount_keeps_its_minus(self, session, resource_key):
        out = action_increments(
            session, self._action(amount=-100000.0, key=resource_key),
            signed=True)
        assert out and out[0]['amount'].startswith('-')

    def test_the_supplement_path_is_unsigned(self, session, resource_key):
        """A supplement's amounts are increments by construction, and its
        wording already says "Added by this request" — a '+' there would be
        noise, and changing it would move a byte in a shipped template."""
        out = action_increments(
            session, self._action(amount=50000.0, key=resource_key))
        assert out and not out[0]['amount'].startswith('+')

    def test_units_are_computed_on_the_magnitude(self, session, resource_key):
        """`allocation_unit` picks singular/plural from the value; -1 is one
        hour in either direction, so a sign must not reach it."""
        neg = action_increments(
            session, self._action(amount=-2500.0, key=resource_key),
            signed=True)
        pos = action_increments(
            session, self._action(amount=2500.0, key=resource_key),
            signed=True)
        assert neg[0]['units'] == pos[0]['units']

    @pytest.mark.parametrize('payload', [
        None, '', 'not json at all', '{"resources": []}',
        '{"resources": [{"awardedAmount": "5"}]}',              # no key
    ])
    def test_anything_unparseable_yields_nothing_rather_than_a_guess(
            self, session, payload):
        action = SimpleNamespace(raw_payload=payload)
        assert action_increments(session, action) == []

    def test_no_action_yields_nothing(self, session):
        assert action_increments(session, None) == []
