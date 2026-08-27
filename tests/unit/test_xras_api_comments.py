"""The approver's note resolver — fail-open, keyed on the POST's ``actionId``."""
import logging
from types import SimpleNamespace

import pytest

from sam.integration.xras_api import (
    XrasApiNotConfigured, XrasSourceUnavailable, approver_comment,
    approver_comment_for_action,
)
from sam.integration.xras_api.comments import find_action, normalize_comment

pytestmark = pytest.mark.unit


def _family():
    """A New line and a later Extension line, as ``reports/request_numbers`` returns."""
    return [
        {'requestNumber': 'UABC0001', 'requestId': 1,
         'actions': [{'actionId': 100, 'actionType': 'New',
                      'adminComments': 'full award recommended'}]},
        {'requestNumber': 'UABC0001', 'requestId': 2,
         'actions': [{'actionId': 200, 'actionType': 'Extension',
                      'adminComments': '  Your extension is approved.\r\n\r\n\r\n\r\n'
                                       'Please verify your balance.  '},
                     {'actionId': 201, 'actionType': 'Supplement',
                      'adminComments': None}]},
    ]


class FakeClient:
    def __init__(self, family=None, raises=None):
        self.family = family if family is not None else _family()
        self.raises = raises
        self.calls = []

    def get_request_family_by_number(self, projcode):
        self.calls.append(projcode)
        if self.raises:
            raise self.raises
        return self.family


class TestNormalize:

    def test_crlf_and_blank_runs_collapse_and_ends_trim(self):
        assert normalize_comment('  a\r\n\r\n\r\n\r\nb  ') == 'a\n\nb'

    @pytest.mark.parametrize('raw', [None, '', '   \r\n  ', 7, []])
    def test_blank_or_non_text_is_none(self, raw):
        assert normalize_comment(raw) is None


class TestFindAction:

    def test_it_searches_every_line_of_the_family(self):
        assert find_action(_family(), 200)['actionType'] == 'Extension'

    def test_the_id_matches_across_int_and_str(self):
        assert find_action(_family(), '100')['actionType'] == 'New'

    def test_a_missing_id_is_none(self):
        assert find_action(_family(), 999) is None

    def test_junk_lines_are_skipped(self):
        assert find_action([None, 'x', {'actions': 'nope'}], 100) is None


class TestApproverComment:

    def test_the_matching_action_supplies_the_note(self):
        client = FakeClient()
        note = approver_comment(client, 'UABC0001', 200)
        assert note == 'Your extension is approved.\n\nPlease verify your balance.'
        assert client.calls == ['UABC0001']

    def test_a_blank_note_is_none(self):
        assert approver_comment(FakeClient(), 'UABC0001', 201) is None

    def test_an_unknown_action_is_none_with_one_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger='sam.integration.xras_api.comments'):
            assert approver_comment(FakeClient(), 'UABC0001', 999) is None
        assert sum('not on the UABC0001 family' in r.getMessage()
                   for r in caplog.records) == 1

    def test_xras_down_is_none_not_an_exception(self, caplog):
        client = FakeClient(raises=XrasSourceUnavailable('timeout'))
        with caplog.at_level(logging.WARNING, logger='sam.integration.xras_api.comments'):
            assert approver_comment(client, 'UABC0001', 200) is None
        assert any('unavailable' in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize('projcode, action_id', [(None, 200), ('', 200),
                                                     ('UABC0001', None)])
    def test_nothing_to_ask_with_asks_nothing(self, projcode, action_id):
        client = FakeClient()
        assert approver_comment(client, projcode, action_id) is None
        assert client.calls == []


class TestForAction:
    """The ``XrasActionLog``-shaped entry point the two notice callers use."""

    def test_a_new_uses_the_minted_projcode(self):
        client = FakeClient()
        action = SimpleNamespace(projcode_result='UABC0001',
                                 request_number='NCAR4999', action_id=100)
        assert approver_comment_for_action(action, client=client) == \
            'full award recommended'
        assert client.calls == ['UABC0001']

    def test_a_later_action_uses_the_request_number(self):
        client = FakeClient()
        action = SimpleNamespace(projcode_result=None,
                                 request_number='UABC0001', action_id=200)
        assert approver_comment_for_action(action, client=client)
        assert client.calls == ['UABC0001']

    def test_no_action_is_none(self):
        assert approver_comment_for_action(None) is None

    def test_an_unconfigured_client_is_none_without_a_call(self, monkeypatch):
        """The test config pins ``XRAS_API_KEY=''`` — the real ``from_environment``
        refuses, and that refusal is the ordinary case in every dev container."""
        from sam.integration.xras_api import client as client_mod
        calls = []

        def refuse(*a, **k):
            calls.append(1)
            raise XrasApiNotConfigured('off')
        monkeypatch.setattr(client_mod.XrasApiClient, 'from_environment', refuse)
        action = SimpleNamespace(projcode_result='UABC0001', request_number=None,
                                 action_id=100)
        assert approver_comment_for_action(action) is None
        assert calls == [1]

    def test_a_bug_on_this_path_is_logged_and_swallowed(self, caplog):
        """The note decorates a notice; a defect here must never withhold mail."""
        class Broken:
            def get_request_family_by_number(self, projcode):
                raise TypeError('boom')
        action = SimpleNamespace(projcode_result='UABC0001', request_number=None,
                                 action_id=100)
        with caplog.at_level(logging.ERROR, logger='sam.integration.xras_api.comments'):
            assert approver_comment_for_action(action, client=Broken()) is None
        assert any('sending without it' in r.getMessage() for r in caplog.records)
