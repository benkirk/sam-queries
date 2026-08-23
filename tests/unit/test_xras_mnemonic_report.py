"""The XRAS mnemonic unblock report — a pivot over the preflight's failed verdicts.

Ranks the org/institution links that would unblock the most failing pushes, using the SAME
`_best_*` resolution the ingest resolver uses, and confirms each target is still unmapped
against the current DB. See `docs/plans/XRAS_INGEST_IMPROVEMENTS.md` § 2.2.
"""

from __future__ import annotations

import pytest
from factories import (make_institution, make_mnemonic_code, make_organization,
                       make_user, make_user_institution, make_user_organization)

from sam.queries.xras_mnemonic_report import mnemonic_unblock_report
from sam.xras.errors import mnemonic_external_failed, mnemonic_internal_failed

pytestmark = pytest.mark.unit

INTERNAL = mnemonic_internal_failed()
EXTERNAL = mnemonic_external_failed()


def _entry(request_number, pi_username, *, messages, rollup='failed', status='failed'):
    return {
        'request_number': request_number,
        'preflight_rollup': rollup,
        'pi': {'username': pi_username},
        'opportunity_name': 'Some Opportunity',
        'actions': [{'action_id': 1,
                     'preflight': {'status': status, 'messages': list(messages)}}],
    }


def _snapshot(*entries):
    return {'generated_at': '2026-08-23', 'statuses': ['Approved'],
            'extra_statuses': {}, 'rows': list(entries)}


def _pi_with_org(session, org_name, username):
    user = make_user(session, username=username)
    org = make_organization(session, name=org_name)
    make_user_organization(session, user=user, organization=org)
    return user, org


class TestRanking:

    def test_orgs_rank_by_how_many_actions_cite_them(self, session):
        _pi_with_org(session, 'Big Blocker University', 'pi-big')
        _pi_with_org(session, 'Small Blocker Lab', 'pi-small')
        snap = _snapshot(
            _entry('NCAR0001', 'pi-big', messages=[INTERNAL]),
            _entry('NCAR0002', 'pi-big', messages=[INTERNAL]),
            _entry('NCAR0003', 'pi-small', messages=[INTERNAL]),
        )
        report = mnemonic_unblock_report(session, snap)
        assert report['kind'] == 'xras_mnemonic_report'
        assert report['actions_seen'] == 3
        names = [t['name'] for t in report['targets']]
        assert names == ['Big Blocker University', 'Small Blocker Lab']
        assert report['targets'][0]['unblock_count'] == 2
        assert set(report['targets'][0]['sample']) == {'NCAR0001', 'NCAR0002'}

    def test_the_prefill_is_the_org_name(self, session):
        _pi_with_org(session, 'Exact Match Org', 'pi-x')
        report = mnemonic_unblock_report(
            session, _snapshot(_entry('NCAR0001', 'pi-x', messages=[INTERNAL])))
        assert report['targets'][0]['prefill'] == 'Exact Match Org'


class TestBothFamilies:

    def test_an_external_pi_is_bucketed_by_institution(self, session):
        user = make_user(session, username='pi-ext')
        inst = make_institution(session, name='Some University')
        make_user_institution(session, user=user, institution=inst)
        report = mnemonic_unblock_report(
            session, _snapshot(_entry('NCAR0001', 'pi-ext', messages=[EXTERNAL])))
        assert len(report['targets']) == 1
        target = report['targets'][0]
        assert target['family'] == 'institution'
        assert target['name'] == 'Some University'


class TestWhatDropsOut:

    def test_a_target_linked_since_the_sweep_drops_out(self, session):
        _, org = _pi_with_org(session, 'Already Linked Org', 'pi-fixed')
        # A mnemonic whose description equals the org name — resolve_for_organization
        # now finds it, so the failure is stale and must not be reported.
        make_mnemonic_code(session, code='QZZ', description='Already Linked Org')
        report = mnemonic_unblock_report(
            session, _snapshot(_entry('NCAR0001', 'pi-fixed', messages=[INTERNAL])))
        assert report['targets'] == []
        assert report['unresolved'] == []      # mapped, not "no affiliation"

    def test_a_non_mnemonic_failure_is_ignored(self, session):
        _pi_with_org(session, 'Irrelevant Org', 'pi-other')
        report = mnemonic_unblock_report(session, _snapshot(
            _entry('NCAR0001', 'pi-other', messages=['No resource found in SAM'])))
        assert report['actions_seen'] == 0
        assert report['targets'] == []

    def test_a_rechecked_action_contributes_nothing(self, session):
        _pi_with_org(session, 'Green Org', 'pi-green')
        report = mnemonic_unblock_report(session, _snapshot(
            _entry('NCAR0001', 'pi-green', messages=[], rollup='rechecked',
                   status='rechecked')))
        assert report['actions_seen'] == 0


class TestUnresolvedBucket:

    def test_a_pi_with_no_current_affiliation(self, session):
        make_user(session, username='pi-orphan')     # no user_organization row
        report = mnemonic_unblock_report(
            session, _snapshot(_entry('NCAR0001', 'pi-orphan', messages=[INTERNAL])))
        assert report['targets'] == []
        assert len(report['unresolved']) == 1
        assert report['unresolved'][0]['pi'] == 'pi-orphan'

    def test_a_pi_sam_does_not_know(self, session):
        report = mnemonic_unblock_report(
            session, _snapshot(_entry('NCAR0001', 'ghost-user-1', messages=[INTERNAL])))
        assert report['targets'] == []
        assert report['unresolved'][0]['pi'] == 'ghost-user-1'


def test_an_empty_snapshot_is_a_clean_empty_report(session):
    assert mnemonic_unblock_report(session, None)['targets'] == []
    assert mnemonic_unblock_report(session, {'rows': []})['actions_seen'] == 0
