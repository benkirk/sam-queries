"""The request types' cards: guided types, open or not, by audience (XRAS_SUBMISSION_SPRINT3.md)."""

import secrets

import pytest

from sam.accounting.allocation_type_guide import AllocationTypeGuide
from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.integration.xras_api.rule_book import RuleBook
from sam.xras.allocation_overview import allocation_type_overview
from sam.xras.audiences import VISITOR_AUDIENCES


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


def _cards(session, opps, ids, audiences=VISITOR_AUDIENCES, **kw):
    rules = RuleBook(required=('title', 'abstract', 'grants'))
    cards = allocation_type_overview(session, opps, rules_for=lambda *_: rules,
                                     audiences=audiences, **kw)
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

    @pytest.fixture
    def three(self, session, type_id):
        _guide(session, type_id, title='Everyone')
        _guide(session, type_id + 1, audience='ncar')
        _guide(session, type_id + 2, audience='university')
        _guide(session, type_id + 3, title='Retired').update(active=False)
        return {type_id + n for n in range(5)}                       # + 4 has no guide

    def _opps(self, ids):
        return [_opp(i) for i in sorted(ids)]

    def test_a_visitor_sees_every_audience_but_staff(self, session, type_id, three):
        cards = _cards(session, self._opps(three), three)
        assert set(cards) == {type_id, type_id + 2}

    def test_staff_see_their_types_and_everyones(self, session, type_id, three):
        cards = _cards(session, self._opps(three), three, audiences={'ncar'})
        assert set(cards) == {type_id, type_id + 1}
        assert cards[type_id + 1].audience == 'ncar' and cards[type_id + 1].published

    def test_a_user_in_two_audiences_sees_both(self, session, type_id, three):
        cards = _cards(session, self._opps(three), three, audiences={'ncar', 'university'})
        assert set(cards) == {type_id, type_id + 1, type_id + 2}

    def test_none_is_every_audience(self, session, type_id, three):
        assert set(_cards(session, self._opps(three), three, audiences=None)) == {
            type_id, type_id + 1, type_id + 2}

    def test_the_admin_view_adds_the_retired_row_last(self, session, type_id, three):
        cards = _cards(session, self._opps(three), three, audiences={'ncar'},
                       include_unguided=True)
        assert set(cards) == {type_id, type_id + 1, type_id + 3}
        assert not cards[type_id + 3].published and cards[type_id + 3].guide is not None


class TestTheUnguidedTypes:

    @pytest.fixture
    def limited(self, session):
        """An active SAM type with a retired limit and no guide row naming it."""
        from tests.factories import make_allocation_type, make_resource
        sam_type = make_allocation_type(session)
        limit = AllocationTypeResourceLimit.create(
            session, allocation_type_id=sam_type.allocation_type_id,
            resource_id=make_resource(session).resource_id, created_by='t', max_amount=500)
        limit.update(active=False)
        return sam_type, limit

    def _named(self, session, name, **kw):
        cards = allocation_type_overview(session, [], rules_for=lambda *_: None, **kw)
        return [c for c in cards if c.allocation_type == name]

    def test_a_type_with_limits_and_no_guide_is_a_card_for_the_admin(self, session, limited):
        sam_type, limit = limited
        (card,) = self._named(session, sam_type.allocation_type, audiences=None,
                              include_unguided=True)
        assert card.guide is None and not card.published and card.title == sam_type.allocation_type
        assert card.anchor.startswith('type-sam-') and card.reason == 'Not on the request page.'
        assert [r['limit'] for r in card.limits] == [limit]       # the retired row, to edit back
        assert card.resources == ()                               # it bounds nothing

    def test_the_visitor_never_sees_it(self, session, limited):
        assert self._named(session, limited[0].allocation_type, audiences=VISITOR_AUDIENCES) == []

    def test_a_guide_naming_the_type_takes_its_place(self, session, type_id, limited):
        sam_type, _ = limited
        _guide(session, type_id, allocation_type=sam_type.allocation_type)
        (card,) = self._named(session, sam_type.allocation_type, audiences=None,
                              include_unguided=True)
        assert card.guide is not None and card.xras_allocation_type_id == type_id
