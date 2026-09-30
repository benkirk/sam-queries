"""The SAM-side inputs to the Last seen review: the user directory and project counts."""

from sam.queries.users import count_active_projects_by_username, get_user_directory
from factories.core import make_user
from factories.projects import make_project


def test_directory_honors_active_only(session):
    live = make_user(session, first_name='Ada', last_name='Lovelace')
    gone = make_user(session, active=False)
    assert get_user_directory(session)[live.username] == ('Ada Lovelace', True, False)
    assert gone.username not in get_user_directory(session)
    assert get_user_directory(session, active_only=False)[gone.username][1] is False


def test_project_counts_match_active_projects(session, multi_project_user):
    u = multi_project_user
    got = count_active_projects_by_username(session, [u.username])
    assert got.get(u.username, 0) == len(u.active_projects())


def test_lead_counts_and_inactive_projects_do_not(session):
    lead = make_user(session)
    idle = make_user(session)
    make_project(session, lead=lead)
    make_project(session, lead=lead, active=False)
    got = count_active_projects_by_username(session, [lead.username, idle.username])
    assert got == {lead.username: 1}
    assert count_active_projects_by_username(session, []) == {}


def test_primary_email_prefers_the_primary_row(session, multi_project_user):
    from sam.queries.users import get_primary_emails
    u = multi_project_user
    assert get_primary_emails(session, [u.username]).get(u.username) == u.primary_email


def test_chunked_counts_agree(session, multi_project_user, monkeypatch):
    import sam.queries.users as users
    names = [multi_project_user.username, 'benkirk']
    whole = count_active_projects_by_username(session, names)
    monkeypatch.setattr(users, '_IN_CHUNK', 1)
    assert count_active_projects_by_username(session, names) == whole


def test_directory_limited_to_usernames(session, monkeypatch):
    import sam.queries.users as users
    a, b, c = (make_user(session) for _ in range(3))
    monkeypatch.setattr(users, '_IN_CHUNK', 1)
    got = get_user_directory(session, active_only=False, usernames=[a.username, b.username])
    assert set(got) == {a.username, b.username}
    assert get_user_directory(session, usernames=[]) == {}


def _member(session, project, user, *, ended=False):
    from datetime import datetime, timedelta
    from sam.accounting.accounts import AccountUser
    from factories.projects import make_account
    account = project.accounts[0] if project.accounts else make_account(session, project=project)
    now = datetime.now()
    session.add(AccountUser(account_id=account.account_id, user_id=user.user_id,
                            start_date=now - timedelta(days=30),
                            end_date=now - timedelta(days=1) if ended else None))
    session.flush()


class TestAbandonedUsers:
    """``get_abandoned_usernames``: every active project in the expired set."""

    def _world(self, session):
        from factories.projects import make_account
        w = {name: make_user(session) for name in
             ('only', 'both_expired', 'mixed', 'ended_elsewhere', 'inactive_elsewhere', 'lead_only')}
        expired = make_project(session)
        make_account(session, project=expired)
        expired_rowless = make_project(session, lead=w['lead_only'])   # no account at all
        live = make_project(session)
        dead = make_project(session, active=False)
        _member(session, expired, w['only'])
        _member(session, expired, w['both_expired'])
        _member(session, expired, w['mixed'])
        _member(session, live, w['mixed'])
        _member(session, expired, w['ended_elsewhere'])
        _member(session, live, w['ended_elsewhere'], ended=True)
        _member(session, expired, w['inactive_elsewhere'])
        _member(session, dead, w['inactive_elsewhere'])
        admin_rowless = make_user(session)
        expired_rowless.project_admin_user_id = admin_rowless.user_id
        session.flush()
        w['both_expired_admin'] = admin_rowless
        return w, [expired, expired_rowless], live

    def test_the_rule(self, session):
        from sam.queries.users import get_abandoned_usernames
        w, expired, _live = self._world(session)
        e, r = expired[0].projcode, expired[1].projcode
        got = get_abandoned_usernames(session, expired)
        assert got == {
            expired[0].lead.username: [e],
            w['only'].username: [e],
            w['both_expired'].username: [e],
            w['ended_elsewhere'].username: [e],
            w['inactive_elsewhere'].username: [e],
            w['lead_only'].username: [r],
            w['both_expired_admin'].username: [r],
        }
        assert w['mixed'].username not in got

    def test_same_rule_as_active_projects(self, session, multi_project_user):
        from sam.queries.users import get_active_projcodes_by_user
        w, expired, live = self._world(session)
        people = [*w.values(), multi_project_user]
        ids = {p.project_id for u in people for p in u.active_projects()}
        got = get_active_projcodes_by_user(session, ids)
        for u in people:
            want = {p.projcode for p in u.active_projects()}
            assert got.get(u.user_id, (u.username, set())) == (u.username, want), u.username

    def test_no_projects_no_query(self, session):
        from sam.queries.users import get_abandoned_usernames, get_active_projcodes_by_user
        assert get_active_projcodes_by_user(session, []) == {}
        assert get_abandoned_usernames(session, []) == {}
