"""sam.queries.heuv: the legacy HEUV rules, one scenario per rule (plan HEUV_API_PORT.md §5-§6)."""

from datetime import datetime, timedelta

import pytest

from sam.queries import heuv as q
from sam.resources.resources import ResourceShell, ResourceType
from sam.core.users import UserResourceHome
from tests.factories import (
    make_access_branch, make_account, make_account_user, make_adhoc_group,
    make_adhoc_system_account_entry, make_allocation, make_comp_charge_summary,
    make_default_project, make_project, make_queue, make_resource, make_resource_type,
    make_user, make_wallclock_exemption,
)

NOW = datetime.now().replace(microsecond=0)
YEAR_START = (NOW - timedelta(days=100)).replace(hour=0, minute=0, second=0)
YEAR_END = (NOW + timedelta(days=264)).replace(hour=23, minute=59, second=59)


def _rtype(session, name):
    return (session.query(ResourceType).filter_by(resource_type=name).first()
            or make_resource_type(session, resource_type=name))


def _resource(session, kind='HPC', **kw):
    return make_resource(session, resource_type=_rtype(session, kind),
                         commission_date=NOW - timedelta(days=400), **kw)


# --- lookups ------------------------------------------------------------------

def test_lookups_ignore_case(session):
    user = make_user(session)
    project = make_project(session)
    res = _resource(session)
    assert q.find_user(session, user.username.upper()) is user
    assert q.find_project(session, project.projcode.lower()) is project
    assert q.find_resource(session, res.resource_name.lower()) is res
    assert q.find_user(session, 'nosuchuserxx') is None


def test_legacy_full_name_prefers_nickname_and_keeps_middle(session):
    user = make_user(session, first_name='Benjamin', last_name='Kirk', nickname='Ben', middle_name='Shelton')
    assert user.legacy_full_name == 'Ben Shelton Kirk'
    user.nickname = '  '
    assert user.legacy_full_name == 'Benjamin Shelton Kirk'


# --- user routes ----------------------------------------------------------------

def test_default_projects_sorted_by_resource(session):
    user = make_user(session)
    project = make_project(session)
    r_b, r_a = _resource(session, resource_name='zz_heuv_b'), _resource(session, resource_name='aa_heuv_a')
    make_default_project(session, user, project, r_b)
    make_default_project(session, user, project, r_a)
    rows = q.default_projects(user)
    assert [r['resource_name'] for r in rows] == ['aa_heuv_a', 'zz_heuv_b']
    assert rows[0] == dict(username=user.username, resource_name='aa_heuv_a', projcode=project.projcode)


def test_groups_put_ncar_first_and_flag_project_groups(session):
    user = make_user(session, primary_gid=1000)
    group = make_adhoc_group(session)
    make_adhoc_system_account_entry(session, group, user.username, 'hpc')
    rows = q.user_groups(session, user)
    assert rows[0] == dict(username=user.username, group_name='ncar', unix_gid=1000, primary=True,
                           project=False, projcode=None)
    assert [r['group_name'] for r in rows[1:]] == sorted(r['group_name'] for r in rows[1:])


def test_inactive_user_gets_ncar_only(session):
    user = make_user(session, active=False)
    group = make_adhoc_group(session)
    make_adhoc_system_account_entry(session, group, user.username, 'hpc')
    assert [r['group_name'] for r in q.user_groups(session, user)] == ['ncar']


def test_access_uses_overrides_and_branch_named_resources(session):
    user = make_user(session)
    login = _resource(session, resource_name='heuvlogin')
    bash = ResourceShell(resource_id=login.resource_id, shell_name='bash', path='/bin/bash')
    session.add(bash)
    session.flush()
    login.default_resource_shell_id = login.default_resource_shell_id or bash.resource_shell_id
    login.default_home_dir_base = '/glade/u/home'
    compute = _resource(session)
    make_access_branch(session, 'heuvlogin', resources=[compute])
    account = make_account(session, resource=compute)
    make_account_user(session, account, user)
    assert q.user_access(session, user) == [dict(
        username=user.username, resource_name='heuvlogin', resource_type='HPC',
        home_directory=f'/glade/u/home/{user.username}', shell_name='bash')]
    session.add(UserResourceHome(user_id=user.user_id, resource_id=login.resource_id, home_directory='/x/y'))
    session.flush()
    session.expire(user, ['resource_homes'])
    assert q.user_access(session, user)[0]['home_directory'] == '/x/y'


def test_user_without_configurable_membership_has_no_access(session):
    assert q.user_access(session, make_user(session)) == []


