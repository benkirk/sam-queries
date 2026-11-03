"""``sam.manage.groups``: opening and closing a mask, its guards, and the entry rows it writes."""
from datetime import datetime, timedelta

import pytest

from factories import (make_adhoc_group, make_adhoc_group_tag, make_adhoc_system_account_entry,
                       make_project, make_user, next_int)
from sam import UnixGroupMask
from sam.manage.groups import (MASK_BATCH_MAX, MaskError, check_mask, close_mask, open_mask,
                               open_masks as open_batch, parse_usernames, resolve_target)
from sam.queries.group_masks import (batch_siblings_open, open_masks, project_mask_times,
                                     project_masks)

T0 = datetime(2026, 11, 2, 9, 0)


def _group(session, tags=('hpc', 'hpc-data', 'glade'), **kw):
    group = make_adhoc_group(session, **kw)
    for tag in tags:
        make_adhoc_group_tag(session, group, tag)
    session.expire(group, ['tags', 'system_accounts'])
    return group


def _rows(group):
    return sorted((e.access_branch_name, e.username) for e in group.system_accounts)


def _project(session):
    project = make_project(session)
    project.unix_gid = 96_000_000 + next_int('mask_gid')
    session.flush()
    return project


class TestOpenAdhoc:
    def test_add_materializes_on_every_branch_the_tags_name(self, session):
        group, user = _group(session), make_user(session)
        mask = open_mask(session, gid=group.unix_gid, username=user.username.upper(), op='add',
                         requested_by='op', note=' why ', now=T0)
        assert (mask.kind, mask.username, mask.note) == ('adhoc', user.username, 'why')
        assert _rows(group) == [('hpc', user.username), ('hpc-data', user.username)]
        assert open_masks(session, now=T0, gids=[group.unix_gid]) == {
            group.unix_gid: ({user.username.lower()}, set())}

    def test_remove_deletes_the_rows_on_every_branch(self, session):
        group, user = _group(session), make_user(session)
        for branch in ('hpc', 'hpc-data', 'hpc-dev'):
            make_adhoc_system_account_entry(session, group, user.username, branch)
        session.expire(group, ['system_accounts'])
        open_mask(session, gid=group.unix_gid, username=user.username, op='remove', requested_by='op', now=T0)
        assert _rows(group) == []

    def test_an_add_over_an_existing_row_adds_only_the_missing_branch(self, session):
        group, user = _group(session), make_user(session)
        make_adhoc_system_account_entry(session, group, user.username, 'hpc')
        session.expire(group, ['system_accounts'])
        open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        assert _rows(group) == [('hpc', user.username), ('hpc-data', user.username)]

    def test_unknown_gid_and_user(self, session):
        group = _group(session)
        with pytest.raises(MaskError, match='No adhoc group or project'):
            open_mask(session, gid=-424242, username='x', op='add', requested_by='op', now=T0)
        with pytest.raises(MaskError, match='No user'):
            open_mask(session, gid=group.unix_gid, username='nobody-here', op='add', requested_by='op', now=T0)

    def test_inactive_or_branchless_group(self, session):
        user = make_user(session)
        inactive = _group(session, active=False)
        with pytest.raises(MaskError, match='inactive'):
            open_mask(session, gid=inactive.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        branchless = _group(session, tags=('glade',))
        with pytest.raises(MaskError, match='no access branch'):
            open_mask(session, gid=branchless.unix_gid, username=user.username, op='add', requested_by='op', now=T0)

    def test_a_role_subject_group_needs_the_flag(self, session):
        group, user = _group(session), make_user(session)
        protected = frozenset({group.group_name})
        with pytest.raises(MaskError, match='MANAGE_ROLES'):
            open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op',
                      now=T0, protected_groups=protected)
        open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op',
                  now=T0, protected_groups=protected, may_touch_protected=True)
        assert len(_rows(group)) == 2

    def test_remove_of_the_primary_group_is_refused(self, session):
        group, user = _group(session), make_user(session)
        user.primary_gid = group.unix_gid
        session.flush()
        with pytest.raises(MaskError, match='primary group'):
            open_mask(session, gid=group.unix_gid, username=user.username, op='remove', requested_by='op', now=T0)

    def test_a_long_username_is_refused(self, session):
        group, user = _group(session), make_user(session, username='a' * 13 + str(next_int('mask_u')))
        with pytest.raises(MaskError, match='longer than an entry'):
            open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)

    def test_one_open_row_per_member(self, session):
        group, user = _group(session), make_user(session)
        open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        with pytest.raises(MaskError, match='already stands'):
            open_mask(session, gid=group.unix_gid, username=user.username, op='remove', requested_by='op', now=T0)

    def test_an_overdue_row_is_expired_on_the_way(self, session):
        group, user = _group(session), make_user(session)
        first = open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op',
                          now=T0, expires_at=T0 + timedelta(days=1))
        second = open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op',
                           now=T0 + timedelta(days=2))
        assert first.resolution == 'expired' and second.is_active
        assert len(_rows(group)) == 2


