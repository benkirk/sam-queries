"""UnixGroupMask: the row shape, its guards and its close stamp."""
from datetime import datetime, timedelta

import pytest

from factories import make_unix_group_mask
from sam import UnixGroupMask

T0 = datetime(2026, 11, 2, 9, 0)


class TestCreate:
    def test_adhoc_defaults_open_ended(self, session):
        row = make_unix_group_mask(session, unix_gid=-1, username=' alice ', note='  ', when=T0)
        assert row.mask_id and row.kind == 'adhoc' and row.op == 'add'
        assert (row.username, row.note, row.expires_at) == ('alice', None, None)
        assert row.creation_time == T0 and row.is_active and row.is_open_at(T0)
        assert row.state == 'pending'

    def test_project_needs_an_expiry_within_the_cap(self, session):
        with pytest.raises(ValueError, match='expiry'):
            make_unix_group_mask(session, unix_gid=-2, username='a', kind='project', when=T0)
        with pytest.raises(ValueError, match='90 days'):
            make_unix_group_mask(session, unix_gid=-2, username='a', kind='project',
                                 expires_at=T0 + timedelta(days=91), when=T0)
        row = make_unix_group_mask(session, unix_gid=-2, username='a', kind='project',
                                   expires_at=T0 + timedelta(days=7), when=T0)
        assert row.is_open_at(T0 + timedelta(days=6))
        assert not row.is_open_at(T0 + timedelta(days=7))

    def test_a_row_heads_itself_unless_given_a_batch(self, session):
        head = make_unix_group_mask(session, unix_gid=-9, username='a', when=T0)
        assert head.batch_id == head.mask_id == head.head_id
        member = make_unix_group_mask(session, unix_gid=-9, username='b', when=T0,
                                      batch_id=head.mask_id)
        assert member.batch_id == head.mask_id and member.head_id == head.mask_id

    @pytest.mark.parametrize('kwargs', [{'kind': 'other'}, {'op': 'drop'}, {'username': ' '},
                                        {'requested_by': ''},
                                        {'expires_at': T0 - timedelta(hours=1)}])
    def test_validation(self, session, kwargs):
        with pytest.raises(ValueError):
            make_unix_group_mask(session, **{'unix_gid': -3, 'username': 'a', 'when': T0, **kwargs})


class TestUpdateAndClose:
    def test_close_stamps_once(self, session):
        row = make_unix_group_mask(session, unix_gid=-4, username='a', when=T0)
        row.close(resolution='withdrawn', by='benkirk', when=T0 + timedelta(hours=1))
        assert (row.resolution, row.closed_by, row.state) == ('withdrawn', 'benkirk', 'withdrawn')
        assert not row.is_active
        with pytest.raises(ValueError):
            row.close(resolution='expired', by='task:x')
        with pytest.raises(ValueError):
            row.update(note='late')
        assert session.query(UnixGroupMask).filter(
            UnixGroupMask.mask_id == row.mask_id, ~UnixGroupMask.is_active).count() == 1

    def test_close_refuses_an_unknown_resolution(self, session):
        row = make_unix_group_mask(session, unix_gid=-5, username='a', when=T0)
        with pytest.raises(ValueError):
            row.close(resolution='done', by='x')

    def test_update_edits_note_and_expiry(self, session):
        row = make_unix_group_mask(session, unix_gid=-6, username='a', when=T0)
        row.update(note='reason', expires_at=T0 + timedelta(days=3), now=T0)
        assert (row.note, row.expires_at) == ('reason', T0 + timedelta(days=3))
        row.update(clear_expiry=True, now=T0)
        assert row.expires_at is None
        project = make_unix_group_mask(session, unix_gid=-7, username='a', kind='project',
                                       expires_at=T0 + timedelta(days=1), when=T0)
        with pytest.raises(ValueError):
            project.update(clear_expiry=True, now=T0)

    def test_an_overdue_open_row_reads_expired(self, session):
        row = make_unix_group_mask(session, unix_gid=-8, username='a',
                                   expires_at=datetime.now() + timedelta(seconds=1), when=T0)
        row.expires_at = datetime.now() - timedelta(seconds=1)
        assert row.state == 'expired' and row.is_active
