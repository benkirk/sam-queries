"""The one place XRAS opens a transaction, and a proof that it is the only one.

Why this file exists
--------------------
Handler tests neutralise the commit by monkeypatching ``management_transaction`` in a
module's globals. The suite's per-test isolation is a SAVEPOINT on the session's
connection, so a real ``COMMIT`` releases it and the rows escape into the shared xdist
database — which has already happened once, during Sprint C, leaking three
``allocation_transaction`` rows and mutating three ``end_date`` values.

While five handler modules each held their own ``from ... import
management_transaction``, every such test had to patch five separate bindings. Missing
one has **two** failure modes and only the first is safe:

* the name is absent → ``monkeypatch.setattr`` raises ``AttributeError`` at setup. Loud.
* the name is present but unused → the patch succeeds, the real context manager runs
  from wherever the code actually reads it, the rows are written and committed, and
  **every assertion still passes**. Silent, and it damages other workers' runs.

The second is what these tests exist to make impossible.
"""

import importlib
import pkgutil
from contextlib import contextmanager

import pytest

import sam.xras
import sam.xras.handlers  # noqa: F401  — imports all six for their registration

pytestmark = pytest.mark.unit

#: The single module permitted to bind ``management_transaction``.
THE_SEAM = 'sam.xras.handlers.base'


def _xras_modules():
    """Every module under ``sam.xras``, imported."""
    modules = [sam.xras]
    for info in pkgutil.walk_packages(sam.xras.__path__, 'sam.xras.'):
        modules.append(importlib.import_module(info.name))
    return modules


class TestTheSeamIsSingular:

    def test_only_the_base_module_binds_management_transaction(self):
        """A **globals** scan, not a grep and not an AST walk.

        Both of the cheaper checks miss the case that matters. A re-export
        (``from .base import management_transaction``) is invisible to a grep for the
        original import line, and an AST walk of one file cannot see a name rebound at
        runtime. ``vars(module)`` sees all of them, because a re-export *is* a module
        global.

        This also fires the day a seventh handler lands opening its own transaction,
        which is the long-term value.
        """
        binders = {m.__name__ for m in _xras_modules()
                   if 'management_transaction' in vars(m)}
        assert binders == {THE_SEAM}, (
            f'management_transaction must be imported only by {THE_SEAM}. '
            f'Found: {sorted(binders)}. Every extra binding is a patch point that '
            f'handler tests must neutralise, and a missed one commits silently.')

    def test_the_handler_modules_have_no_transaction_of_their_own(self):
        """The specific regression: five modules used to bind it, one each."""
        import sam.xras.handlers as handlers

        for name in ('extension', 'supplement', 'adjustment', 'new', 'update',
                     'transfer'):
            module = getattr(handlers, name)
            assert not hasattr(module, 'management_transaction'), (
                f'sam.xras.handlers.{name} binds management_transaction again')


class TestPatchingTheSeamIsSufficient:
    """The behavioural half — the scan proves *where*, this proves *enough*."""

    @pytest.fixture
    def recording(self, session, monkeypatch):
        """Patch only ``base``, and record every entry."""
        import sam.xras.handlers.base as base

        entries = []

        @contextmanager
        def flushing(sess):
            entries.append(sess)
            yield sess
            sess.flush()

        monkeypatch.setattr(base, 'management_transaction', flushing)
        return entries

    @pytest.fixture
    def no_commit(self, session, monkeypatch):
        """Turn any commit into a failure.

        This is the assertion that does not care *how* the code reached a commit —
        a stray import, a helper that opens its own transaction, a future handler that
        forgets the template. Anything that gets there fails the test.
        """
        def forbidden():
            pytest.fail('committed outside the one seam in sam.xras.handlers.base')

        monkeypatch.setattr(session, 'commit', forbidden)

    @pytest.fixture
    def mapped_resource(self, session):
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource

        resource = make_resource(session)
        key = 940_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        session.flush()
        resource.xras_key = key
        return resource

    def test_one_entry_per_action_and_no_commit(self, session, recording, no_commit,
                                                mapped_resource):
        """Drive a real handler through ``dispatch_action`` with one patch point."""
        from factories import make_account, make_allocation, make_project
        from sam.xras.dispatch import dispatch_action

        project = make_project(session)
        make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project,
                                 resource=mapped_resource))
        session.refresh(project)

        result = dispatch_action(session, {
            'actionType': 'Supplement', 'requestNumber': project.projcode,
            'allocationType': 'Small',
            'resources': [{'key': mapped_resource.xras_key,
                           'awardedAmount': '250000', 'comments': None}],
            'roles': []})

        assert result.status == 'processed'
        assert len(recording) == 1, (
            'a handler must open exactly one transaction per action — the '
            'assemble → check once → execute contract')

    def test_a_rejected_action_opens_no_transaction_at_all(self, session, recording,
                                                           no_commit, mapped_resource):
        """The other half of the contract, and the reason the 422 can promise
        "nothing was written": the rejection happens *before* the seam."""
        from factories import make_account, make_allocation, make_project
        from sam.xras.dispatch import dispatch_action
        from sam.xras.errors import XrasActionRejected

        project = make_project(session)
        make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project,
                                 resource=mapped_resource))
        session.refresh(project)

        with pytest.raises(XrasActionRejected):
            dispatch_action(session, {
                'actionType': 'Supplement', 'requestNumber': project.projcode,
                'allocationType': 'Small',
                'resources': [{'key': 999_996, 'awardedAmount': '250000',
                               'comments': None}],
                'roles': []})

        assert recording == []


class TestTheNewHandlerDoesNotOwnProject:
    """A tripwire, not a behaviour test.

    ``ActionHandler.project`` means "the **existing** project named by
    ``requestNumber``". For the New handler that is always ``None`` — ``select_service``
    routes to ``add`` only when no such project exists — and the project it creates
    lives on ``created_project`` deliberately.

    Repointing ``self.project`` at the created row is legal, tempting, and would
    silently change what ``auth_at_panel_meeting``'s second arm reads: from "no project,
    so ``False``" to "the type this action just assigned". On the handler with the
    highest failure rate and the least production evidence.
    """

    def test_project_is_none_for_a_new_action(self, session):
        from sam.xras.handlers.new import NewHandler

        handler = NewHandler(session, {
            'actionType': 'New', 'requestNumber': 'NOSUCH9999', 'roles': [],
            'resources': []})
        assert handler.project is None
        assert handler.projcode == 'NOSUCH9999'
