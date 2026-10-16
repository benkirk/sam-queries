"""``DirectoryConfig`` and the registry: off by default, per-kind apply levers, named readers."""
import pytest

from sam.integration.directory import KINDS, DirectoryNotConfigured, NullDirectory
from sam.integration.directory.config import (DirectoryConfig, apply_lever,
                                              directory_check_on_sync, directory_configured)
from sam.integration.directory.registry import (READERS, build_reader, reader_from_environment,
                                                reader_names)
from sam.integration.directory.ucar import UCARDirectory


def test_the_suite_runs_with_the_directory_off():
    config = DirectoryConfig.from_environment()
    assert (config.provider, config.url, config.configured, config.check_on_sync) == (
        '', '', False, False)
    assert config.apply_kinds == frozenset()
    assert directory_configured() is False
    assert directory_check_on_sync() is False


def test_configured_needs_both_the_provider_and_the_url(monkeypatch):
    monkeypatch.setenv('DIRECTORY_PROVIDER', 'ucar')
    assert DirectoryConfig.from_environment().configured is False
    monkeypatch.setenv('DIRECTORY_URL', 'ldap://ldap.example.invalid')
    assert DirectoryConfig.from_environment().configured is True


@pytest.mark.parametrize('kind', KINDS)
def test_each_kind_has_its_own_apply_lever(monkeypatch, kind):
    assert apply_lever(kind) == f'DIRECTORY_APPLY_{kind.upper()}'
    monkeypatch.setenv(apply_lever(kind), '1')
    config = DirectoryConfig.from_environment()
    assert config.apply_kinds == frozenset({kind})
    assert config.apply_enabled(kind) is True
    assert all(not config.apply_enabled(other) for other in KINDS if other != kind)


def test_the_summary_names_every_kind_and_no_secret(monkeypatch):
    monkeypatch.setenv('DIRECTORY_APPLY_PERSON', 'yes')
    summary = DirectoryConfig.from_environment().summary()
    assert set(summary['apply']) == set(KINDS)
    assert summary['apply']['person'] is True
    assert set(summary) == {'provider', 'url', 'base_dn', 'timeout', 'configured',
                            'check_on_sync', 'apply'}


def test_defaults_survive_blank_values(monkeypatch):
    monkeypatch.setenv('DIRECTORY_BASE_DN', '')
    monkeypatch.setenv('DIRECTORY_TIMEOUT_SECONDS', 'soon')
    config = DirectoryConfig.from_environment()
    assert (config.base_dn, config.timeout) == ('dc=ucar,dc=edu', 5.0)


class TestRegistry:
    def test_every_reader_is_named_by_its_key(self):
        for name, cls in READERS.items():
            assert cls.name == name
        assert reader_names() == ['ucar']

    @pytest.mark.parametrize('name', ['', 'none', 'OFF', ' None '])
    def test_off_names_give_the_null_reader(self, name):
        assert isinstance(build_reader(name), NullDirectory)

    def test_the_suite_selects_nothing(self):
        assert isinstance(reader_from_environment(), NullDirectory)

    def test_a_provider_without_a_url_is_off(self, monkeypatch):
        monkeypatch.setenv('DIRECTORY_PROVIDER', 'ucar')
        assert isinstance(reader_from_environment(), NullDirectory)

    def test_a_provider_with_a_url_builds_its_reader(self, monkeypatch):
        monkeypatch.setenv('DIRECTORY_PROVIDER', 'ucar')
        monkeypatch.setenv('DIRECTORY_URL', 'ldap://ldap.example.invalid')
        reader = reader_from_environment()
        assert isinstance(reader, UCARDirectory)
        assert reader.configured is True
        assert reader.summary()['url'] == 'ldap://ldap.example.invalid'

    def test_an_unknown_name_lists_the_valid_ones(self):
        with pytest.raises(DirectoryNotConfigured) as caught:
            build_reader('active-directory')
        assert 'ucar' in str(caught.value) and 'none' in str(caught.value)
