"""`row_blockers` — the per-row blocker classifier behind the Remediations Blocker facet."""

from sam.queries.xras_requests import BLOCKER_LABELS, row_blockers
from sam.xras.errors import (MNEMONIC_EXTERNAL_PREFIX, MNEMONIC_INTERNAL_PREFIX,
                             no_affiliation_for_pi, no_current_affiliation_for_pi)


def _action(*, messages=(), unresolved_grants=None):
    return {'preflight': {'messages': list(messages),
                          'resolved': {'unresolved_grants': unresolved_grants}}}


def test_empty_row_has_no_blockers():
    assert row_blockers({}) == set()
    assert row_blockers({'actions': [], 'has_stuck_placeholder': False}) == set()


def test_mnemonic_internal_prefix_is_a_mnemonic_blocker():
    row = {'actions': [_action(messages=[MNEMONIC_INTERNAL_PREFIX + ': ACME'])]}
    assert row_blockers(row) == {'mnemonic'}


def test_mnemonic_external_prefix_is_a_mnemonic_blocker():
    row = {'actions': [_action(messages=[MNEMONIC_EXTERNAL_PREFIX + ': Some U'])]}
    assert row_blockers(row) == {'mnemonic'}


def test_an_unrelated_422_is_not_a_mnemonic_blocker():
    row = {'actions': [_action(messages=['some other validation failure'])]}
    assert row_blockers(row) == set()


def test_no_current_affiliation_is_a_mnemonic_blocker():
    """akeesee/NCAR4287: no affiliation carries no MNEMONIC prefix, but a code
    override is exactly its fix — so the picker must be reachable."""
    row = {'actions': [_action(messages=[no_current_affiliation_for_pi('akeesee')])]}
    assert row_blockers(row) == {'mnemonic'}


def test_no_affiliation_data_is_a_mnemonic_blocker():
    row = {'actions': [_action(messages=[no_affiliation_for_pi('someone')])]}
    assert row_blockers(row) == {'mnemonic'}


def test_unresolved_grants_is_a_contract_blocker():
    row = {'actions': [_action(unresolved_grants=[{'number': 'X-1'}])]}
    assert row_blockers(row) == {'contract'}


def test_empty_unresolved_grants_is_not_a_contract_blocker():
    assert row_blockers({'actions': [_action(unresolved_grants=[])]}) == set()
    assert row_blockers({'actions': [_action(unresolved_grants=None)]}) == set()


def test_stuck_placeholder_is_an_account_blocker():
    assert row_blockers({'has_stuck_placeholder': True}) == {'account'}


def test_a_row_can_carry_every_blocker_at_once():
    row = {'has_stuck_placeholder': True,
           'actions': [_action(messages=[MNEMONIC_INTERNAL_PREFIX + ': ACME'],
                               unresolved_grants=[{'number': 'X-1'}])]}
    assert row_blockers(row) == {'mnemonic', 'contract', 'account'}


def test_blockers_union_across_multiple_actions():
    row = {'actions': [_action(messages=[MNEMONIC_INTERNAL_PREFIX + ': ACME']),
                       _action(unresolved_grants=[{'number': 'X-1'}])]}
    assert row_blockers(row) == {'mnemonic', 'contract'}


def test_missing_preflight_cell_is_survivable():
    row = {'actions': [{}, {'preflight': None}]}
    assert row_blockers(row) == set()


def test_blocker_labels_cover_exactly_the_three_categories():
    assert {slug for slug, _ in BLOCKER_LABELS} == {'mnemonic', 'contract', 'account'}
