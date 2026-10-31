"""The request page's overview: guided types only, open or not (XRAS_SUBMISSION_SPRINT2.md § 0)."""

import secrets

import pytest

from sam.accounting.allocation_type_guide import AllocationTypeGuide
from sam.integration.xras_api.rule_book import RuleBook
from sam.xras.allocation_overview import allocation_type_overview


@pytest.fixture
def type_id():
    return 10_000_000 + secrets.randbelow(10_000_000) * 10


def _guide(session, xras_type, **kw):
    kw.setdefault('title', f'Type {xras_type}')
    return AllocationTypeGuide.create(session, xras_allocation_type_id=xras_type,
                                      created_by='t', **kw)


def _opp(type_id, *, can_submit=True, name='Opp', reason=None, description=None):
    return {'opportunity_id': type_id + 7, 'opportunity_name': name,
            'allocation_type_id': type_id, 'allocation_type': 'Probe',
            'description': description, 'award_months': 12, 'can_submit': can_submit,
            'reason': reason, 'resource_ids': [], 'resources': []}


def _cards(session, opps, ids, **kw):
    rules = RuleBook(required=('title', 'abstract', 'grants'))
    cards = allocation_type_overview(session, opps, rules_for=lambda *_: rules, **kw)
    return {c.xras_allocation_type_id: c for c in cards if c.xras_allocation_type_id in ids}


class TestTheCards:

    def test_an_open_type_carries_its_opportunity_and_rule_book(self, session, type_id):
        _guide(session, type_id, summary='Ours')
        card = _cards(session, [_opp(type_id, description='Theirs')], {type_id})[type_id]
        assert card.is_open and card.reason is None
        assert card.summary == 'Ours' and card.rules.wants_grants

    def test_xras_description_stands_in_for_an_empty_summary(self, session, type_id):
        _guide(session, type_id)
        card = _cards(session, [_opp(type_id, description='Theirs')], {type_id})[type_id]
        assert card.summary == 'Theirs'

    def test_a_type_with_no_open_opportunity_still_shows_without_start(self, session, type_id):
        _guide(session, type_id)
        card = _cards(session, [], {type_id})[type_id]
        assert not card.is_open and card.reason == 'Not open for requests now.'
        assert card.rules is None

    def test_xras_refusal_reads_as_the_reason(self, session, type_id):
        _guide(session, type_id)
        card = _cards(session, [_opp(type_id, can_submit=False, reason=['Closed'])],
                      {type_id})[type_id]
        assert not card.is_open and card.reason == 'Closed'

    def test_an_open_opportunity_is_preferred_over_a_closed_one(self, session, type_id):
        _guide(session, type_id)
        opps = [_opp(type_id, can_submit=False, name='A'), _opp(type_id, name='B')]
        assert _cards(session, opps, {type_id})[type_id].opportunity['opportunity_name'] == 'B'


class TestWhoSeesWhat:

    def test_unguided_staff_and_ineligible_types_stay_off(self, session, type_id):
        _guide(session, type_id, title='Shown')
        _guide(session, type_id + 1, audience='ncar')
        _guide(session, type_id + 2, title='Filtered')
        opps = [_opp(type_id + n) for n in range(4)]                 # + 3 has no guide
        ids = {type_id + n for n in range(4)}
        cards = _cards(session, opps, ids, eligible=lambda g: g.title != 'Filtered')
        assert set(cards) == {type_id}
