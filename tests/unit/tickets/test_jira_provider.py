"""JiraServiceDeskProvider against a mocked ``session.request`` (the house idiom)."""
import json
from unittest.mock import MagicMock

import pytest
import requests

from sam.integration.tickets import (DEFAULT_AUTOMATION_NOTE, TicketDraft,
                                     TicketNotConfigured, TicketRejected,
                                     TicketSourceUnavailable)
from sam.integration.tickets.jira import (JiraConfig, JiraServiceDeskProvider,
                                          _JiraTransport)

BASE = 'https://ithelp.example.invalid'
DRAFT = TicketDraft(handle='SAM-AR-12', summary="New HPC User Request 'A B' [SAM-AR-12]",
                    body='Name:   A B\nEmail:  a@b.edu', link_url='https://sam/x?request=12')


def _response(status=200, body=None, text=None):
    r = MagicMock(status_code=status, reason='R')
    r.content = b'' if body is None and text is None else b'x'
    r.text = text if text is not None else (json.dumps(body) if body is not None else '')
    if body is None:
        r.json.side_effect = ValueError('no json')
    else:
        r.json.return_value = body
    return r


def _provider(*responses, **config):
    cfg = JiraConfig(enabled=True, write_enabled=True, token='t0k', base_url=BASE,
                     max_retries=config.pop('max_retries', 3), **config)
    provider = JiraServiceDeskProvider(cfg)
    mock = MagicMock(side_effect=list(responses))
    provider.transport.session.request = mock
    return provider, mock


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr('sam.integration.tickets.jira.time.sleep', lambda s: None)


class TestConfig:
    def test_defaults_are_the_ithelp_request_type(self):
        cfg = JiraConfig.from_environment()
        assert (cfg.base_url, cfg.project_key, cfg.service_desk_id, cfg.request_type_id) == (
            'https://ithelp.ucar.edu', 'RC', '3', '20')
        assert cfg.labels == ('sam-account-request',)

    def test_the_summary_never_carries_the_token(self):
        summary = JiraConfig(enabled=True, token='sekrit').summary()
        assert summary['token_set'] is True
        assert 'sekrit' not in json.dumps(summary)

    def test_write_needs_read(self):
        assert not JiraConfig(write_enabled=True, token='t').write_configured
        assert JiraConfig(enabled=True, write_enabled=True, token='t').write_configured

    def test_basic_needs_a_user(self):
        assert not JiraConfig(enabled=True, token='t', auth='basic').configured
        assert JiraConfig(enabled=True, token='t', auth='basic', user='u@x').configured

    def test_interactive_is_one_short_attempt(self):
        cfg = JiraConfig(timeout=10, max_retries=3).interactive()
        assert (cfg.timeout, cfg.max_retries) == (5.0, 1)

    def test_labels_split_on_commas_and_space(self, monkeypatch):
        monkeypatch.setenv('JIRA_LABELS', 'a, b  c')
        assert JiraConfig.from_environment().labels == ('a', 'b', 'c')


class TestAuth:
    def test_bearer_by_default(self):
        transport = _JiraTransport(JiraConfig(token='t0k'))
        assert transport.session.headers['Authorization'] == 'Bearer t0k'
        assert transport.session.auth is None

    def test_basic_for_cloud(self):
        transport = _JiraTransport(JiraConfig(token='t0k', auth='basic', user='me@x'))
        assert 'Authorization' not in transport.session.headers
        assert transport.session.auth == ('me@x', 't0k')


