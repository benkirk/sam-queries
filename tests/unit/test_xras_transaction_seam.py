"""The one place XRAS opens a transaction, and a proof that it is the only one.

Why this file exists
--------------------
Handler tests neutralize the commit by monkeypatching ``management_transaction`` in a
module's globals. The suite's per-test isolation is a SAVEPOINT on the session's
connection, so a real ``COMMIT`` releases it and the rows escape into the shared xdist
database — which has already happened once, during Sprint C, leaking three
``allocation_transaction`` rows and mutating three ``end_date`` values.

While five handler modules each held their own ``from ... import
management_transaction``, every such test had to patch five separate bindings. Missing
one has **two** failure modes and only the first is safe:

* the name is absent -> ``monkeypatch.setattr`` raises ``AttributeError`` at setup. Loud.
* the name is present but unused -> the patch succeeds, the real context manager runs
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
from sam.xras.errors import ActionErrors

from factories import make_xras_opportunity_mapping

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
    """The behavioral half — the scan proves *where*, this proves *enough*."""

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
            'resources': [{'resourceRepositoryKey': mapped_resource.xras_key,
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
                'resources': [{'resourceRepositoryKey': 999_996,
                               'awardedAmount': '250000', 'comments': None}],
                'roles': []})

        assert recording == []


class TestTheNewHandlerDoesNotOwnProject:
    """A tripwire, not a behavior test.

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


class TestPanelAuthorisationAgreesWithTheResolvedType:
    """WARNING: **The sharp edge of the ``opportunityId`` map.**

    ``auth_at_panel_meeting`` re-derives the ``(panel, type)`` pair *independently*
    of ``resolve_allocation_type`` — the first sets ``auth_at_panel_mtg`` on
    ``allocation_transaction`` rows, the second sets ``project.allocation_type_id``.
    Wiring only the second to the map would let a project's type come from the map
    while its transactions' panel-authorization flag came from the ladder:
    inconsistent rows, written, with nothing raised and nothing logged.

    Called from ``new.py``, ``update.py``, ``supplement.py`` and ``adjustment.py``,
    so this is not a corner.
    """

    def _chap(self, session):
        from sam.accounting.allocations import AllocationType
        from sam.resources.facilities import Panel
        return (session.query(AllocationType)
                .join(Panel, AllocationType.panel_id == Panel.panel_id)
                .filter(Panel.panel_name == 'CHAP')
                .filter(AllocationType.allocation_type == 'CHAP').one())

    def test_a_mapped_action_is_panel_authorised_through_the_map(self, session):
        """``Small`` is not panel-authorized; ``CHAP`` is. Mapping the id to CHAP must
        flip the flag — if this reads the ladder it stays False and the transaction is
        written unauthorised while the project sits on a CHAP type."""
        from sam.xras.extractors import resolve_allocation_type
        from sam.xras.handlers._allocations import auth_at_panel_meeting

        wire = {'allocationType': 'Small', 'opportunityId': 999002}
        assert auth_at_panel_meeting(session, wire) is False

        make_xras_opportunity_mapping(session, allocation_type=self._chap(session),
                                      opportunity_id=999002)

        assert auth_at_panel_meeting(session, wire) is True
        row = resolve_allocation_type(session, wire, ActionErrors())
        assert row.allocation_type == 'CHAP'

    def test_an_unmapped_action_still_reads_the_ladder(self, session):
        """The fallback, on the same call path — an empty map changes nothing here
        either."""
        from sam.xras.handlers._allocations import auth_at_panel_meeting

        assert auth_at_panel_meeting(
            session, {'allocationType': 'Small', 'opportunityId': 999003}) is False
        assert auth_at_panel_meeting(
            session, {'allocationType': 'CSL', 'requestTitle': 'CSL'}) is True

    def test_the_two_functions_agree_on_every_seeded_corpus_payload(self, session):
        """Stated as the invariant rather than a case: whatever the pair comes from,
        both consumers must read the *same* pair."""
        from sam.xras.extractors import (resolve_allocation_type,
                                         select_allocation_type_mapped)
        from sam.xras.handlers._allocations import (_PANEL_AUTHORISED,
                                                    auth_at_panel_meeting)
        from xras_helpers import FIXTURE_DIR, load_fixture

        checked = 0
        for path in sorted(FIXTURE_DIR.glob('*.json')):
            payload = load_fixture(path.name)
            if not payload.get('allocationType'):
                continue          # the second arm; a different question (see the docstring there)
            checked += 1
            parms = select_allocation_type_mapped(session, payload)
            row = resolve_allocation_type(session, payload, ActionErrors())
            assert row is not None and row.allocation_type == parms.allocation_type
            assert auth_at_panel_meeting(session, payload) == (
                parms.allocation_type in _PANEL_AUTHORISED)
        assert checked, 'no payload carried allocationType — the loop proved nothing'


class TestPanelsOnTheWire:
    """``panels[]`` names the reviewing panel outright — CHAP in exactly the
    payloads where the flag matters. It may ADD authorization the ladder
    missed; it never withdraws a derived True (the stored-type CSL arm has no
    wire counterpart)."""

    def test_a_chap_primary_panel_authorises_when_the_ladder_misses(
            self, session):
        from sam.xras.handlers._allocations import auth_at_panel_meeting

        wire = {'allocationType': 'Small', 'opportunityId': None,
                'panels': [{'type': 'Technical',
                            'name': 'CISL HPC Allocation Panel',
                            'abbr': 'CHAP', 'isPrimary': True},
                           {'type': 'Technical',
                            'name': 'External reviewers for CHAP',
                            'abbr': 'CHAP External', 'isPrimary': False}]}
        assert auth_at_panel_meeting(session, wire) is True

    def test_a_non_chap_panel_cannot_withdraw_a_derived_true(self, session):
        from sam.xras.handlers._allocations import auth_at_panel_meeting

        wire = {'allocationType': 'University Large', 'opportunityId': None,
                'panels': [{'abbr': 'CISL RSD', 'isPrimary': True}]}
        derived_alone = auth_at_panel_meeting(session, dict(wire, panels=[]))
        assert auth_at_panel_meeting(session, wire) is derived_alone

    def test_no_primary_panel_defers_entirely_to_the_derivation(self, session):
        from sam.xras.handlers._allocations import auth_at_panel_meeting

        wire = {'allocationType': 'Small', 'opportunityId': None,
                'panels': [{'abbr': 'CHAP', 'isPrimary': False}]}
        assert auth_at_panel_meeting(session, wire) is False
