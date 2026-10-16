"""Drift gates for the directory-client levers in the chart.

Both environments read ldap.ucar.edu (the panels, and the check after each sync PUT) and
write nothing. A DIRECTORY_APPLY_<KIND> key appears in neither file until step 2 of
docs/plans/DIRECTORY_CLIENT.md soaks on dev.
"""
import yaml

from _paths import REPO_ROOT

HELM = REPO_ROOT / 'helm'
DECLARED = ('DIRECTORY_PROVIDER', 'DIRECTORY_URL', 'DIRECTORY_CHECK_ON_SYNC')


def _values(name='values.yaml'):
    return yaml.safe_load((HELM / name).read_text())


def _text(name):
    return (HELM / name).read_text()


class TestProduction:
    def test_production_reads_and_reports(self):
        env = _values()['webapp']['env']
        assert tuple(env[k] for k in DECLARED) == ('ucar', 'ldap://ldap.ucar.edu', '1')

    def test_no_apply_lever_anywhere(self):
        for name in ('values.yaml', 'values-dev.yaml'):
            assert 'DIRECTORY_APPLY_' not in '\n'.join(
                line for line in _text(name).splitlines() if not line.lstrip().startswith('#')), name


class TestDev:
    def test_dev_reads_the_real_directory_and_reports(self):
        env = _values('values-dev.yaml')['webapp']['env']
        assert tuple(env[k] for k in DECLARED) == ('ucar', 'ldap://ldap.ucar.edu', '1')

    def test_the_dev_provider_is_registered(self):
        from sam.integration.directory.registry import OFF_NAMES, READERS
        for name in ('values.yaml', 'values-dev.yaml'):
            provider = _values(name)['webapp']['env']['DIRECTORY_PROVIDER']
            assert provider in READERS or provider in OFF_NAMES


def test_the_testing_config_pins_the_directory_off():
    from webapp.config import TestingConfig
    assert (TestingConfig.DIRECTORY_PROVIDER, TestingConfig.DIRECTORY_URL,
            TestingConfig.DIRECTORY_CHECK_ON_SYNC) == ('', '', False)
