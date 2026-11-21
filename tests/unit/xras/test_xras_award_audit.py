"""`sam.queries.xras_award_audit` and `sam-admin xras --award-audit`: XRAS awarded vs SAM allocated."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from factories import (make_account, make_allocation, make_project, make_xras_action,
                       make_xras_key_mapping)

from cli.cmds.admin import cli
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from sam.queries.xras_award_audit import (PAGE_SIZE, audit_awards, fetch_award_lines,
                                          fold_awards)

TODAY = date(2026, 11, 19)
RID = 7


def _action(action_id, action_type, resources=(), begin=None, end=None, status='Approved'):
    dates = [{'allocationDateType': 'Approved', 'beginDate': begin, 'endDate': end}] \
        if begin or end else []
    return {'actionId': action_id, 'actionType': action_type, 'actionStatus': status,
            'allocationDates': dates,
            'resources': [{'resourceId': rid, 'resourceName': f'Res{rid}', 'type': stage,
                           'amount': amount, 'resourceUnits': 'Core-hours'}
                          for rid, amount, stage in resources]}


def _line(number, *actions, request_id=1):
    return {'requestId': request_id, 'requestNumber': number, 'actions': list(actions)}


class TestFold:
    def test_supplement_adds_an_increment_and_extension_moves_the_end(self):
        awards = fold_awards([_line('UXYZ0001',
            _action(1, 'New', [(RID, 1000.0, 'Approved'), (RID, 5000.0, 'Requested')],
                    '2026-01-01', '2026-12-31'),
            _action(2, 'Supplement', [(RID, 250.0, 'Approved')]),
            _action(3, 'Extension', end='2027-06-30'),
            _action(4, 'Adjustment', [(RID, -50.0, 'Approved')]),
            _action(5, 'Supplement', [(RID, 9999.0, 'Approved')], status='Submitted'))])
        [award] = awards
        assert award.amount == 1200.0
        assert (award.begin, award.end) == (date(2026, 1, 1), date(2027, 6, 30))
        assert award.action_ids == [1, 2, 3, 4]

    def test_a_resource_requested_but_not_approved_is_not_awarded(self):
        awards = fold_awards([_line('UXYZ0001',
            _action(1, 'New', [(RID, 1000.0, 'Approved')], '2026-01-01', '2026-12-31'),
            _action(2, 'Supplement', [(RID, 500.0, 'Approved'), (RID, 5000.0, 'Requested'),
                                      (8, 10.0, 'Requested')]))])
        assert [(a.resource_id, a.amount) for a in awards] == [(RID, 1500.0)]

    def test_a_disjoint_renewal_opens_a_new_period(self):
        [award] = fold_awards([
            _line('UXYZ0001', _action(1, 'New', [(RID, 1000.0, 'Approved')],
                                      '2025-01-01', '2025-12-31')),
            _line('UXYZ0001', _action(9, 'Renewal', [(RID, 300.0, 'Approved')],
                                      '2026-01-01', '2026-12-31'), request_id=2)])
        assert award.amount == 300.0 and award.begin == date(2026, 1, 1)

    def test_an_overlapping_renewal_extends_and_adds_as_update_does(self):
        [award] = fold_awards([_line('UXYZ0001',
            _action(1, 'New', [(RID, 1000.0, 'Approved')], '2026-01-01', '2026-12-31'),
            _action(2, 'Renewal', [(RID, 300.0, 'Approved')], '2026-10-01', '2027-09-30'))])
        assert award.amount == 1300.0
        assert (award.begin, award.end) == (date(2026, 1, 1), date(2027, 9, 30))

    def test_a_renewal_inside_the_held_window_adds_and_keeps_the_begin(self):
        [award] = fold_awards([
            _line('UXYZ0001', _action(1, 'New', [(RID, 500.0, 'Approved')],
                                      '2024-08-19', '2029-06-30')),
            _line('UXYZ0001', _action(9, 'Renewal', [(RID, 2930.0, 'Approved')],
                                      '2025-05-01', '2029-06-30'), request_id=2)])
        assert award.amount == 3430.0
        assert (award.begin, award.end) == (date(2024, 8, 19), date(2029, 6, 30))

    def test_an_overlapping_renewal_ending_earlier_is_refused(self):
        [award] = fold_awards([_line('UXYZ0001',
            _action(1, 'New', [(RID, 1000.0, 'Approved')], '2026-01-01', '2027-12-31'),
            _action(2, 'Renewal', [(RID, 300.0, 'Approved')], '2026-06-01', '2027-06-30'))])
        assert (award.amount, award.end, award.action_ids) == (1000.0, date(2027, 12, 31), [1])

    def test_a_contingent_renewal_moves_the_end_but_not_the_amount(self):
        renewal = _action(2, 'Renewal', [(RID, 300.0, 'Approved')], '2026-10-01', '2027-09-30')
        renewal['resources'][0]['comments'] = 'AUTO_DEFAULT_ALLOCATION_TRANSACTION'
        [award] = fold_awards([_line('UXYZ0001', _action(
            1, 'New', [(RID, 1000.0, 'Approved')], '2026-01-01', '2026-12-31'), renewal)])
        assert (award.amount, award.end) == (1000.0, date(2027, 9, 30))

    def test_deleted_lines_and_actions_are_ignored(self):
        deleted = dict(_action(2, 'Supplement', [(RID, 5.0, 'Approved')]), isDeleted=True)
        [award] = fold_awards([_line('UXYZ0001', _action(
            1, 'New', [(RID, 10.0, 'Approved')], '2026-01-01', '2026-12-31'), deleted)])
        assert award.amount == 10.0
        assert fold_awards([dict(_line('UXYZ0001'), isDeleted=True)]) == []


@pytest.fixture
def mapped(session):
    resource = make_xras_key_mapping(session)
    return resource, {RID: resource.xras_key}


def _new(number, amount=1000.0, begin='2026-01-01', end='2026-12-31'):
    return _line(number, _action(1, 'New', [(RID, amount, 'Approved')], begin, end))


def _held(session, project, resource, amount=1000.0, start=datetime(2026, 1, 1),
          end=datetime(2026, 12, 31)):
    return make_allocation(session, account=make_account(session, project=project,
                                                         resource=resource),
                           amount=amount, start_date=start, end_date=end)


def _audit(session, lines, keys, **kw):
    return audit_awards(session, lines, keys, today=TODAY, **kw)


class TestAudit:
    def test_a_matching_allocation_has_no_issues(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        alloc = _held(session, project, resource)
        audit = _audit(session, [_new(project.projcode)], keys)
        [row] = audit.rows
        assert row.issues == [] and row.allocation_id == alloc.allocation_id
        assert audit.mismatches == []

    def test_amount_and_end_differences(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        _held(session, project, resource, amount=800.0, end=datetime(2026, 11, 30))
        [row] = _audit(session, [_new(project.projcode)], keys).rows
        assert row.issues == ['amount_differs', 'end_differs']
        assert (row.xras_amount, row.sam_amount) == (1000.0, 800.0)

    def test_missing_project_and_missing_allocation(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        rows = _audit(session, [_new('ZZZZ9999'), _new(project.projcode)], keys).rows
        assert {r.projcode: r.issues for r in rows} == {
            'ZZZZ9999': ['project_missing'], project.projcode: ['allocation_missing']}

    def test_a_push_landed_under_another_projcode_is_audited_there(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        _held(session, project, resource)
        make_xras_action(session, status='processed', action_type='New',
                         request_number='NCAR9999', projcode_result=project.projcode)
        [row] = _audit(session, [_new('NCAR9999')], keys).rows
        assert row.issues == ['renamed_pending'] and row.sam_projcode == project.projcode

    def test_an_unmapped_resource_and_an_inactive_project(self, session, mapped):
        _, keys = mapped
        project = make_project(session, active=False)
        line = _line(project.projcode, _action(1, 'New', [(999, 5.0, 'Approved')],
                                               '2026-01-01', '2026-12-31'))
        [row] = _audit(session, [line], keys).rows
        assert row.issues == ['project_inactive', 'resource_unmapped']

    def test_expired_awards_are_out_of_scope_unless_asked(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        old = _new(project.projcode, begin='2024-01-01', end='2024-12-31')
        assert _audit(session, [old], keys).rows == []
        [row] = _audit(session, [old], keys, include_expired=True).rows
        assert row.issues == ['allocation_missing']

    def test_a_live_sam_allocation_xras_never_awarded_is_sam_only(self, session, mapped):
        resource, keys = mapped
        other = make_xras_key_mapping(session)
        project = make_project(session)
        _held(session, project, resource)
        now = datetime.now()
        _held(session, project, other, start=now - timedelta(days=1),
              end=now + timedelta(days=30))
        audit = audit_awards(session, [_new(project.projcode, begin=None, end=None)], keys)
        kinds = sorted(k for r in audit.rows for k in r.issues)
        assert kinds == ['sam_only']
        assert audit.mismatches == []

    def test_the_envelope(self, session, mapped):
        resource, keys = mapped
        project = make_project(session)
        _held(session, project, resource, amount=1.0)
        payload = _audit(session, [_new(project.projcode)], keys).to_dict()
        assert payload['kind'] == 'xras_award_audit'
        assert payload['as_of'] == '2026-11-19'
        assert payload['mismatched'] == 1 and payload['counts']['amount_differs'] == 1
        assert payload['rows'][0]['xras_begin'] == '2026-01-01'
        json.dumps(payload)


class TestFetch:
    def test_one_projcode_reads_its_family(self):
        client = MagicMock()
        client.get_request_family_by_number.return_value = [_new('UXYZ0001')]
        assert fetch_award_lines(client, projcode='UXYZ0001') == ([_new('UXYZ0001')], True)

    def test_a_spent_page_budget_is_incomplete(self):
        client = MagicMock()
        client.iter_request_pages.return_value = iter([[{'requestId': i} for i in range(PAGE_SIZE)]])
        lines, complete = fetch_award_lines(client, max_pages=1)
        assert len(lines) == PAGE_SIZE and complete is False
        assert client.iter_request_pages.call_args.kwargs['active'] is True

    def test_a_short_last_page_is_complete(self):
        client = MagicMock()
        client.iter_request_pages.return_value = iter([[{'requestId': 1}]])
        assert fetch_award_lines(client, max_pages=1)[1] is True


@pytest.fixture
def cli_session(session):
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.core.context.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


class TestCli:
    def _configure(self, monkeypatch, lines, keys, error=None):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = MagicMock()
        client.get_request_family_by_number.return_value = lines
        client.iter_request_pages.return_value = iter([lines])
        if error is not None:
            client.get_request_family_by_number.side_effect = error
        monkeypatch.setattr('sam.integration.xras_api.XrasApiClient.from_environment',
                            classmethod(lambda cls, *a, **k: client))
        monkeypatch.setattr('sam.integration.xras_api.get_resources',
                            lambda: [{'resourceId': r, 'resourceRepositoryKey': k}
                                     for r, k in keys.items()])

    def test_unconfigured_exits_error(self, cli_session, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        assert CliRunner().invoke(cli, ['xras', '--award-audit']).exit_code == EXIT_ERROR

    def test_an_outage_exits_error(self, cli_session, monkeypatch, mapped):
        from sam.integration.xras_api.base import XrasSourceUnavailable
        self._configure(monkeypatch, [], mapped[1], error=XrasSourceUnavailable('down'))
        result = CliRunner().invoke(cli, ['xras', '--award-audit', '--request', 'UXYZ0001'])
        assert result.exit_code == EXIT_ERROR

    def test_a_mismatch_exits_not_found_with_the_envelope(self, cli_session, monkeypatch,
                                                         mapped):
        resource, keys = mapped
        project = make_project(cli_session)
        _held(cli_session, project, resource, amount=10.0,
              start=datetime(2026, 1, 1), end=datetime(2099, 12, 31))
        self._configure(monkeypatch, [_new(project.projcode, end='2099-12-31')], keys)
        result = CliRunner().invoke(cli, ['--format', 'json', 'xras', '--award-audit',
                                          '--request', project.projcode])
        assert result.exit_code == EXIT_NOT_FOUND
        payload = json.loads(result.stdout)
        assert payload['projcode'] == project.projcode
        assert payload['rows'][0]['issues'] == ['amount_differs']

    def test_a_clean_audit_exits_zero_and_renders(self, cli_session, monkeypatch, mapped):
        resource, keys = mapped
        project = make_project(cli_session)
        _held(cli_session, project, resource, start=datetime(2026, 1, 1),
              end=datetime(2099, 12, 31))
        self._configure(monkeypatch, [_new(project.projcode, end='2099-12-31')], keys)
        result = CliRunner().invoke(cli, ['xras', '--award-audit'])
        assert result.exit_code == EXIT_SUCCESS, result.output
        assert 'match SAM' in result.output
