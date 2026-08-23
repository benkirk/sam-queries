"""`Context`'s two SAM accessors — the connect, and the CLI's exit policy.

`require_sam()` calls ``sys.exit(1)``, which is right for a subcommand and fatal
for a scheduled task: `scheduling.runner._execute` catches `Exception`, not
`BaseException`, so a `SystemExit` raised inside a task body escapes `run_due`
and terminates the dispatcher rather than failing one task.

`open_sam()` is the same connect without that policy. These tests pin both
halves — the raising one because the task runner depends on it, and the exiting
one because every existing subcommand's behavior must be unchanged by the split.
"""

from io import StringIO

import pytest
from rich.console import Console

from cli.core.context import Context, SamConnectionError

pytestmark = pytest.mark.unit


@pytest.fixture
def ctx():
    return Context()


def _fail_to_connect(monkeypatch, message='refused'):
    """Make `create_sam_engine` blow up at the import site `Context` uses."""
    def boom():
        raise OSError(message)
    monkeypatch.setattr('sam.session.create_sam_engine', boom)


def _capture_consoles(ctx):
    """Swap both consoles for buffers, returning ``(stdout_buf, stderr_buf)``.

    Injected rather than captured: rich binds its file object when the Console
    is constructed, so neither `capsys` nor `capfd` reliably sees output from a
    Console built before the fixture installed its capture. Swapping the two
    attributes tests the real contract — *which* console was written to — with
    no dependency on pytest's capture plumbing.
    """
    out, err = StringIO(), StringIO()
    ctx.console = Console(file=out)
    ctx.stderr_console = Console(file=err)
    return out, err


class TestOpenSam:
    def test_raises_rather_than_exiting(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch)

        with pytest.raises(SamConnectionError, match='Error connecting'):
            ctx.open_sam()

    def test_chains_the_original_exception(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch, 'connection refused by host')

        with pytest.raises(SamConnectionError) as exc:
            ctx.open_sam()

        assert isinstance(exc.value.__cause__, OSError)
        assert 'connection refused by host' in str(exc.value)

    def test_a_failed_connect_leaves_no_half_open_session(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch)

        with pytest.raises(SamConnectionError):
            ctx.open_sam()

        assert ctx.session is None, 'reading .session must still not connect'


class TestRequireSam:
    """Unchanged behavior: the red message and exit 1, exactly as before."""

    def test_exits_1_on_failure(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            ctx.require_sam()

        assert exc.value.code == 1

    def test_prints_the_error_to_stderr_not_stdout(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch, 'host is down')
        out, err = _capture_consoles(ctx)

        with pytest.raises(SystemExit):
            ctx.require_sam()

        assert 'host is down' in err.getvalue()
        assert 'Error connecting to database' in err.getvalue()
        assert out.getvalue() == '', 'a JSON envelope shares stdout; keep it clean'

    def test_does_not_leak_a_traceback(self, ctx, monkeypatch):
        _fail_to_connect(monkeypatch)
        _out, err = _capture_consoles(ctx)

        with pytest.raises(SystemExit):
            ctx.require_sam()

        assert 'Traceback' not in err.getvalue()


class TestTheyShareOneSession:
    """The split must not duplicate the connect or the cache."""

    def test_both_return_the_injected_session_without_connecting(self, ctx):
        sentinel = object()
        ctx.session = sentinel                      # the setter tests use

        def explode():
            raise AssertionError('must not connect when one is cached')

        assert ctx.open_sam() is sentinel
        assert ctx.require_sam() is sentinel

    def test_connects_once_across_both_accessors(self, ctx, monkeypatch):
        calls = []

        def fake_engine():
            calls.append(1)
            return (object(), None)

        monkeypatch.setattr('sam.session.create_sam_engine', fake_engine)
        monkeypatch.setattr('cli.core.context.Session', lambda engine: object())

        first = ctx.open_sam()
        assert ctx.require_sam() is first
        assert ctx.open_sam() is first
        assert len(calls) == 1


def test_the_task_runner_is_wired_to_the_raising_accessor():
    """The whole point of the split, asserted at the wiring rather than in prose.

    `sam-admin tasks --run-due` hands this factory to `run_due`. If it ever goes
    back to `require_sam`, a SAM outage stops being one failed task and becomes a
    dead dispatcher.
    """
    import inspect

    from cli.tasks.commands import TasksCommand

    source = inspect.getsource(TasksCommand._dispatch)
    assert 'sam_session_factory=self.ctx.open_sam' in source
    assert 'sam_session_factory=self.ctx.require_sam' not in source
