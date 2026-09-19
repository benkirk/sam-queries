"""configure_logging — plugin-logger wiring.

The interesting case is not app.logger (Flask would have given us something
usable anyway); it is the OPTIONAL PLUGIN loggers. `job_history` / `fs_scans`
call `getLogger(__name__)` under their own package roots, so they inherit the
root logger, which this app never configures. Left alone that resolves to
WARNING with no handlers and the plugin's diagnostics vanish — including
`jobs_timeseries`'s DEBUG line naming which path served a request, which is
the only way to tell a ~65 ms rollup hit from a ~7.4 s full scan (the two
return byte-identical envelopes by design).
"""
import logging

import pytest

from webapp.logging_config import configure_logging

pytestmark = pytest.mark.unit

_PLUGIN_LOGGERS = ('job_history', 'fs_scans')


class _FakeApp:
    """Minimal stand-in — configure_logging touches only .config/.logger."""

    def __init__(self, level='INFO'):
        self.config = {'LOG_LEVEL': level, 'LOG_FILE': ''}
        self.logger = logging.getLogger(f'_test_app_{level}_{id(self)}')


@pytest.fixture(autouse=True)
def _restore_plugin_loggers():
    """configure_logging mutates process-global loggers; put them back."""
    saved = {name: (lg.handlers[:], lg.level, lg.propagate)
             for name in _PLUGIN_LOGGERS
             for lg in [logging.getLogger(name)]}
    yield
    for name, (handlers, level, propagate) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers = handlers
        lg.setLevel(level)
        lg.propagate = propagate


@pytest.mark.parametrize('name', _PLUGIN_LOGGERS)
def test_plugin_loggers_get_handlers(name):
    """Without a handler the plugin's records are dropped on the floor."""
    configure_logging(_FakeApp())
    assert logging.getLogger(name).handlers


@pytest.mark.parametrize('name', _PLUGIN_LOGGERS)
def test_plugin_loggers_follow_log_level(name):
    """LOG_LEVEL=DEBUG must actually reach the plugin, or the routing
    diagnostic stays invisible no matter how it is configured."""
    configure_logging(_FakeApp(level='DEBUG'))
    assert logging.getLogger(name).isEnabledFor(logging.DEBUG)


@pytest.mark.parametrize('name', _PLUGIN_LOGGERS)
def test_plugin_loggers_are_quiet_at_default_level(name):
    """The wiring must not turn INFO into a firehose of plugin DEBUG."""
    configure_logging(_FakeApp(level='INFO'))
    assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)
    assert logging.getLogger(name).isEnabledFor(logging.INFO)


@pytest.mark.parametrize('name', _PLUGIN_LOGGERS)
def test_plugin_loggers_do_not_propagate(name):
    """Own handlers AND propagate would double-print if root ever gains one."""
    configure_logging(_FakeApp())
    assert logging.getLogger(name).propagate is False


def test_repeated_configuration_does_not_stack_handlers():
    """create_app() runs per-process, but tests build many apps — handlers
    must be replaced, not appended, or output multiplies."""
    configure_logging(_FakeApp())
    first = len(logging.getLogger('job_history').handlers)
    configure_logging(_FakeApp())
    assert len(logging.getLogger('job_history').handlers) == first


def test_noisy_third_party_loggers_stay_suppressed():
    """Guard against the tempting 'just configure root' shortcut, which would
    unmute sqlalchemy.engine along with the plugin."""
    configure_logging(_FakeApp(level='DEBUG'))
    for noisy in ('werkzeug', 'sqlalchemy.engine', 'sqlalchemy.pool'):
        assert not logging.getLogger(noisy).isEnabledFor(logging.DEBUG)
