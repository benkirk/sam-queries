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
