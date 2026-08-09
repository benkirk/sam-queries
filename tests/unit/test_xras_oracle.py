"""The pre-cutover oracle: real payload bytes, through the whole pipeline, once.

Why this exists when every handler already has its own tests
------------------------------------------------------------
**There is no observation window.** XRAS repoints one base URL and all six handlers go
live at the same moment, so the first production traffic is also the first validation.
The only comparison available beforehand is against captured payloads whose legacy
outcome is already known.

The handler suites call handlers directly with dicts I wrote. This one starts from the
**bytes on disk**, loads them through ``XrasActionSchema``, and lets the *dispatcher*
choose — so it covers the three seams nothing else does:

1. **bytes → schema → dispatcher → handler → rows**, in one pass. A handler test cannot
   catch a payload whose real shape routes somewhere unexpected.
2. **The § 1.2 action-mix correlation**, which is a claim about *how many rows of which
   type* a post produces. That is a cross-handler property; no single handler test can
   state it.
3. **The replay invariant swept over every allocation an action touched**, rather than
   one at a time.

What it is not
--------------
Eight payloads reach roughly 6 of the 34 error strings and 5 of the 11 allocation-type
strategies. It is a regression harness, not a proof, and it **cannot falsify the wire
contract** — a payload we chose validates a reading we already hold. Real payloads did
the falsifying, in Sprint A. The synthetic error-path fixtures cover the branches this
cannot reach.

Referents are substituted, not sampled
--------------------------------------
The corpus usernames and most projcodes were scrubbed independently of the obfuscated
snapshot, so they resolve to no rows. Each scenario therefore seeds the entities its
payload needs and rewrites ``requestNumber`` / ``roles[]`` / ``resources[]`` to point at
them. **The wire shape stays the real bytes**; only the referents move. That distinction
is the whole reason this is worth running: the schema, the dispatch decision and the
row-shape assertions all see production structure.

See ``docs/plans/XRAS_SPRINT_C.md`` § *The oracle*.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from sam.accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
    replay_amount,
)
from sam.projects.projects import Project
from sam.schemas.forms import XrasActionSchema
from sam.xras.dispatch import dispatch_action
from sam.xras.errors import XrasActionRejected

# noqa: F401 shim — Stage 4A. The body moved to tests/xras_helpers.py; this
# re-export keeps the suite passing UNEDITED, which is the proof the move was
# pure. Commit 4B repoints the imports and deletes every one of these.
from xras_helpers import FIXTURE_DIR, committing  # noqa: F401

pytestmark = pytest.mark.unit

ALL_FIXTURES = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))


def load_through_schema(name):
    """The real bytes, through the real schema — not a hand-written dict."""
    return XrasActionSchema().load(json.loads((FIXTURE_DIR / name).read_text()))




@pytest.fixture
def mapped_resources(session):
    """Three mapped resources, so a multi-resource payload keeps its arity.

    Production shows an Extension touching **3.3 allocations on average** (§ 1.2), so a
    one-resource fixture would not exercise the shape the correlation describes.
    """
    from factories import make_resource
    from sam.integration.xras import XrasResourceRepositoryKeyResource

    resources = []
    for _ in range(3):
        resource = make_resource(session)
        key = 980_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        resource.xras_key = key
        resources.append(resource)
    session.flush()
    return resources


def _project_with_allocations(session, resources, *, amount=1_000_000.0,
                              start=datetime(2020, 1, 1),
                              end=datetime(2025, 12, 31)):
    # ⚠️ The default end deliberately precedes every corpus `actionEndDate`.
    # UCUB0166's is 2026-12-31 exactly, so seeding that date made the extension a
    # legitimate **no-op** — the equal-end-date skip firing correctly, and a test
    # asserting three rows failing for the right reason. Kept as a note because the
    # coincidence is easy to reintroduce.
    from factories import make_account, make_allocation, make_project, make_user
    project = make_project(session)
    project.project_lead_user_id = make_user(session).user_id
    allocations = [
        make_allocation(session, amount=amount, start_date=start, end_date=end,
                        account=make_account(session, project=project,
                                             resource=resource))
        for resource in resources
    ]
    session.flush()
    session.refresh(project)
    return project, allocations


def _retarget(action, *, projcode=None, pi=None, resources=None, amount='250000'):
    """Point a real payload at referents that exist. Shape untouched.

    ⚠️ "Shape untouched" was not true of ``resources``: this rebuilt each entry as
    ``{'key': ...}`` while the wire — and every corpus fixture — sends
    ``resourceRepositoryKey``. So the one helper whose job was to preserve the real
    shape was replacing the field that mattered, which is a large part of why the
    handler read the wrong name for a whole sprint. Keep the wire names here.
    """
    action = dict(action)
    if projcode is not None:
        action['requestNumber'] = projcode
    if pi is not None:
        action['roles'] = [{'roleType': 'PI', 'username': pi.username,
                            'beginDate': '2019-01-01', 'endDate': None}]
    if resources is not None:
        action['resources'] = [
            {'resourceRepositoryKey': r.xras_key, 'awardedAmount': amount,
             'comments': None}
            for r in resources]
    return action


def rows_for(session, allocations, kind):
    ids = {a.allocation_id for a in allocations}
    return [t for t in session.query(AllocationTransaction)
            .filter(AllocationTransaction.allocation_id.in_(ids)).all()
            if t.transaction_type == kind]


def assert_replay_invariant(session, allocations, *, deltas=None):
    """The house invariant, swept over every allocation an action touched.

    ``deltas`` maps allocation id → the amount change the action should have caused;
    absent means zero. Asserting the *delta* rather than equality with ``amount`` is
    deliberate: the factories seed no ``NEW`` row, so absolute equality would be testing
    the fixtures rather than the handler.
    """
    deltas = deltas or {}
    for allocation in allocations:
        history = (session.query(AllocationTransaction)
                   .filter_by(allocation_id=allocation.allocation_id).all())
        expected = deltas.get(allocation.allocation_id, 0.0)
        assert replay_amount(history) == pytest.approx(expected), (
            f'replay drifted on allocation {allocation.allocation_id}')


# ---------------------------------------------------------------------------
# Every payload survives the whole pipeline.
# ---------------------------------------------------------------------------


class TestEveryPayloadLoadsAndRoutes:
    """The cheapest end-to-end claim, and the one that would catch a wire change."""

    def test_the_corpus_is_present(self):
        assert len(ALL_FIXTURES) == 8, ALL_FIXTURES

    @pytest.mark.parametrize('name', ALL_FIXTURES)
    def test_bytes_load_through_the_real_schema(self, name):
        data = load_through_schema(name)
        assert data['actionType']
        assert data['requestNumber']

    @pytest.mark.parametrize('name', ALL_FIXTURES)
    def test_the_dispatcher_always_reaches_a_decision(self, session, name):
        """Never an exception, and never a silent drop: every payload either selects a
        service or is explicitly parked with a reason."""
        from sam.xras.dispatch import select_service
        data = load_through_schema(name)
        service = select_service(session, data)
        assert service is None or service in {
            'add', 'update', 'extend', 'supplement', 'transfer', 'adjust'}


# ---------------------------------------------------------------------------
# The § 1.2 action-mix correlation, as row-shape claims.
# ---------------------------------------------------------------------------


class TestTheActionMixCorrelation:
    """§ 1.2 says exactly how many rows of which type each post produced in production.

    The absolute counts cannot be reproduced against a different database, but the
    **per-post shape** can, and that is what the correlation actually asserts:
    Extension writes one ``EXTENSION`` row per touched allocation, New writes one
    ``NEW`` per resource, Supplement one ``SUPPLEMENT`` per resource.
    """

    def test_extension_writes_one_row_per_allocation(self, committing,
                                                     mapped_resources):
        """213 EXTENSION rows over 65 posts — **3.3 allocations per post**. The shape
        that produces that average is one row per active account's latest allocation,
        and ``resources[]`` is not consulted."""
        session = committing
        project, allocations = _project_with_allocations(session, mapped_resources)
        data = _retarget(load_through_schema('extension_ucub0166_ok.json'),
                         projcode=project.projcode)
        assert data['resources'] == [], 'the real Extension sends no resources'

        result = dispatch_action(session, data)

        assert result.status == 'processed'
        assert len(rows_for(session, allocations,
                            AllocationTransactionType.EXTENSION)) == 3
        assert_replay_invariant(session, allocations)

    def test_supplement_writes_one_row_per_requested_resource(self, committing,
                                                              mapped_resources):
        """24 SUPPLEMENT rows over 16 posts — 1.5 per post, i.e. driven by the length
        of ``resources[]`` rather than by the project's accounts."""
        session = committing
        project, allocations = _project_with_allocations(session, mapped_resources)
        data = _retarget(load_through_schema('supplement_ubrn0027_ok.json'),
                         projcode=project.projcode,
                         resources=mapped_resources[:2], amount='90000')

        result = dispatch_action(session, data)

        assert result.status == 'processed'
        assert len(rows_for(session, allocations,
                            AllocationTransactionType.SUPPLEMENT)) == 2
        touched = allocations[:2]
        assert_replay_invariant(
            session, touched,
            deltas={a.allocation_id: 90_000.0 for a in touched})

    def test_new_writes_one_allocation_per_resource(self, committing,
                                                    mapped_resources, session):
        """63 NEW rows over 23 posts — 2.7 allocations per post."""
        from factories import (make_mnemonic_code, make_organization, make_user,
                               make_user_organization)
        from factories._seq import next_seq

        pi = make_user(session)
        soft_link = f'Oracle Test Section {next_seq("orc")}'
        make_user_organization(session, user=pi,
                               organization=make_organization(session,
                                                              name=soft_link))
        make_mnemonic_code(session, description=soft_link)

        # ⚠️ The real payload carries `grants: ['EAR-2425607']`, so the New path's
        # contract resolution is exercised end to end here — and the action **fails**
        # without it. That is not a fixture detail: a New action whose grant SAM does
        # not hold is one of the measured production failure classes.
        from factories import make_contract
        make_contract(session, contract_number='EAR-2425607')
        data = _retarget(load_through_schema('new_ncar4253_ok.json'),
                         pi=pi, resources=mapped_resources, amount='500000')

        result = dispatch_action(committing, data)

        assert result.status == 'processed'
        project = Project.get_by_projcode(session, result.projcode)
        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project.project_id).all())
        assert len(created) == 3
        assert len(rows_for(session, created, 'NEW')) == 3

    def test_a_successful_post_can_mutate_nothing(self, committing,
                                                  mapped_resources):
        """§ 1.2's "2 successful posts that mutated nothing", reproduced. An Extension
        whose action end date equals every existing end writes no rows and still
        reports success — the equal-end-date skip is not an error path."""
        session = committing
        project, allocations = _project_with_allocations(
            session, mapped_resources, end=datetime(2033, 7, 31))
        data = _retarget(load_through_schema('extension_ucub0166_ok.json'),
                         projcode=project.projcode)
        data['actionEndDate'] = '2033-07-31'

        result = dispatch_action(session, data)

        assert result.status == 'processed'
        assert rows_for(session, allocations,
                        AllocationTransactionType.EXTENSION) == []
        assert_replay_invariant(session, allocations)


