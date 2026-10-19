"""`row_blockers` and `projcode_series`: per-row derivations behind the Remediations card."""

from sam.queries.xras_requests import (BLOCKER_LABELS, is_person_blocker, projcode_series,
                                      row_blockers)
import pytest

from sam.xras import errors as e
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


def test_no_affiliation_data_is_a_person_blocker():
    """Emitted only when the PI has no SAM user: a mnemonic override clears the line, not the 422."""
    row = {'actions': [_action(messages=[no_affiliation_for_pi('someone')])]}
    assert row_blockers(row) == {'person'}


@pytest.mark.parametrize('message', [
    e.pi_not_in_database('x-user-1'), e.pi_not_active('x'),
    e.manager_not_in_database('x-user-1'), e.manager_not_active('x'),
    e.username_missing('x-user-1'), e.username_inactive('x'),
    e.ambiguous_role('Allocation Manager', ['a', 'b']), e.missing_pi_role(),
    e.no_affiliation_for_pi('x-user-1'),
])
def test_every_roster_person_message_is_a_person_blocker(message):
    assert is_person_blocker(message)


@pytest.mark.parametrize('message', [
    'AreaOfInterest (FOS) id is not in database: 500003',
    MNEMONIC_INTERNAL_PREFIX + ': ACME', no_current_affiliation_for_pi('x'), '', None,
])
def test_other_messages_are_not_person_blockers(message):
    assert not is_person_blocker(message)


def test_production_placeholder_pi_reads_person_not_mnemonic():
    """NCAR4398, 2026-10-14: the card said "mnemonic" and offered the override."""
    messages = [e.pi_not_in_database('aligons-user-d94v0'),
                e.manager_not_in_database('ntraylor-knowles-user-8k67e'),
                e.username_missing('aligons-user-d94v0'),
                e.username_missing('ntraylor-knowles-user-8k67e'),
                e.no_affiliation_for_pi('aligons-user-d94v0')]
    assert row_blockers({'actions': [_action(messages=messages)]}) == {'person'}


def test_inactive_pi_without_affiliation_reads_both():
    """NCAR4391: inactive people, and a PI SAM holds but cannot affiliate."""
    messages = [e.pi_not_active('iraji'),
                e.ambiguous_role('Allocation Manager', ['akintomide', 'iraji']),
                e.username_inactive('akintomide'), e.username_inactive('iraji'),
                no_current_affiliation_for_pi('iraji')]
    assert row_blockers({'actions': [_action(messages=messages)]}) == {'person', 'mnemonic'}


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


def test_blocker_labels_cover_exactly_the_four_categories():
    assert {slug for slug, _ in BLOCKER_LABELS} == {'person', 'mnemonic', 'contract', 'account'}


def _typed(action_id, action_type, series=None):
    resolved = {'series': series} if series else None
    return {'action_id': action_id, 'action_type': action_type,
            'preflight': {'status': 'rechecked', 'resolved': resolved}}


class TestProjcodeSeries:

    def test_empty_row_has_no_series(self):
        assert projcode_series({}) is None
        assert projcode_series({'actions': []}) is None

    def test_the_newest_new_wins(self):
        row = {'actions': [_typed(3, 'New', 'UOLD'), _typed(9, 'New', 'UCSG')]}
        assert projcode_series(row) == 'UCSG'

    def test_only_a_new_mints(self):
        assert projcode_series({'actions': [_typed(9, 'Supplement', 'UCSG')]}) is None

    def test_a_new_without_a_resolved_series_falls_through(self):
        row = {'actions': [_typed(3, 'New', 'UCSG'), _typed(9, 'New'),
                           {'action_id': 10, 'action_type': 'New', 'preflight': None}]}
        assert projcode_series(row) == 'UCSG'