def test_assigned_projects_filter_and_group(session):
    user = make_user(session)
    project = make_project(session)
    limited = make_account(session, project=project, resource=_resource(session, resource_name='heuv_b'))
    limited.first_threshold = 50
    plain = make_account(session, project=project, resource=_resource(session, resource_name='heuv_a'))
    make_account_user(session, limited, user)
    make_account_user(session, plain, user)
    make_account_user(session, plain, user, end_date=NOW - timedelta(days=2))   # ended: not listed
    everything = q.assigned_projects(session, user)
    assert [r['resource_name'] for r in everything[0]['resource_assignments']] == ['heuv_a', 'heuv_b']
    assert [r['resource_name'] for r in q.assigned_projects(session, user, True)[0]['resource_assignments']] == ['heuv_b']
    assert [r['resource_name'] for r in q.assigned_projects(session, user, False)[0]['resource_assignments']] == ['heuv_a']
    assert q.assigned_projects(session, user, resource=limited.resource)[0]['resource_assignments'][0]['resource_name'] == 'heuv_b'
    by_resource = q.assigned_resources(session, user)
    assert [r['resource_name'] for r in by_resource] == ['heuv_a', 'heuv_b']
    assert by_resource[0]['project_assignments'][0]['projcode'] == project.projcode


def test_primary_flag_compares_gids(session):
    project = make_project(session)
    project.unix_gid = 777_001
    user = make_user(session, primary_gid=777_001)
    make_account_user(session, make_account(session, project=project), user)
    assert q.assigned_projects(session, user)[0]['primary'] is True


def test_role_logins_lists_active_contacts_case_insensitively(session):
    user = make_user(session, upid=True)
    make_user(session, username='Zrole_heuv', contact_person_upid=user.upid)
    make_user(session, username='arole_heuv', contact_person_upid=user.upid)
    make_user(session, username='mrole_heuv', contact_person_upid=user.upid, active=False)
    assert q.role_logins(session, user) == [user.username, 'arole_heuv', 'Zrole_heuv']


def test_wallclock_groups_and_filters(session):
    user = make_user(session)
    queue = make_queue(session, queue_name='main')
    old = make_wallclock_exemption(session, user=user, queue=queue, start_date=NOW - timedelta(days=400),
                                   end_date=NOW - timedelta(days=300), time_limit_hours=47.5)
    make_wallclock_exemption(session, user=user, queue=queue, start_date=NOW - timedelta(days=1),
                             end_date=NOW + timedelta(days=1))
    session.expire(user, ['wallclock_exemptions'])
    out = q.wallclock_exemptions(user, 'AsSent')
    assert out['username'] == 'AsSent'
    exemptions = out['resources'][0]['queues'][0]['exemptions']
    assert [e['active'] for e in exemptions] == [True, False]
    assert exemptions[1]['hour_limit'] == 48 and exemptions[1]['start_date'] == old.start_date
    assert len(q.wallclock_exemptions(user, 'x', active=True)['resources'][0]['queues'][0]['exemptions']) == 1


# --- search / hierarchy / report project ---------------------------------------

def test_search_escapes_wildcards_and_filters_by_member(session):
    project = make_project(session, projcode='HEUV_9001')
    make_project(session, projcode='HEUVA9002')
    user = make_user(session)
    make_account_user(session, make_account(session, project=project), user)
    assert [r['projcode'] for r in q.search_projcodes(session, 'heuv_', None)] == ['HEUV_9001']
    assert [r['projcode'] for r in q.search_projcodes(session, 'HEUV', user.username.upper())] == ['HEUV_9001']
    assert q.search_projcodes(session, 'HEUV', 'nosuchuserxx') == []


def test_hierarchy_from_root_with_active_children_only(session):
    root = make_project(session, projcode='HEUVR001')
    child = make_project(session, projcode='HEUVR003', parent=root)
    make_project(session, projcode='HEUVR002', parent=root, active=False)
    tree = q.project_hierarchy(child)
    assert tree['projcode'] == 'HEUVR001' and tree['parent_projcode'] is None
    assert [c['projcode'] for c in tree['children']] == ['HEUVR003']
    assert tree['children'][0]['root_projcode'] == 'HEUVR001'


def test_report_project_rules(session):
    root = make_project(session)
    child = make_project(session, parent=root, active=False)
    res = _resource(session)
    account = make_account(session, project=child, resource=res)
    make_allocation(session, account=account, start_date=YEAR_START, end_date=YEAR_END)
    make_allocation(session, account=account, start_date=NOW + timedelta(days=300),
                    end_date=NOW + timedelta(days=600))                         # future: dropped
    res.decommission_date = NOW + timedelta(days=10)
    session.flush()
    out = q.report_project(child, now=NOW)
    assert out['hierarchical'] is True and q.report_project(root, now=NOW)['hierarchical'] is True
    [acct] = out['accounts']
    [alloc] = acct['allocations']
    assert alloc['end_date'] == res.decommission_date and alloc['active'] is True
    assert acct['threshold_limited'] is False


# --- access ----------------------------------------------------------------------

def test_accessible_resources_sort_shells(session):
    res = _resource(session, resource_name='heuvbranch')
    for name in ('zsh', 'bash'):
        session.add(ResourceShell(resource_id=res.resource_id, shell_name=name, path=f'/bin/{name}'))
    session.flush()
    make_access_branch(session, 'HEUVBRANCH')
    [row] = q.accessible_resources(session, 'heuvbranch')
    assert [s['shell_name'] for s in row['shells']] == ['bash', 'zsh'] and row['login'] is True
    assert q.accessible_resources(session, 'nosuch') == []