class TestOpenProject:
    def test_a_project_mask_needs_an_expiry_and_writes_no_rows(self, session):
        project, user = _project(session), make_user(session)
        with pytest.raises(MaskError, match='expiry'):
            open_mask(session, gid=project.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        mask = open_mask(session, gid=project.unix_gid, username=user.username, op='add', requested_by='op',
                         now=T0, expires_at=T0 + timedelta(days=7))
        assert mask.kind == 'project'
        assert resolve_target(session, project.unix_gid).project is project
        found = [m for m in project_masks(session, now=T0) if m.mask_id == mask.mask_id]
        assert found and found[0].projcode == project.projcode and found[0].branches == frozenset()
        assert project_mask_times(session)[project.unix_gid] == T0
        assert project_masks(session, now=T0 + timedelta(days=8)) == [
            m for m in project_masks(session, now=T0 + timedelta(days=8)) if m.mask_id != mask.mask_id]

    def test_an_inactive_project_still_takes_a_mask(self, session):
        project, user = _project(session), make_user(session)
        project.active = False
        session.flush()
        mask = open_mask(session, gid=project.unix_gid, username=user.username, op='add', requested_by='op',
                         now=T0, expires_at=T0 + timedelta(days=1))
        assert mask.mask_id


class TestClose:
    def test_withdrawing_an_add_takes_its_rows_back(self, session):
        group, user = _group(session), make_user(session)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        close_mask(session, mask, resolution='withdrawn', by='op', now=T0)
        assert _rows(group) == [] and mask.state == 'withdrawn'

    def test_withdrawing_a_remove_restores_the_rows(self, session):
        group, user = _group(session), make_user(session)
        make_adhoc_system_account_entry(session, group, user.username, 'hpc')
        session.expire(group, ['system_accounts'])
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='remove', requested_by='op', now=T0)
        assert _rows(group) == []
        close_mask(session, mask, resolution='expired', by='task:unix_group_masks', now=T0)
        assert _rows(group) == [('hpc', user.username), ('hpc-data', user.username)]

    def test_a_confirmed_mask_keeps_the_rows(self, session):
        group, user = _group(session), make_user(session)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        close_mask(session, mask, resolution='confirmed', by='task:unix_group_masks', now=T0)
        assert len(_rows(group)) == 2
        with pytest.raises(MaskError, match='already closed'):
            close_mask(session, mask, resolution='withdrawn', by='op', now=T0)
        assert open_masks(session, now=T0, gids=[group.unix_gid]) == {}


class TestParseUsernames:
    def test_lines_blanks_and_comments(self):
        names, problems = parse_usernames('alice\n\n# a comment\n  Bob  \n')
        assert names == ['alice', 'Bob'] and problems == []

    def test_problems_name_their_lines(self):
        names, problems = parse_usernames('alice\nnot a user\nALICE\n' + 'x' * 36)
        assert names == ['alice']
        assert problems == ['line 2: expected one username', 'line 3: duplicate of line 1',
                            'line 4: expected one username']

    def test_the_cap_and_the_empty_box(self):
        names, problems = parse_usernames('\n'.join(f'u{i}' for i in range(MASK_BATCH_MAX + 1)))
        assert len(names) == MASK_BATCH_MAX + 1 and problems == [
            f'At most {MASK_BATCH_MAX} members at once; open another mask for the rest.']
        assert parse_usernames('# nobody\n') == ([], ['Name at least one member.'])


class TestOpenBatch:
    def test_every_row_names_the_head_and_shares_one_clock(self, session):
        group = _group(session)
        users = [make_user(session) for _ in range(3)]
        masks = open_batch(session, gid=group.unix_gid, usernames=[u.username for u in users],
                           op='add', requested_by='op', now=T0)
        head = masks[0]
        assert [m.username for m in masks] == [u.username for u in users]
        assert {m.batch_id for m in masks} == {head.mask_id} and head.head_id == head.mask_id
        assert {m.creation_time for m in masks} == {T0}
        assert len(_rows(group)) == 6
        assert batch_siblings_open(session, head) == 2
        close_mask(session, masks[1], resolution='withdrawn', by='op', now=T0)
        assert batch_siblings_open(session, head) == 1

    def test_a_single_heads_itself(self, session):
        group, user = _group(session), make_user(session)
        (mask,) = open_batch(session, gid=group.unix_gid, usernames=[user.username], op='add',
                             requested_by='op', now=T0)
        assert mask.batch_id == mask.mask_id and batch_siblings_open(session, mask) == 0

    def test_a_refusal_on_the_second_name_writes_nothing(self, session):
        group, user = _group(session), make_user(session)
        with pytest.raises(MaskError, match='No user named'):
            open_batch(session, gid=group.unix_gid, usernames=[user.username, 'no-such-user-zz'],
                       op='add', requested_by='op', now=T0)
        assert _rows(group) == []
        assert session.query(UnixGroupMask).filter(UnixGroupMask.unix_gid == group.unix_gid).count() == 0

    def test_an_empty_or_oversized_list_is_refused(self, session):
        group = _group(session)
        with pytest.raises(MaskError, match='at least one'):
            open_batch(session, gid=group.unix_gid, usernames=[], op='add', requested_by='op', now=T0)
        with pytest.raises(MaskError, match='At most'):
            open_batch(session, gid=group.unix_gid, usernames=[f'u{i}' for i in range(MASK_BATCH_MAX + 1)],
                       op='add', requested_by='op', now=T0)


class TestCheckMask:
    def test_it_reports_without_writing_and_returns_the_overdue_row(self, session):
        group, user = _group(session), make_user(session)
        target, found, standing = check_mask(session, gid=group.unix_gid, username=user.username,
                                             op='add', now=T0)
        assert (target.kind, target.label, found.username, standing) == (
            'adhoc', f'{group.group_name} (gid {group.unix_gid}, adhoc)', user.username, None)
        assert _rows(group) == []
        overdue = open_mask(session, gid=group.unix_gid, username=user.username, op='add',
                            requested_by='op', expires_at=T0 + timedelta(hours=1), now=T0)
        later = T0 + timedelta(days=1)
        assert check_mask(session, gid=group.unix_gid, username=user.username, op='add',
                          now=later)[2] is overdue
        with pytest.raises(MaskError, match='already stands'):
            check_mask(session, gid=group.unix_gid, username=user.username, op='add', now=T0)
