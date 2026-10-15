"""The registry and the ABC: every provider concrete, the pins fail closed,
and a second provider needs only the abstract members."""
import inspect

import pytest

from factories.tickets import MinimalProvider
from sam.integration.tickets import (TicketDraft, TicketNotConfigured,
                                     TicketProvider)
from sam.integration.tickets.registry import (MAIL_NAMES, PROVIDERS,
                                              build_provider, provider_for_stored,
                                              provider_from_environment,
                                              provider_names)

DRAFT = TicketDraft(handle='SAM-AR-1', summary='s [SAM-AR-1]', body='b')


@pytest.mark.parametrize('name,cls', sorted(PROVIDERS.items()))
def test_every_registered_class_is_concrete_and_named_by_its_key(name, cls):
    assert not inspect.isabstract(cls)
    assert cls.name == name
    assert len(name) <= 16, 'notification_log.transport is VARCHAR(16)'


@pytest.mark.parametrize('name', sorted(PROVIDERS))
def test_under_the_suite_pins_nothing_is_configured(name):
    provider = build_provider(name)
    assert provider.configured is False
    assert provider.write_configured is False
    with pytest.raises(TicketNotConfigured):
        provider.create(DRAFT)


@pytest.mark.parametrize('name', sorted(MAIL_NAMES) + ['MAIL', ' none '])
def test_mail_names_mean_no_provider(name):
    assert build_provider(name) is None


def test_the_suite_selects_mail():
    assert provider_from_environment() is None


def test_an_unknown_name_raises_naming_the_valid_ones():
    with pytest.raises(TicketNotConfigured) as caught:
        build_provider('jira-cloud')
    for valid in provider_names():
        assert valid in str(caught.value)
    assert 'mail' in str(caught.value)


def test_the_env_selector_is_read_per_call(monkeypatch):
    monkeypatch.setenv('TICKET_PROVIDER', 'jira-servicedesk')
    assert provider_from_environment().name == 'jira-servicedesk'


def test_a_stored_name_resolves_even_when_mail_is_selected():
    """Links on old rows keep working after TICKET_PROVIDER goes back to mail."""
    assert provider_for_stored('jira-servicedesk').browse_url('RC-1').endswith('/browse/RC-1')
    assert provider_for_stored('retired') is None


def test_the_abstract_surface_is_seven_members():
    assert TicketProvider.__abstractmethods__ == {
        'from_environment', 'configured', 'write_configured', 'summary',
        'create', 'get', 'browse_url'}


class TestMinimalProvider:
    """The extensibility proof: the defaults are safe without overrides."""

    def test_it_instantiates(self):
        assert MinimalProvider().create(DRAFT).key == 'MIN-1'

    def test_find_defaults_to_none_and_check_to_ok(self):
        provider = MinimalProvider()
        assert provider.find('SAM-AR-1') is None
        assert provider.check() == (True, '')

    def test_a_provider_that_cannot_comment_still_creates(self):
        assert MinimalProvider().post_automation_note('MIN-1', DRAFT) is False


class TestDryRun:
    """Files nowhere, mints a local key, and only a task can select it."""

    def test_it_is_never_read(self, monkeypatch):
        from sam.integration.tickets.registry import read_providers
        monkeypatch.setenv('TICKET_PROVIDER', 'dry-run')
        assert 'dry-run' not in PROVIDERS
        assert all(p.name != 'dry-run' for p in read_providers())

    def test_the_webapp_cannot_select_it(self, monkeypatch):
        monkeypatch.setenv('TICKET_PROVIDER', 'dry-run')
        with pytest.raises(TicketNotConfigured, match='scheduled tasks only'):
            provider_from_environment(interactive=True)

    def test_a_task_files_and_comments_without_posting(self, monkeypatch, caplog):
        monkeypatch.setenv('TICKET_PROVIDER', 'dry-run')
        provider = provider_from_environment()
        assert provider.write_configured
        with caplog.at_level('INFO', logger='sam.integration.tickets.dry_run'):
            ref = provider.create(DRAFT)
            provider.comment(ref.key, 'changed')
        assert ref.key == 'DRY-SAM-AR-1' and len(ref.key) <= 32
        assert provider.find('SAM-AR-1') is None
        assert 'would file SAM-AR-1' in caplog.text and 'changed' in caplog.text
