"""Push-readiness preflight — synthesis, verdict mapping, and applied-inference.

The synthesizer is a guess about what XRAS puts on the wire; these pin the field
map against hand-built ``reports/requests`` fixtures. The verdict itself is the
real ingest path (``dispatch_action(validate_only=True)``), so a green here means
a green push. Nothing touches the network — ``resource_keys`` and
``opportunities`` are injected. See ``docs/plans/XRAS_PUSH_READINESS.md``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from factories import make_allocation, make_project

from sam.xras.preflight import (Synthesis, iter_candidate_actions, infer_applied,
                                 preflight_action, synthesize_action)

pytestmark = pytest.mark.unit


def _report(*, number='NCAR9001', request_type='New', opportunity_id=5,
            status='Approved', actions=None, roles=None):
    return {
        'requestId': 1446994,
        'requestNumber': number,
        'requestStatus': status,
        'requestType': request_type,
        'opportunityId': opportunity_id,
        'opportunity_name': 'Test Opportunity',
        'title': 'A Title', 'abstract': 'An abstract.',
        'roles': roles if roles is not None else [
            {'person': {'username': 'ghost-user-1'},
             'roles': [{'role': 'PI', 'beginDate': '2026-01-01', 'endDate': None,
                        'isAccountToBeCreated': True}]}],
        'fos': [{'fosNum': '40', 'isPrimary': True}],
        'grants': [],
        'actions': actions if actions is not None else [_new_action()],
    }


def _new_action(**over):
    action = {
        'actionId': 11, 'actionType': 'New', 'actionStatus': 'Approved',
        'entryDate': '2026-08-01', 'isDeleted': False,
        'allocationDates': [{'allocationDateType': 'Approved',
                             'beginDate': '2026-09-01', 'endDate': '2027-08-31'}],
        'resources': [{'resourceId': 7, 'type': 'Approved', 'amount': 500000.0,
                       'comments': None}],
    }
    action.update(over)
    return action


KEYS = {7: 2001}
OPPS = {5: {'allocationType': 'Small'}}


class TestCandidateSelection:

    def test_it_skips_declined_deleted_and_out_of_window(self):
        payload = _report(actions=[
            _new_action(actionId=1, actionStatus='Approved'),
            _new_action(actionId=2, actionStatus='Declined'),
            _new_action(actionId=3, isDeleted=True),
            _new_action(actionId=4, entryDate='2020-01-01'),
        ])
        ids = [a['actionId'] for a in iter_candidate_actions(
            payload, since=date(2026, 1, 1))]
        assert ids == [1]

    def test_no_window_keeps_everything_not_declined(self):
        payload = _report(actions=[_new_action(actionId=1, entryDate='2020-01-01')])
        assert [a['actionId'] for a in iter_candidate_actions(payload, since=None)]


class TestSynthesis:

    def test_the_field_map_produces_the_inbound_shape(self):
        syn = synthesize_action(_report(), _new_action(),
                                resource_keys=KEYS, opportunities=OPPS)
        assert syn.gaps == ()
        assert syn.stage == 'Approved'
        a = syn.action
        assert a['actionType'] == 'New'
        assert a['actionBeginDate'] == '2026-09-01'
        assert a['actionEndDate'] == '2027-08-31'
        assert a['requestNumber'] == 'NCAR9001'
        assert a['allocationType'] == 'Small'          # from the opportunity map
        # resourceId -> resourceRepositoryKey (different id space)
        assert a['resources'][0]['resourceRepositoryKey'] == 2001
        assert a['resources'][0]['awardedAmount'] == '500000.0'
        # nested roles flattened to the inbound flat shape
        assert a['roles'][0]['roleType'] == 'PI'
        assert a['roles'][0]['username'] == 'ghost-user-1'

    def test_stage_falls_back_when_approved_is_absent(self):
        action = _new_action(
            allocationDates=[{'allocationDateType': 'Requested',
                              'beginDate': '2026-09-01', 'endDate': '2027-08-31'}],
            resources=[{'resourceId': 7, 'type': 'Requested', 'amount': 1.0}])
        syn = synthesize_action(_report(actions=[action]), action,
                                resource_keys=KEYS, opportunities=OPPS)
        assert syn.stage == 'Requested'

    def test_missing_dates_is_a_fatal_gap(self):
        action = _new_action(allocationDates=[])
        syn = synthesize_action(_report(actions=[action]), action,
                                resource_keys=KEYS, opportunities=OPPS)
        assert 'no_allocation_dates' in syn.gaps
        assert syn.action is None

    def test_extension_needs_only_end(self):
        # An Extension carries only the new end (begin inherited by the handler);
        # the New-only both-dates gate wrongly stranded these as incomplete.
        action = _new_action(
            actionType='Extension', resources=[],
            allocationDates=[{'allocationDateType': 'Requested',
                              'beginDate': None, 'endDate': '2028-08-31'}])
        syn = synthesize_action(_report(actions=[action]), action,
                                resource_keys=KEYS, opportunities=OPPS)
        assert syn.action is not None
        assert 'no_allocation_dates' not in syn.gaps
        assert syn.action['actionBeginDate'] is None
        assert syn.action['actionEndDate'] == '2028-08-31'

    def test_extension_missing_end_is_still_fatal(self):
        action = _new_action(actionType='Extension', resources=[],
                             allocationDates=[])
        syn = synthesize_action(_report(actions=[action]), action,
                                resource_keys=KEYS, opportunities=OPPS)
        assert 'no_allocation_dates' in syn.gaps
        assert syn.action is None

    def test_supplement_needs_no_dates(self):
        # A Supplement inherits both dates from the existing allocation.
        action = _new_action(actionType='Supplement', allocationDates=[])
        syn = synthesize_action(_report(actions=[action]), action,
                                resource_keys=KEYS, opportunities=OPPS)
        assert syn.action is not None
        assert 'no_allocation_dates' not in syn.gaps

    def test_an_unmapped_resource_id_is_a_fatal_gap(self):
        syn = synthesize_action(_report(), _new_action(),
                                resource_keys={}, opportunities=OPPS)
        assert 'resource_id_unmapped:7' in syn.gaps
        assert syn.action is None

    def test_it_is_pure_no_network(self):
        # None maps turn every resource/opportunity into a gap without any lookup.
        syn = synthesize_action(_report(), _new_action(),
                                resource_keys=None, opportunities=None)
        assert syn.action is None


class TestVerdictMapping:

    def test_a_date_adjustment_parks_manual(self, session):
        action = _new_action(actionType='Date Adjustment')
        v = preflight_action(session, _report(number='UZZZ0001', actions=[action]),
                             action, resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'manual'
        assert v.would_succeed is False
        assert v.messages and 'service' in v.messages[0]

    def test_a_broken_new_fails_with_the_ordered_list(self, session):
        # A New token with no project routes to add; assembly reports real 422s.
        action = _new_action()
        v = preflight_action(session, _report(number='NCAR9099', actions=[action]),
                             action, resource_keys=KEYS, opportunities=OPPS)
        assert v.status in ('failed', 'incomplete')
        if v.status == 'failed':
            assert v.messages
            assert v.push_state == 'pending'          # New, no project

    def test_a_failed_verdict_carries_what_assembly_resolved(self, session):
        """A contract blocker is a *failed* action, so `resolved` must survive the
        rejection — the contract-blockers report has nothing else to read."""
        action = _new_action()
        report = _report(number='NCAR9098', actions=[action])
        report['grants'] = [{'grantNumber': 'NSF-9990301', 'title': 'Ice',
                             'fundingAgency': 'National Science Foundation'}]
        v = preflight_action(session, report, action,
                             resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'failed'
        [grant] = v.resolved['unresolved_grants']
        assert grant['number'] == 'NSF-9990301' and grant['reason'] == 'missing'
        assert grant['title'] == 'Ice'
        from sam.xras.preflight import verdict_to_dict
        assert verdict_to_dict(v)['resolved']['unresolved_grants'][0]['core'] == '9990301'

    def test_the_call_registers_the_handlers(self, session):
        import sam.xras.dispatch as d
        action = _new_action(actionType='Date Adjustment')
        preflight_action(session, _report(actions=[action]), action,
                         resource_keys=KEYS, opportunities=OPPS)
        assert d.registered_services(), 'preflight must register handlers'

    def _extension(self, projcode, end, status='Submitted'):
        return _new_action(actionType='Extension', actionStatus=status, resources=[],
                           allocationDates=[{'allocationDateType': 'Requested',
                                             'beginDate': None, 'endDate': end}])

    def test_an_extension_would_land(self, session):
        # The exact live shape (UMSU0016): only a new end date, begin inherited.
        # Before the fix this stranded as incomplete; now it dispatches to extend.
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2027, 8, 31))
        action = self._extension(project.projcode, '2028-08-31')
        v = preflight_action(session, _report(number=project.projcode, actions=[action]),
                             action, resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'rechecked'
        assert v.status != 'incomplete'
        assert v.service == 'extend'

    def test_an_extension_that_shrinks_fails_not_incomplete(self, session):
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2027, 8, 31))
        action = self._extension(project.projcode, '2026-01-01')
        v = preflight_action(session, _report(number=project.projcode, actions=[action]),
                             action, resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'failed'
        assert v.messages

    def test_a_transfer_parks_manual_not_incomplete(self, session):
        from factories import make_project
        project = make_project(session)
        action = _new_action(actionType='Transfer', resources=[], allocationDates=[])
        v = preflight_action(session, _report(number=project.projcode, actions=[action]),
                             action, resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'manual'
        assert v.status != 'incomplete'

    def test_an_unsynthesizable_action_is_incomplete_not_green(self, session):
        action = _new_action(allocationDates=[])
        v = preflight_action(session, _report(actions=[action]), action,
                             resource_keys=KEYS, opportunities=OPPS)
        assert v.status == 'incomplete'
        assert v.would_succeed is False
        assert 'no_allocation_dates' in v.gaps


class TestInferApplied:

    def _extension(self, projcode, end):
        action = {'actionId': 22, 'actionType': 'Extension', 'actionStatus': 'Approved',
                  'entryDate': '2026-08-01',
                  'allocationDates': [{'allocationDateType': 'Approved',
                                       'beginDate': '2026-01-01', 'endDate': end}],
                  'resources': []}
        payload = _report(number=projcode, request_type='Renewal', actions=[action])
        syn = synthesize_action(payload, action, resource_keys=KEYS, opportunities=OPPS)
        return syn

    def test_extension_already_at_the_end_is_applied_exact(self, session):
        alloc = make_allocation(session, end_date=datetime(2027, 8, 31))
        projcode = alloc.account.project.projcode
        syn = self._extension(projcode, '2027-08-31')
        applied = infer_applied(session, syn)
        assert applied is not None
        assert applied['heuristic'] is False

    def test_extension_short_of_the_end_is_not_applied(self, session):
        alloc = make_allocation(session, end_date=datetime(2027, 8, 31))
        projcode = alloc.account.project.projcode
        syn = self._extension(projcode, '2028-08-31')      # a later end
        assert infer_applied(session, syn) is None

    def test_a_new_is_never_inferred_applied(self, session):
        syn = synthesize_action(_report(number='NCAR9001'), _new_action(),
                                resource_keys=KEYS, opportunities=OPPS)
        assert infer_applied(session, syn) is None

    def test_log_seen_wins_over_inference(self, session):
        alloc = make_allocation(session, end_date=datetime(2027, 8, 31))
        projcode = alloc.account.project.projcode
        action = {'actionId': 22, 'actionType': 'Extension', 'actionStatus': 'Approved',
                  'entryDate': '2026-08-01',
                  'allocationDates': [{'allocationDateType': 'Approved',
                                       'beginDate': '2026-01-01', 'endDate': '2027-08-31'}],
                  'resources': []}
        payload = _report(number=projcode, request_type='Renewal', actions=[action])
        v = preflight_action(session, payload, action, resource_keys=KEYS,
                             opportunities=OPPS,
                             log_seen={22: {'status': 'processed', 'log_id': 1}})
        assert v.push_state == 'seen_in_log'
        assert v.push_detail['status'] == 'processed'


class TestLogSeenFor:
    """The latest log row per action wins, whatever order the rows come back."""

    @staticmethod
    def _row(session, action_id, status):
        from datetime import datetime

        from sam.integration.xras import XrasActionLog
        row = XrasActionLog(received_time=datetime(2026, 8, 25), remote_actor='XRAS',
                            raw_payload='{}', status=status, action_id=action_id)
        session.add(row)
        session.flush()
        return row

    def test_the_highest_id_wins(self, session):
        from sam.xras.preflight import log_seen_for
        self._row(session, 990392007, 'failed')
        latest = self._row(session, 990392007, 'processed')
        seen = log_seen_for(session, [990392007, None])
        assert seen[990392007]['status'] == 'processed'
        assert seen[990392007]['log_id'] == latest.xras_action_log_id

    def test_a_failed_repost_reports_failed(self, session):
        from sam.xras.preflight import log_seen_for
        self._row(session, 990392008, 'processed')
        self._row(session, 990392008, 'failed')
        assert log_seen_for(session, [990392008])[990392008]['status'] == 'failed'

    def test_nothing_asked_nothing_queried(self, session):
        from sam.xras.preflight import log_seen_for
        assert log_seen_for(session, [None]) == {}
