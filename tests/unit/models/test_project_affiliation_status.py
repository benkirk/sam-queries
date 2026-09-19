"""Tests for Project.contracts_current_first() / organizations_current_first()
and the project-card badges that surface them.

Unlike the User affiliation helpers, these filter NOTHING: 84% of production
project-contract links point at an expired contract, and an expired grant is
still the project's funding provenance. The helpers only ORDER the rows so the
card can badge the lapsed ones.
"""
import pytest
from datetime import datetime, timedelta

from factories import (
    make_organization,
    make_project,
    make_project_contract,
    make_project_organization,
)

pytestmark = pytest.mark.unit


def _ago(days):
    return datetime.now() - timedelta(days=days)


def _ahead(days):
    return datetime.now() + timedelta(days=days)


class TestContractsCurrentFirst:

    def test_nothing_is_filtered_out(self, session):
        """Every link is returned, expired ones included."""
        project = make_project(session)
        make_project_contract(session, project=project, end_date=_ago(30))
        make_project_contract(session, project=project, end_date=None)

        assert len(project.contracts_current_first()) == 2

    def test_current_sorts_ahead_of_expired(self, session):
        project = make_project(session)
        expired = make_project_contract(
            session, project=project, start_date=_ago(800), end_date=_ago(30))
        current = make_project_contract(
            session, project=project, start_date=_ago(100), end_date=_ahead(400))

        assert project.contracts_current_first() == [current, expired]

    def test_expired_ordered_most_recent_first(self, session):
        """The CESM0002 shape: one current contract, three lapsed ones."""
        project = make_project(session)
        oldest = make_project_contract(
            session, project=project, start_date=_ago(3000), end_date=_ago(2000))
        middle = make_project_contract(
            session, project=project, start_date=_ago(2000), end_date=_ago(900))
        newest = make_project_contract(
            session, project=project, start_date=_ago(900), end_date=_ago(100))
        current = make_project_contract(
            session, project=project, start_date=_ago(500), end_date=_ahead(400))

        assert project.contracts_current_first() == [
            current, newest, middle, oldest]

    def test_all_expired_still_returns_every_row(self, session):
        """The URTG0006 shape — 171 active projects have only expired contracts,
        so the block must not collapse to empty."""
        project = make_project(session)
        older = make_project_contract(
            session, project=project, start_date=_ago(900), end_date=_ago(400))
        newer = make_project_contract(
            session, project=project, start_date=_ago(400), end_date=_ago(20))

        assert project.contracts_current_first() == [newer, older]

    def test_future_dated_contract_is_not_current(self, session):
        """A not-yet-started contract must be distinguishable from an expired
        one — an end_date alone is not the discriminator."""
        project = make_project(session)
        pc = make_project_contract(
            session, project=project,
            start_date=_ahead(30), end_date=_ahead(400))

        assert pc.contract.is_active is False
        assert pc.contract.is_future is True

    def test_mistyped_start_after_end_reads_as_future(self, session):
        """Two production rows have a start_date typo later than their end_date
        (e.g. start 3012). Labeling those 'not started' surfaces the data error
        rather than claiming a bogus expiry date."""
        project = make_project(session)
        pc = make_project_contract(
            session, project=project,
            start_date=_ahead(900), end_date=_ago(400))

        assert pc.contract.is_active is False
        assert pc.contract.is_future is True

    def test_as_of_reclassifies_expired_contract(self, session):
        project = make_project(session)
        end = _ago(30)
        pc = make_project_contract(
            session, project=project, start_date=_ago(400), end_date=end)

        assert pc.contract.is_active_at(end - timedelta(days=1)) is True
        assert pc.contract.is_active_at() is False


class TestOrganizationsCurrentFirst:

    def test_current_sorts_ahead_of_ended_and_nothing_dropped(self, session):
        project = make_project(session)
        ended = make_project_organization(
            session, project=project, start_date=_ago(800), end_date=_ago(30))
        current = make_project_organization(
            session, project=project, start_date=_ago(100), end_date=None)

        assert project.organizations_current_first() == [current, ended]

    def test_dissolved_organization_is_flagged_independently(self, session):
        """37 active projects hold an OPEN link to a dissolved org — the link
        is current while the organization itself is not."""
        project = make_project(session)
        org = make_organization(session)
        org.active = False
        session.flush()
        po = make_project_organization(
            session, project=project, organization=org, end_date=None)

        assert po.is_active is True
        assert po.organization.is_active is False


