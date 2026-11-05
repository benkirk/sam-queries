"""``XrasRequestNote``: the upsert, the rename rekey, clear, and the two lookups."""
from datetime import datetime

import pytest

from sam.integration.xras import (XrasRequestNote, lookup_request_notes,
                                  request_notes_by_number)

from factories import make_request_note


def _count(session, number):
    return session.query(XrasRequestNote).filter_by(request_number=number).count()


class TestSet:

    def test_a_second_run_overwrites_the_one_row(self, session):
        make_request_note(session, request_number='NCAR8101', run_id='r1', decision='approve')
        row = make_request_note(session, request_number='NCAR8101', run_id='r2',
                                decision='reject', admin_comment='second')
        assert _count(session, 'NCAR8101') == 1
        assert (row.run_id, row.decision, row.admin_comment) == ('r2', 'reject', 'second')

    def test_a_cleared_note_comes_back_active(self, session):
        make_request_note(session, request_number='NCAR8102').clear()
        assert lookup_request_notes(session, 'NCAR8102') == []
        make_request_note(session, request_number='NCAR8102')
        assert len(lookup_request_notes(session, 'NCAR8102')) == 1

    def test_an_unknown_decision_is_refused(self, session):
        with pytest.raises(ValueError, match='decision'):
            make_request_note(session, decision='maybe')

    def test_values_and_findings_round_trip(self, session):
        row = make_request_note(session, values={'hours_by_resource': {'Derecho': 1000000}},
                                pi_history='Ana Núñez 🌋 has 1 past request.')
        session.expire(row)
        assert row.values == {'hours_by_resource': {'Derecho': 1000000}}
        assert [f['severity'] for f in row.findings_to_judge] == ['warn']
        assert [f['severity'] for f in row.findings_checked] == ['ok']
        assert row.pi_history.endswith('🌋 has 1 past request.')
        assert row.decision_phrase == 'Needs a person to review'


class TestRekey:

    def test_rename_moves_the_note(self, session):
        make_request_note(session, request_number='NCAR8103')
        moved, collided = XrasRequestNote.rekey(session, old_number='NCAR8103',
                                                new_number='UCUB8103')
        assert (moved, collided) == (['arc-allocation-recommender'], [])
        assert _count(session, 'NCAR8103') == 0 and _count(session, 'UCUB8103') == 1

    def test_a_note_already_on_the_new_number_stays_put(self, session):
        make_request_note(session, request_number='NCAR8104', run_id='old')
        make_request_note(session, request_number='UCUB8104', run_id='new')
        moved, collided = XrasRequestNote.rekey(session, old_number='NCAR8104',
                                                new_number='UCUB8104')
        assert (moved, collided) == ([], ['arc-allocation-recommender'])
        assert lookup_request_notes(session, 'UCUB8104')[0].run_id == 'new'


class TestLookups:

    def test_by_number_is_one_map_of_active_notes(self, session):
        make_request_note(session, request_number='NCAR8105')
        make_request_note(session, request_number='NCAR8106', source='other',
                          generated_at=datetime(2026, 11, 4, 6, 0))
        make_request_note(session, request_number='NCAR8106',
                          generated_at=datetime(2026, 11, 5, 6, 0))
        make_request_note(session, request_number='NCAR8107').clear()
        out = request_notes_by_number(session, ['NCAR8105', 'NCAR8106', 'NCAR8107', None])
        assert set(out) == {'NCAR8105', 'NCAR8106'}
        assert [n.source for n in out['NCAR8106']] == ['arc-allocation-recommender', 'other']

    def test_no_numbers_no_query(self, session):
        assert request_notes_by_number(session, []) == {}
        assert lookup_request_notes(session, None) == []