# ---------------------------------------------------------------------------
# The two known-correct failure oracles.
# ---------------------------------------------------------------------------


class TestTheFailureOracles:
    """UFSU0023 and NCAR4232 both have production outcomes with exact strings."""

    def test_ufsu0023_rejects_with_its_production_string(self, committing,
                                                        mapped_resources):
        """Its ``actionEndDate`` is 2027-09-30 against an allocation ending
        2033-07-31, and legacy answered with this exact line. End to end from the real
        bytes this time, rather than from a hand-built dict."""
        session = committing
        project, _ = _project_with_allocations(session, mapped_resources,
                                               end=datetime(2033, 7, 31))
        data = _retarget(load_through_schema('extension_ufsu0023_failed.json'),
                         projcode=project.projcode)
        assert data['actionEndDate'] == '2027-09-30'

        with pytest.raises(XrasActionRejected) as exc:
            dispatch_action(session, data)
        assert exc.value.messages == [
            'Action end date is before existing allocation end date (2033-07-31)']

    def test_a_rejected_action_writes_absolutely_nothing(self, committing,
                                                        mapped_resources):
        """Assemble → check once → execute. The whole contract in one assertion."""
        session = committing
        project, allocations = _project_with_allocations(
            session, mapped_resources, end=datetime(2033, 7, 31))
        data = _retarget(load_through_schema('extension_ufsu0023_failed.json'),
                         projcode=project.projcode)

        with pytest.raises(XrasActionRejected):
            dispatch_action(session, data)

        assert rows_for(session, allocations,
                        AllocationTransactionType.EXTENSION) == []
        assert all(a.end_date == datetime(2033, 7, 31, 23, 59, 59)
                   for a in allocations)

    def test_ncar4232_fails_on_the_mnemonic_as_it_did_in_production(
            self, committing, mapped_resources, session):
        """The 24% failure class. Its PI is an ARC placeholder identity with no
        resolvable organization, and ``grants: []`` is **not** what failed it."""
        from factories import make_organization, make_user, make_user_organization
        pi = make_user(session)
        make_user_organization(session, user=pi,
                               organization=make_organization(session))

        data = load_through_schema('new_ncar4232_failed.json')
        assert data['grants'] == [], 'the Educational shape carries no grant'
        data = _retarget(data, pi=pi, resources=mapped_resources)

        with pytest.raises(XrasActionRejected) as exc:
            dispatch_action(committing, data)
        assert ('Could not determine Mnemonic code for internal PI via organization'
                in exc.value.messages)