class TestCreate:
    def test_posts_the_pinned_jsm_body_then_labels_then_the_internal_note(self):
        provider, mock = _provider(
            _response(201, {'issueKey': 'RC-9', 'currentStatus': {'status': 'Waiting for support'}}),
            _response(204), _response(201, {'id': '1'}))
        ref = provider.create(DRAFT)
        assert (ref.key, ref.url, ref.status, ref.closed) == (
            'RC-9', f'{BASE}/browse/RC-9', 'Waiting for support', False)

        (m1, u1), k1 = mock.call_args_list[0][0], mock.call_args_list[0][1]
        assert (m1, u1) == ('POST', f'{BASE}/rest/servicedeskapi/request')
        assert k1['json'] == {
            'serviceDeskId': '3', 'requestTypeId': '20',
            'requestFieldValues': {
                'summary': DRAFT.summary,
                'description': ('{noformat}\nName:   A B\nEmail:  a@b.edu\n{noformat}'
                                '\n\nSAM request: https://sam/x?request=12')}}
        assert 'raiseOnBehalfOf' not in k1['json']

        (m2, u2), k2 = mock.call_args_list[1][0], mock.call_args_list[1][1]
        assert (m2, u2) == ('PUT', f'{BASE}/rest/api/2/issue/RC-9')
        assert k2['json'] == {'update': {'labels': [{'add': 'sam-account-request'}]}}

        (m3, u3), k3 = mock.call_args_list[2][0], mock.call_args_list[2][1]
        assert (m3, u3) == ('POST', f'{BASE}/rest/servicedeskapi/request/RC-9/comment')
        assert k3['json'] == {'body': DEFAULT_AUTOMATION_NOTE, 'public': False}

    def test_on_behalf_of_is_sent_only_when_set(self):
        from dataclasses import replace
        provider, mock = _provider(_response(201, {'issueKey': 'RC-9'}),
                                   _response(204), _response(201, {}))
        provider.create(replace(DRAFT, on_behalf_of='pi@uni.edu'))
        assert mock.call_args_list[0][1]['json']['raiseOnBehalfOf'] == 'pi@uni.edu'

    def test_no_labels_means_no_put(self):
        provider, mock = _provider(_response(201, {'issueKey': 'RC-9'}), _response(201, {}),
                                   labels=())
        provider.create(DRAFT)
        assert [c[0][0] for c in mock.call_args_list] == ['POST', 'POST']

    def test_a_failed_label_or_note_never_fails_the_create(self):
        provider, _ = _provider(_response(201, {'issueKey': 'RC-9'}),
                                _response(400, {'errorMessages': ['no']}),
                                _response(500))
        assert provider.create(DRAFT).key == 'RC-9'

    @pytest.mark.parametrize('status,body,match', [
        (400, {'errorMessage': 'requestTypeId is invalid'}, 'requestTypeId is invalid'),
        (401, {}, 'token rejected'),
        (403, {}, 'token rejected'),
    ])
    def test_a_4xx_is_rejected_with_its_status(self, status, body, match):
        provider, _ = _provider(_response(status, body))
        with pytest.raises(TicketRejected, match=match) as caught:
            provider.create(DRAFT)
        assert caught.value.status == status

    def test_a_5xx_is_one_attempt(self):
        provider, mock = _provider(_response(502), _response(201, {'issueKey': 'RC-9'}))
        with pytest.raises(TicketSourceUnavailable):
            provider.create(DRAFT)
        assert mock.call_count == 1, 'a retried create is a duplicate ticket'

    def test_a_timeout_is_one_attempt(self):
        provider, mock = _provider(requests.Timeout('slow'))
        with pytest.raises(TicketSourceUnavailable):
            provider.create(DRAFT)
        assert mock.call_count == 1

    def test_no_issue_key_is_unavailable(self):
        provider, _ = _provider(_response(201, {}))
        with pytest.raises(TicketSourceUnavailable, match='issueKey'):
            provider.create(DRAFT)

    def test_write_lever_off_refuses_before_any_call(self):
        cfg = JiraConfig(enabled=True, write_enabled=False, token='t')
        provider = JiraServiceDeskProvider(cfg, transport=MagicMock())
        with pytest.raises(TicketNotConfigured):
            provider.create(DRAFT)
        provider.transport.send.assert_not_called()

    def test_a_noformat_in_the_body_cannot_close_the_block(self):
        text = JiraServiceDeskProvider.description(TicketDraft('h', 's', 'a {noformat} b'))
        assert text.count('{noformat}') == 2


