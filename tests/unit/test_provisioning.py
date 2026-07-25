"""Unit tests for sam.provisioning — host NSS cross-checks.

These are host-independent: the NSS layer (`pwd` / `grp` / `os.getgrouplist`)
is monkeypatched with in-memory fakes, and the SAM user/project objects are
lightweight stand-ins (only the attributes the checks read). Nothing here
touches the database or the real host, so it runs anywhere including CI.
"""
import os
import types
import pytest

from sam import provisioning


# --------------------------------------------------------------- fake objects

def _pw(uid, gid, home='/home/x', shell='/bin/bash'):
    return types.SimpleNamespace(
        pw_uid=uid, pw_gid=gid, pw_dir=home, pw_shell=shell
    )


def _gr(gid, name, members):
    return types.SimpleNamespace(gr_gid=gid, gr_name=name, gr_mem=list(members))


def _user(username, unix_uid):
    return types.SimpleNamespace(username=username, unix_uid=unix_uid)


def _project(projcode, unix_gid, users):
    return types.SimpleNamespace(projcode=projcode, unix_gid=unix_gid, users=users)


@pytest.fixture
def nss(monkeypatch):
    """In-memory NSS. Populate `.passwd` (name→pw) and `.groups` (gid→gr);
    `.grouplist` maps username→set(gids). Missing lookups raise KeyError like
    the real modules, exercising the module's swallowing logic."""
    state = types.SimpleNamespace(passwd={}, groups={}, grouplist={})

    def getpwnam(name):
        try:
            return state.passwd[name]
        except KeyError:
            raise KeyError(name)

    def getgrgid(gid):
        try:
            return state.groups[gid]
        except KeyError:
            raise KeyError(gid)

    def getgrnam(name):
        for gr in state.groups.values():
            if gr.gr_name == name:
                return gr
        raise KeyError(name)

    def getgrouplist(username, pw_gid):
        if username not in state.grouplist:
            raise KeyError(username)
        return list(state.grouplist[username] | {pw_gid})

    monkeypatch.setattr(provisioning.pwd, 'getpwnam', getpwnam)
    monkeypatch.setattr(provisioning.grp, 'getgrgid', getgrgid)
    monkeypatch.setattr(provisioning.grp, 'getgrnam', getgrnam)
    monkeypatch.setattr(provisioning.os, 'getgrouplist', getgrouplist)
    # home_exists check — default everything to "exists" unless a test overrides
    monkeypatch.setattr(provisioning.os.path, 'isdir', lambda p: True)
    return state


# ------------------------------------------------------------ is_provisioned_host

def test_gate_ncar_host(monkeypatch):
    monkeypatch.delenv('SAM_CHECK_PROVISIONING', raising=False)
    monkeypatch.setenv('NCAR_HOST', 'casper')
    assert provisioning.is_provisioned_host() is True


def test_gate_no_ncar_host(monkeypatch):
    monkeypatch.delenv('SAM_CHECK_PROVISIONING', raising=False)
    monkeypatch.delenv('NCAR_HOST', raising=False)
    assert provisioning.is_provisioned_host() is False


@pytest.mark.parametrize('val,expected', [
    ('1', True), ('true', True), ('YES', True),
    ('0', False), ('false', False), ('', False),
])
def test_gate_override_wins(monkeypatch, val, expected):
    # Override beats NCAR_HOST in both directions.
    monkeypatch.setenv('NCAR_HOST', 'casper')
    monkeypatch.setenv('SAM_CHECK_PROVISIONING', val)
    assert provisioning.is_provisioned_host() is expected


# --------------------------------------------------------- check_user_provisioning

def test_user_consistent(nss):
    nss.passwd['jdoe'] = _pw(1001, 500)
    nss.grouplist['jdoe'] = {500, 68283}
    proj = _project('SCSG0001', 68283, [])
    r = provisioning.check_user_provisioning(_user('jdoe', 1001), [proj])
    assert r['recognized'] and r['uid_matches'] and r['shell_ok']
    assert r['home_exists'] and r['missing_project_groups'] == [] and r['ok']


def test_user_not_recognized(nss):
    r = provisioning.check_user_provisioning(_user('ghost', 1), [])
    assert r['recognized'] is False and r['ok'] is False
    assert r['uid'] is None and r['missing_project_groups'] == []