# ---------------------------------------------------------------------------
# Cross-handler sweep.
# ---------------------------------------------------------------------------


class TestTheWholeCorpusInOnePass:

    def test_no_payload_leaves_an_allocation_inconsistent(self, committing,
                                                          mapped_resources):
        """Every corpus payload, dispatched against a fresh project, with the replay
        invariant checked over everything it touched.

        Payloads that reject are counted as covered — a 422 that writes nothing is a
        correct outcome, and the assertion that nothing was written is the point.
        """
        session = committing
        outcomes = {}

        for name in ALL_FIXTURES:
            project, allocations = _project_with_allocations(
                session, mapped_resources[:1])
            data = load_through_schema(name)
            if data['actionType'] in ('Extension', 'Supplement', 'Adjustment'):
                data = _retarget(data, projcode=project.projcode,
                                 resources=mapped_resources[:1], amount='1000')
            else:
                continue        # New/Update need a mnemonic; covered above

            before = {a.allocation_id: float(a.amount) for a in allocations}
            try:
                result = dispatch_action(session, data)
                outcomes[name] = result.status
            except XrasActionRejected:
                outcomes[name] = 'failed'
                assert all(float(a.amount) == before[a.allocation_id]
                           for a in allocations), f'{name} wrote on a rejection'
                continue

            deltas = {a.allocation_id: float(a.amount) - before[a.allocation_id]
                      for a in allocations}
            assert_replay_invariant(session, allocations, deltas=deltas)

        assert outcomes, 'the sweep matched no payloads — the filter is wrong'
        assert set(outcomes.values()) <= {'processed', 'manual', 'failed'}

    def test_transfer_would_park_rather_than_apply(self, committing,
                                                   mapped_resources):
        """No Transfer payload exists, so this synthesises the dispatch decision only —
        the one case where the corpus cannot speak for itself."""
        session = committing
        project, _ = _project_with_allocations(session, mapped_resources[:1])
        result = dispatch_action(session, {
            'actionType': 'Transfer', 'requestNumber': project.projcode,
            'resources': [], 'roles': []})
        assert result.status == 'manual'
        assert 'deliberately not serviced' in result.reason


