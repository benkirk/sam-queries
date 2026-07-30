"""Tests for User.active_/former_institutions() and their organization twins,
plus the user-card rendering gate.

Motivating case: `krasting` is an active, unlocked user whose only
`user_institution` row ended 2026-07-03. The card used to render it as his
current institution because it iterated the relationship unfiltered.

Model-layer tests build a fresh affiliation graph with factories (Layer 2).
The HTTP tests follow the house convention (auth/render smoke only) and rely
solely on committed snapshot rows — Flask routes read a separate db.session
connection and cannot see factory rows (see test_user_former_projects.py).
"""
import pytest
from datetime import datetime, timedelta

from factories import (
    make_institution,
    make_organization,
    make_user,
    make_user_institution,
    make_user_organization,
)

pytestmark = pytest.mark.unit

INST_LABEL = 'Former Institution'
ORG_LABEL = 'Former Organization'
EMPTY_LABEL = 'No current affiliation'


def _ago(days):
    return datetime.now() - timedelta(days=days)


def _ahead(days):
    return datetime.now() + timedelta(days=days)


class TestActiveAndFormerInstitutions:
    """Bucket semantics for UserInstitution."""

    def test_open_ended_affiliation_is_current(self, session):
        user = make_user(session)
        ui = make_user_institution(session, user=user, end_date=None)

        assert user.active_institutions() == [ui]
        assert user.former_institutions() == []

    def test_ended_affiliation_is_former(self, session):
        """The krasting shape: sole affiliation, already ended."""
        user = make_user(session)
        ui = make_user_institution(
            session, user=user, start_date=_ago(400), end_date=_ago(30))

        assert user.active_institutions() == []
        assert user.former_institutions() == [ui]

    def test_future_dated_affiliation_is_not_current(self, session):
        user = make_user(session)
        ui = make_user_institution(
            session, user=user, start_date=_ahead(30), end_date=_ahead(400))

        assert user.active_institutions() == []
        assert user.former_institutions() == [ui]
        assert ui.is_future is True

    def test_reaffiliation_appears_only_in_current(self, session):
        """Same institution ended once and re-opened must not show as both.

        402 organization and 38 institution rows in production have this shape.
        """
        user = make_user(session)
        inst = make_institution(session)
        make_user_institution(
            session, user=user, institution=inst,
            start_date=_ago(800), end_date=_ago(400))
        open_row = make_user_institution(
            session, user=user, institution=inst,
            start_date=_ago(100), end_date=None)

        assert user.active_institutions() == [open_row]
        assert user.former_institutions() == []

    def test_current_deduped_on_institution_keeping_newest(self, session):
        """Overlapping open rows for one institution collapse to the newest."""
        user = make_user(session)
        inst = make_institution(session)
        make_user_institution(
            session, user=user, institution=inst, start_date=_ago(500))
        newer = make_user_institution(
            session, user=user, institution=inst, start_date=_ago(10))

        assert user.active_institutions() == [newer]

    def test_former_sorted_most_recently_ended_first(self, session):
        user = make_user(session)
        oldest = make_user_institution(
            session, user=user, start_date=_ago(900), end_date=_ago(700))
        middle = make_user_institution(
            session, user=user, start_date=_ago(700), end_date=_ago(300))
        newest = make_user_institution(
            session, user=user, start_date=_ago(300), end_date=_ago(20))

        assert user.former_institutions() == [newest, middle, oldest]

    def test_current_sorted_newest_start_first(self, session):
        user = make_user(session)
        older = make_user_institution(session, user=user, start_date=_ago(500))
        newer = make_user_institution(session, user=user, start_date=_ago(5))

        assert user.active_institutions() == [newer, older]

    def test_buckets_are_disjoint_and_cover_every_row(self, session):
        user = make_user(session)
        make_user_institution(session, user=user, end_date=None)
        make_user_institution(
            session, user=user, start_date=_ago(400), end_date=_ago(30))

        current = user.active_institutions()
        former = user.former_institutions()
        assert not set(current) & set(former)
        assert len(current) + len(former) == len(user.institutions)

    def test_as_of_reclassifies_ended_affiliation(self, session):
        """Before its end_date the affiliation is current, not former."""
        user = make_user(session)
        end = _ago(30)
        ui = make_user_institution(
            session, user=user, start_date=_ago(400), end_date=end)

        as_of = end - timedelta(days=1)
        assert user.active_institutions(as_of=as_of) == [ui]
        assert user.former_institutions(as_of=as_of) == []