def test_user_uid_mismatch(nss):
    nss.passwd['jdoe'] = _pw(9999, 500)   # host uid ≠ SAM unix_uid
    nss.grouplist['jdoe'] = {500}
    r = provisioning.check_user_provisioning(_user('jdoe', 1001), [])
    assert r['uid'] == 9999 and r['uid_matches'] is False and r['ok'] is False


def test_user_nologin_shell(nss):
    nss.passwd['svc'] = _pw(1001, 500, shell='/sbin/nologin')
    nss.grouplist['svc'] = {500}
    r = provisioning.check_user_provisioning(_user('svc', 1001), [])
    assert r['shell_ok'] is False
    # shell_ok is a soft warning — does not gate ok
    assert r['ok'] is True


def test_user_missing_home(nss, monkeypatch):
    nss.passwd['jdoe'] = _pw(1001, 500, home='/home/gone')
    nss.grouplist['jdoe'] = {500}
    monkeypatch.setattr(provisioning.os.path, 'isdir', lambda p: False)
    r = provisioning.check_user_provisioning(_user('jdoe', 1001), [])
    assert r['home_exists'] is False and r['ok'] is True  # soft warning


def test_user_missing_project_group(nss):
    nss.passwd['jdoe'] = _pw(1001, 500)
    nss.grouplist['jdoe'] = {500}          # NOT in 68283
    proj = _project('SCSG0001', 68283, [])
    r = provisioning.check_user_provisioning(_user('jdoe', 1001), [proj])
    assert r['missing_project_groups'] == [{'projcode': 'SCSG0001', 'unix_gid': 68283}]
    assert r['ok'] is False


def test_user_primary_group_counts(nss):
    # Project group is the user's PRIMARY group — getgrouplist includes it
    # via the pw_gid seed even though it's not a supplementary membership.
    nss.passwd['jdoe'] = _pw(1001, 68283)
    nss.grouplist['jdoe'] = set()          # no supplementary groups
    proj = _project('SCSG0001', 68283, [])
    r = provisioning.check_user_provisioning(_user('jdoe', 1001), [proj])
    assert r['missing_project_groups'] == [] and r['ok']


# ------------------------------------------------------ check_project_provisioning

def test_project_consistent(nss):
    members = [_user('a', 1), _user('b', 2)]
    nss.passwd['a'] = _pw(1, 500)
    nss.passwd['b'] = _pw(2, 500)
    nss.grouplist['a'] = {68283}
    nss.grouplist['b'] = {68283}
    nss.groups[68283] = _gr(68283, 'scsg0001', ['a', 'b'])
    r = provisioning.check_project_provisioning(_project('SCSG0001', 68283, members))
    # case-insensitive name match
    assert r['group_exists'] and r['name_matches'] is True
    assert r['missing_from_group'] == [] and r['extra_in_group'] == [] and r['ok']


def test_project_group_missing(nss):
    r = provisioning.check_project_provisioning(_project('SCSG0001', 68283, []))
    assert r['group_exists'] is False and r['ok'] is False
    assert r['gid'] == 68283


def test_project_member_missing_from_group(nss):
    members = [_user('a', 1), _user('b', 2)]
    nss.passwd['a'] = _pw(1, 500)
    nss.passwd['b'] = _pw(2, 500)
    nss.grouplist['a'] = {68283}
    nss.grouplist['b'] = set()                 # b lacks the project gid
    nss.groups[68283] = _gr(68283, 'scsg0001', ['a'])
    r = provisioning.check_project_provisioning(_project('SCSG0001', 68283, members))
    assert r['missing_from_group'] == ['b'] and r['ok'] is False


def test_project_ghost_member(nss):
    members = [_user('a', 1)]
    nss.passwd['a'] = _pw(1, 500)
    nss.grouplist['a'] = {68283}
    # group lists a stale member 'z' with no active SAM membership
    nss.groups[68283] = _gr(68283, 'scsg0001', ['a', 'z'])
    r = provisioning.check_project_provisioning(_project('SCSG0001', 68283, members))
    assert r['extra_in_group'] == ['z'] and r['ok'] is False


def test_project_name_lookup_fallback(nss):
    # gid unresolvable, but projcode names a real group → found by name.
    members = [_user('a', 1)]
    nss.passwd['a'] = _pw(1, 500)
    nss.grouplist['a'] = {70000}
    nss.groups[70000] = _gr(70000, 'scsg0001', ['a'])
    r = provisioning.check_project_provisioning(_project('SCSG0001', None, members))
    assert r['group_exists'] and r['gid'] == 70000 and r['ok']