# ---------------------------------------------------------------------------
# The pre-cutover mapping gate.
# ---------------------------------------------------------------------------


class TestTheResourceMappingGate:
    """``sam-admin xras --validate-mapping``.

    ⚠️ **This is a pre-cutover check specifically, not a post-cutover one.**
    ``xras_resource_repository_key_resource`` is the join behind two different things:
    on the write side an unmapped key fails the action, and on the **read** side
    ``resourceRepositoryKey`` is simply *omitted* from the GET payloads when a resource
    has no row. So closing a gap **moves response bytes** — adding a mapping after the
    parity run invalidates it.
    """

    def test_it_reports_an_unmapped_active_resource(self, session):
        from cli.xras.builders import build_mapping_report
        from factories import make_resource
        resource = make_resource(session)

        report = build_mapping_report(session)
        assert resource.resource_name in report['unmapped_active']

    def test_a_mapped_resource_is_not_reported(self, session, mapped_resources):
        from cli.xras.builders import build_mapping_report
        report = build_mapping_report(session)
        for resource in mapped_resources:
            assert resource.resource_name not in report['unmapped_active']

    def test_a_decommissioned_mapping_is_reported_separately(self, session):
        """Untidy, not broken — so it must not gate a deploy the way a gap does."""
        from cli.xras.builders import build_mapping_report
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        resource = make_resource(session)
        resource.decommission_date = datetime(2020, 1, 1)
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=990_000 + resource.resource_id,
            resource_id=resource.resource_id))
        session.flush()

        report = build_mapping_report(session)
        assert resource.resource_name in [
            e['resource'] for e in report['mapped_decommissioned']]
        assert resource.resource_name not in report['unmapped_active']

    def test_the_snapshot_still_shows_the_documented_gap(self, session):
        """The 11 unmapped active resources § 9 names. If this count moves, either
        somebody closed a gap — which changes GET response bytes and needs a parity
        re-run — or a resource was commissioned without a mapping."""
        from cli.xras.builders import build_mapping_report
        report = build_mapping_report(session)
        documented = {'Boreas', 'Destor', 'GLADE user', 'GLADE work', 'Gust',
                      'Gust GPU', 'hpc', 'hpc-dev', 'HPC_Futures_Lab', 'Laramie',
                      'Quasar'}
        assert documented <= set(report['unmapped_active']), (
            'a documented mapping gap closed — GET response bytes moved, so the '
            'parity run needs repeating')