class TestReads:
    def test_get_maps_the_done_category_to_closed(self):
        provider, mock = _provider(_response(200, {
            'key': 'RC-9', 'fields': {'status': {'name': 'Resolved',
                                                 'statusCategory': {'key': 'done'}}}}))
        ref = provider.get('RC-9')
        assert (ref.status, ref.closed) == ('Resolved', True)
        assert mock.call_args[1]['params'] == {'fields': 'status'}

    def test_get_open(self):
        provider, _ = _provider(_response(200, {
            'key': 'RC-9', 'fields': {'status': {'name': 'In Progress',
                                                 'statusCategory': {'key': 'indeterminate'}}}}))
        assert provider.get('RC-9').closed is False

    def test_get_404_is_none(self):
        provider, _ = _provider(_response(404, {'errorMessages': ['gone']}))
        assert provider.get('RC-9') is None

    def test_get_retries_5xx_then_succeeds(self):
        provider, mock = _provider(_response(503), requests.ConnectionError('x'),
                                   _response(200, {'key': 'RC-9', 'fields': {}}))
        assert provider.get('RC-9').key == 'RC-9'
        assert mock.call_count == 3

    def test_get_gives_up_after_max_retries(self):
        provider, mock = _provider(_response(503), _response(503), max_retries=2)
        with pytest.raises(TicketSourceUnavailable, match='2 attempt'):
            provider.get('RC-9')
        assert mock.call_count == 2

    def test_reads_off_refuse_before_any_call(self):
        provider = JiraServiceDeskProvider(JiraConfig(token='t'), transport=MagicMock())
        with pytest.raises(TicketNotConfigured):
            provider.get('RC-9')
        with pytest.raises(TicketNotConfigured):
            provider.find('SAM-AR-1')
        provider.transport.get.assert_not_called()


def _issue(key, summary, category='new'):
    return {'key': key, 'fields': {'summary': summary,
                                   'status': {'name': 'Waiting', 'statusCategory': {'key': category}}}}


class TestFind:
    def test_the_jql_is_pinned(self):
        provider, mock = _provider(_response(200, {'issues': []}))
        assert provider.find('SAM-AR-12') is None
        params = mock.call_args[1]['params']
        assert params['jql'] == 'project = RC AND summary ~ "\\"SAM-AR-12\\"" ORDER BY created ASC'
        assert mock.call_args[0] == ('GET', f'{BASE}/rest/api/2/search')

    def test_one_hit(self):
        provider, _ = _provider(_response(200, {'issues': [_issue('RC-5', 'x [SAM-AR-12]')]}))
        assert provider.find('SAM-AR-12').key == 'RC-5'

    def test_two_hits_take_the_oldest_and_warn(self, caplog):
        provider, _ = _provider(_response(200, {'issues': [
            _issue('RC-5', 'x [SAM-AR-12]'), _issue('RC-8', 'y [SAM-AR-12]')]}))
        with caplog.at_level('WARNING'):
            assert provider.find('SAM-AR-12').key == 'RC-5'
        assert 'RC-5, RC-8' in caplog.text

    def test_a_longer_id_is_not_a_hit(self):
        provider, _ = _provider(_response(200, {'issues': [_issue('RC-5', 'x [SAM-AR-123]')]}))
        assert provider.find('SAM-AR-12') is None

    def test_a_bad_jql_is_rejected(self):
        provider, _ = _provider(_response(400, {'errorMessages': ['bad jql']}))
        with pytest.raises(TicketRejected, match='bad jql'):
            provider.find('SAM-AR-12')


class TestCommentAndCheck:
    def test_a_public_comment(self):
        provider, mock = _provider(_response(201, {}))
        provider.comment('RC-9', 'hi', internal=False)
        assert mock.call_args[1]['json'] == {'body': 'hi', 'public': True}

    def test_check_names_the_token_owner(self):
        provider, _ = _provider(_response(200, {'name': 'bk', 'displayName': 'B K'}))
        assert provider.check() == (True, 'B K')

    def test_check_reports_a_rejected_token(self):
        provider, _ = _provider(_response(401, {}))
        ok, why = provider.check()
        assert not ok and 'token rejected' in why

    def test_check_unconfigured(self):
        ok, why = JiraServiceDeskProvider(JiraConfig()).check()
        assert not ok and 'not configured' in why