class TestActiveAndFormerOrganizations:
    """The organization twin — same helper, different relationship."""

    def test_open_ended_is_current_and_ended_is_former(self, session):
        user = make_user(session)
        current = make_user_organization(session, user=user, end_date=None)
        ended = make_user_organization(
            session, user=user, start_date=_ago(400), end_date=_ago(30))

        assert user.active_organizations() == [current]
        assert user.former_organizations() == [ended]

    def test_current_deduped_on_organization(self, session):
        """1,710 production users hold the same org active twice — the card's
        old `unique_orgs` dedupe must survive in the model helper."""
        user = make_user(session)
        org = make_organization(session)
        make_user_organization(
            session, user=user, organization=org, start_date=_ago(500))
        newer = make_user_organization(
            session, user=user, organization=org, start_date=_ago(10))

        assert user.active_organizations() == [newer]

    def test_reaffiliation_appears_only_in_current(self, session):
        user = make_user(session)
        org = make_organization(session)
        make_user_organization(
            session, user=user, organization=org,
            start_date=_ago(800), end_date=_ago(400))
        open_row = make_user_organization(
            session, user=user, organization=org, start_date=_ago(100))

        assert user.active_organizations() == [open_row]
        assert user.former_organizations() == []


class TestIsFutureHybrid:
    """DateRangeMixin.is_future — the discriminator display code needs."""

    def test_not_started_row_is_future(self, session):
        user = make_user(session)
        ui = make_user_institution(
            session, user=user, start_date=_ahead(10), end_date=_ahead(400))
        assert ui.is_future is True
        assert ui.is_active is False

    def test_ended_row_is_not_future(self, session):
        user = make_user(session)
        ui = make_user_institution(
            session, user=user, start_date=_ago(400), end_date=_ago(10))
        assert ui.is_future is False
        assert ui.is_active is False

    def test_current_row_is_not_future(self, session):
        user = make_user(session)
        ui = make_user_institution(session, user=user)
        assert ui.is_future is False
        assert ui.is_active is True

    def test_sql_expression_filters(self, session):
        """The hybrid must work in a filter(), not just in Python."""
        from sam.core.organizations import UserInstitution

        user = make_user(session)
        make_user_institution(
            session, user=user, start_date=_ahead(10), end_date=_ahead(400))
        make_user_institution(session, user=user)

        future_rows = session.query(UserInstitution).filter(
            UserInstitution.user_id == user.user_id,
            UserInstitution.is_future,
        ).all()
        assert len(future_rows) == 1


def _user_with_only_lapsed_institutions(session):
    """Any active snapshot user whose institution affiliations have ALL ended.

    This is the `krasting` shape. Selected by structural shape rather than by
    username because the obfuscated snapshot rewrites usernames to
    `user_<hex>` (only `benkirk` is preserved), so a hardcoded name would not
    survive a fixture refresh.
    """
    from sam.core.organizations import UserInstitution
    from sam.core.users import User

    has_any = session.query(UserInstitution).filter(
        UserInstitution.user_id == User.user_id).exists()
    has_active = session.query(UserInstitution).filter(
        UserInstitution.user_id == User.user_id,
        UserInstitution.is_active).exists()

    row = session.query(User.username).filter(
        User.is_active, has_any, ~has_active).first()
    return row[0] if row else None


class TestUserCardAffiliationRendering:
    """Render/authz smoke for the card blocks (snapshot rows only)."""

    def test_only_lapsed_affiliations_show_empty_state_and_former_block(
            self, session, auth_client):
        """The krasting case: an operator sees the empty state plus the Former
        block, instead of a lapsed affiliation posing as current."""
        username = _user_with_only_lapsed_institutions(session)
        if username is None:
            pytest.skip('snapshot has no user whose institutions all lapsed')

        resp = auth_client.get(f'/admin/user/{username}')
        assert resp.status_code == 200
        assert INST_LABEL.encode() in resp.data
        assert EMPTY_LABEL.encode() in resp.data
        # The glossary popover ships with the Former block.
        assert b'ended' in resp.data

    def test_admin_user_card_renders_benkirk(self, auth_client):
        resp = auth_client.get('/admin/user/benkirk')
        assert resp.status_code == 200
        # Former-projects section must keep working — it is gated separately.
        assert b'Active Projects' in resp.data

    def test_own_info_page_shows_history_via_is_self(self, auth_client):
        """/user/info renders with is_admin=False but is_self=True, so a user
        can see their own affiliation history without elevated permissions."""
        resp = auth_client.get('/user/info')
        assert resp.status_code == 200
        # The operator-only former-PROJECTS block must still be absent.
        assert b'Inactive / Former Projects' not in resp.data