# --- usage report ----------------------------------------------------------------

def _charged(session, project, resource, amount=1000.0, charges=0.0, start=YEAR_START, end=YEAR_END):
    account = make_account(session, project=project, resource=resource)
    account.creation_time = NOW - timedelta(days=500)   # the server stamp can trail NOW
    make_allocation(session, account=account, amount=amount, start_date=start, end_date=end)
    if charges:
        make_comp_charge_summary(session, account=account, charges=charges, activity_date=NOW - timedelta(days=5))
    return account


def test_usage_report_charges_status_and_thresholds(session):
    project = make_project(session)
    hpc = _resource(session, resource_name='heuv_hpc')
    _charged(session, project, hpc, amount=365_000.0, charges=1234.4)
    [row] = q.project_usage_report(session, project, now=NOW)['account_reports']
    assert row['resource_usage_type'] == q.ACCRUED_CHARGES and row['status'] == 'Normal'
    assert (row['total_charges'], row['adjustments'], row['balance']) == (1234, 0, 365_000 - 1234)
    assert row['threshold_limited'] is True
    divisor = (YEAR_END.date() - YEAR_START.date()).days
    first = row['threshold_reports'][0]
    assert first['allocation_amount'] == q.st.java_round(30 * 365_000 / divisor)
    assert first['charges'] == 1234 and first['percent_limit'] is None and first['label'] == '30-Day'


def test_usage_report_overspent_and_exempt(session):
    project = make_project(session)
    _charged(session, project, _resource(session), amount=100.0, charges=150.0)
    assert q.project_usage_report(session, project, now=NOW)['account_reports'][0]['status'] == 'Overspent'
    project.charging_exempt = True
    assert q.project_usage_report(session, project, now=NOW)['account_reports'][0]['status'] == 'Normal'


def test_usage_report_expired_row_nulls(session):
    project = make_project(session)
    _charged(session, project, _resource(session), start=NOW - timedelta(days=400), end=NOW - timedelta(days=40))
    [row] = q.project_usage_report(session, project, now=NOW)['account_reports']
    assert row['status'] == 'Expired'
    assert (row['allocation_amount'], row['balance'], row['threshold_reports'], row['threshold_limited']) == \
        (None, None, [], False)


def test_child_inherits_no_account_from_parent(session):
    parent = make_project(session)
    child = make_project(session, parent=parent)
    _charged(session, child, _resource(session))
    assert q.project_usage_report(session, child, now=NOW)['account_reports'][0]['status'] == 'No Account'


def test_one_day_allocation_emits_null_threshold(session):
    project = make_project(session)
    day = NOW.replace(hour=0, minute=0, second=0)
    _charged(session, project, _resource(session), start=day, end=day.replace(hour=23, minute=59, second=59))
    first = q.project_usage_report(session, project, now=NOW)['account_reports'][0]['threshold_reports'][0]
    assert first['allocation_amount'] is None and first['percent_usage'] is None


def test_data_holdings_rows_come_first(session):
    project = make_project(session)
    _charged(session, project, _resource(session, resource_name='aa_heuv_hpc'))
    _charged(session, project, _resource(session, kind='DISK', resource_name='zz_heuv_disk'))
    rows = q.project_usage_report(session, project, now=NOW)['account_reports']
    assert [r['resource_usage_type'] for r in rows] == [q.DATA_HOLDINGS, q.ACCRUED_CHARGES]
    assert set(rows[0]) >= {'total_holdings', 'number_of_files'} and 'threshold_reports' not in rows[0]


@pytest.mark.parametrize('when', [datetime(2026, 1, 15, 12), datetime(2026, 7, 15, 12)])
def test_usernames_are_day_granular(session, when):
    project = make_project(session)
    account = _charged(session, project, _resource(session), start=when - timedelta(days=30),
                       end=when + timedelta(days=30))
    account.creation_time = when - timedelta(days=60)
    early = make_user(session, username='aaa_heuv_' + str(when.month))
    make_account_user(session, account, early, start_date=when - timedelta(days=5),
                      end_date=when.replace(hour=0, minute=0, second=1))      # ended this morning: still listed
    names = q.project_usage_report(session, project, now=when)['account_reports'][0]['usernames']
    assert early.username in names


@pytest.mark.parametrize('first, second, limits', [(50, 80, [50, 80]), (50, None, [None, None])])
def test_percent_limit_needs_both_thresholds(session, first, second, limits):
    project = make_project(session)
    account = _charged(session, project, _resource(session))
    account.first_threshold, account.second_threshold = first, second
    reports = q.project_usage_report(session, project, now=NOW)['account_reports'][0]['threshold_reports']
    assert [r['percent_limit'] for r in reports] == limits