class TestContractMixinSwap:
    """Contract now derives its date window from DateRangeMixin."""

    def test_contract_uses_date_range_mixin(self):
        from sam.base import DateRangeMixin
        from sam.projects.contracts import Contract

        assert DateRangeMixin in Contract.__mro__
        cols = {c.name for c in Contract.__table__.columns}
        assert {'start_date', 'end_date'} <= cols

    def test_is_active_sql_expression_filters(self, session):
        """The hybrid must still work in a filter() after the swap."""
        from sam.projects.contracts import Contract

        project = make_project(session)
        current = make_project_contract(
            session, project=project, end_date=_ahead(400))
        make_project_contract(session, project=project, end_date=_ago(30))

        ids = [pc.contract_id for pc in project.contracts]
        active = session.query(Contract).filter(
            Contract.contract_id.in_(ids), Contract.is_active).all()
        assert [c.contract_id for c in active] == [current.contract_id]

    def test_end_date_validator_still_normalizes(self, session):
        """DateRangeMixin carries the same normalize_end_date validator the
        model used to declare by hand — 23:59:59 convention preserved."""
        project = make_project(session)
        pc = make_project_contract(
            session, project=project,
            end_date=datetime(2030, 6, 30, 0, 0, 0))

        assert (pc.contract.end_date.hour,
                pc.contract.end_date.minute,
                pc.contract.end_date.second) == (23, 59, 59)


def _project_with_mixed_contracts(session):
    """Any project holding both a current and an expired contract link.

    Chosen by shape, not projcode: the obfuscated snapshot preserves contract
    date windows but rewrites contract numbers, and projcodes may churn.
    """
    from sam.projects.contracts import Contract, ProjectContract
    from sam.projects.projects import Project

    has_current = session.query(ProjectContract).join(Contract).filter(
        ProjectContract.project_id == Project.project_id,
        Contract.is_active).exists()
    has_expired = session.query(ProjectContract).join(Contract).filter(
        ProjectContract.project_id == Project.project_id,
        ~Contract.is_active).exists()

    row = session.query(Project.projcode).filter(
        Project.is_active, has_current, has_expired).first()
    return row[0] if row else None


class TestProjectCardRendering:
    """Render smoke on snapshot rows."""

    def test_expired_contracts_are_listed_and_badged(self, session, auth_client):
        """A project with both current and expired contracts shows all of them,
        with the lapsed ones badged rather than dropped."""
        projcode = _project_with_mixed_contracts(session)
        if projcode is None:
            pytest.skip('snapshot has no project mixing current/expired contracts')

        resp = auth_client.get(f'/admin/project/{projcode}')
        assert resp.status_code == 200
        assert b'Contracts' in resp.data
        assert b'expired' in resp.data

    def test_current_contract_renders_before_first_expired_badge(
            self, session, auth_client):
        """Ordering is observable in the markup: the current contract's row
        precedes the first `expired` badge."""
        from sam.projects.projects import Project

        projcode = _project_with_mixed_contracts(session)
        if projcode is None:
            pytest.skip('snapshot has no project mixing current/expired contracts')

        project = Project.get_by_projcode(session, projcode)
        current = [pc for pc in project.contracts_current_first()
                   if pc.contract.is_active][0]

        resp = auth_client.get(f'/admin/project/{projcode}')
        assert resp.status_code == 200

        # Scope to the Contracts *rows* only. Two decoys otherwise: the
        # resources table further up the page has its own `expired` badge, and
        # the g_contract_status popover between the label and the rows spells
        # out the badge vocabulary. `text-detail` opens the row container.
        block = (resp.data
                 .split(b'Contracts:', 1)[1]
                 .split(b'text-detail', 1)[1]
                 .split(b'Directories', 1)[0])
        assert block.index(current.contract.contract_number.encode()) < \
            block.index(b'expired')
