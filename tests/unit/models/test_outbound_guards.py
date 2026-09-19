"""The suite's two outbound guards, tested rather than assumed.

An untested guard is worse than none: it reads as protection, and the failure
mode is silent. Both of these exist because the credentials a developer's
`.env` supplies are **production** credentials, and the suite inherits them.

The SMTP half has been in place since the notification framework
(`ndir.ucar.edu` relays for the whole UCAR /16, so any host that can run this
suite can mail anyone). The HTTP half is newer and sharper: the outbound XRAS
key is **write-provisioned**, and a person merge deletes an account with no
undo. Measured 2026-08-21, before the fix: the suite inherited a real 96-char
key and `xras_api_configured()` returned True.
"""

from __future__ import annotations

import os

import pytest
import requests

pytestmark = pytest.mark.unit


class TestTheXrasCredentialsArePinnedFailClosed:
    """Layer one: the suite must not inherit a usable credential at all."""

    def test_no_api_key_reaches_the_suite(self):
        """WARNING: `.env` supplies a real one; `pytest_configure` occupies the name
        first, and `load_dotenv(override=False)` then skips it."""
        assert os.environ.get('XRAS_API_KEY') == ''

    @pytest.mark.parametrize('lever', ['XRAS_OUTGOING_ENABLED',
                                       'XRAS_WRITE_ENABLED'])
    def test_both_levers_are_off(self, lever):
        assert os.environ.get(lever) == '0'

    def test_the_predicates_agree(self):
        from sam.integration.xras_api import (xras_api_configured,
                                              xras_write_configured)
        assert xras_api_configured() is False
        assert xras_write_configured() is False

    def test_a_test_can_still_opt_into_the_configured_path(self, monkeypatch):
        """The pin must not make the configured path untestable — every XRAS
        test drives it with fakes."""
        from sam.integration.xras_api import xras_api_configured
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'not-a-real-key')
        assert xras_api_configured() is True


class TestNoRealOutboundHttp:
    """Layer two: the socket, which no config can re-open.

    Layer one is a value a test can override and a fixture can forget. This one
    is not — which is the whole argument for having both.
    """

    def test_a_remote_host_is_refused(self):
        with pytest.raises(RuntimeError, match='real GET request'):
            requests.get('https://api.xras.org/v1/people/nobody', timeout=1)

    def test_the_message_explains_the_stakes(self):
        """A guard that fires without saying why gets patched around."""
        with pytest.raises(RuntimeError) as caught:
            requests.post('https://api.xras.org/v1/people/a/merge/b', timeout=1)
        message = str(caught.value)
        assert 'write-provisioned' in message
        assert 'no undo' in message
        assert 'monkeypatch' in message, 'it must name the way out'

    def test_it_covers_a_session_not_just_the_module_helpers(self):
        """The clients hold a persistent `requests.Session`."""
        with pytest.raises(RuntimeError):
            requests.Session().request('GET', 'https://api.xras.org/v1/resources',
                                       timeout=1)

    def test_localhost_is_left_alone(self):
        """Blocking a local stub server would be surprising, not protective.

        Asserts the guard lets it through — the connection then fails on its
        own merits, which is a ConnectionError, not the guard's RuntimeError.
        """
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get('http://127.0.0.1:9/nothing-listens-here', timeout=1)

    def test_the_established_mocking_idiom_still_works(self, monkeypatch):
        """WARNING: Load-bearing: every XRAS test patches the transport on the
        *instance*, which shadows the guard on the class. If this broke, the
        guard would have made the suite untestable rather than safe."""
        from unittest.mock import MagicMock

        from sam.integration.xras_api.client import XrasApiClient
        from sam.integration.xras_api.config import XrasApiConfig

        response = MagicMock(status_code=200, text='')
        response.json.return_value = {'message': None, 'result': {'x': 1}}

        client = XrasApiClient(XrasApiConfig(enabled=True, api_key='k'))
        monkeypatch.setattr(client.session, 'request',
                            MagicMock(return_value=response))
        assert client.get_person('somebody') == {'x': 1}


class TestNoRealSmtp:
    """The older sibling, asserted here so both guards have one home."""

    def test_an_smtp_connection_is_refused(self):
        import smtplib
        with pytest.raises(RuntimeError, match='SMTP connection'):
            smtplib.SMTP('mail.example.invalid')

    def test_smtp_ssl_is_refused_too(self):
        import smtplib
        with pytest.raises(RuntimeError, match='SMTP connection'):
            smtplib.SMTP_SSL('mail.example.invalid')
