"""The last-seen ledger: monotonic upsert semantics, and it is never purged."""

from datetime import datetime, timedelta

import pytest

from system_status import AccessSource, System, UserDef, UserLastSeen
from system_status import retention
from system_status.queries.last_seen import (
    get_last_seen,
    get_or_create_source,
    record_seen,
    record_seen_at,
)

T0 = datetime(2026, 9, 1, 12, 0, 0)
LATER = T0 + timedelta(hours=1)
EARLIER = T0 - timedelta(days=400)


def _row(session, username, kind='pbs', system='derecho'):
    return (session.query(UserLastSeen)
            .join(UserLastSeen.user).join(UserLastSeen.source).join(AccessSource.system)
            .filter(UserDef.username == username, AccessSource.kind == kind,
                    System.name == system)
            .one())


class TestRecordSeen:

    def test_first_sighting_inserts_a_point_window(self, status_session):
        assert record_seen_at(status_session, 'pbs', 'derecho', ['alice'], T0) == 1
        status_session.commit()
        row = _row(status_session, 'alice')
        assert (row.first_seen, row.last_seen) == (T0, T0)

    def test_newer_sighting_advances_last_seen_only(self, status_session):
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], T0)
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], LATER)
        status_session.commit()
        row = _row(status_session, 'alice')
        assert (row.first_seen, row.last_seen) == (T0, LATER)

    def test_older_sighting_never_moves_last_seen_back(self, status_session):
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], LATER)
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], T0)
        status_session.commit()
        assert _row(status_session, 'alice').last_seen == LATER

    def test_backfill_lowers_first_seen_after_live_rows_exist(self, status_session):
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], LATER)
        record_seen(status_session, 'pbs', 'derecho', [('alice', EARLIER, T0)])
        status_session.commit()
        row = _row(status_session, 'alice')
        assert (row.first_seen, row.last_seen) == (EARLIER, LATER)

    def test_repeated_username_in_one_batch_is_merged(self, status_session):
        n = record_seen(status_session, 'pbs', 'derecho',
                        [('alice', T0, T0), ('alice', EARLIER, EARLIER), ('alice', LATER, LATER)])
        status_session.commit()
        assert n == 1
        row = _row(status_session, 'alice')
        assert (row.first_seen, row.last_seen) == (EARLIER, LATER)

    def test_blank_usernames_are_skipped(self, status_session):
        assert record_seen_at(status_session, 'pbs', 'derecho', ['', '  ', None], T0) == 0
        assert status_session.query(UserLastSeen).count() == 0

    def test_sources_are_per_kind_and_system(self, status_session):
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], T0)
        record_seen_at(status_session, 'pbs', 'casper', ['alice'], LATER)
        record_seen_at(status_session, 'login', 'casper', ['alice'], EARLIER)
        status_session.commit()
        assert status_session.query(UserLastSeen).count() == 3
        assert status_session.query(UserDef).filter_by(username='alice').count() == 1
        assert _row(status_session, 'alice', 'pbs', 'derecho').last_seen == T0

    def test_source_rows_are_reused(self, status_session):
        a = get_or_create_source(status_session, 'login', 'derecho')
        b = get_or_create_source(status_session, 'login', 'derecho')
        assert a.source_id == b.source_id

    def test_unknown_kind_is_rejected(self, status_session):
        with pytest.raises(ValueError, match='unknown access source kind'):
            record_seen_at(status_session, 'ssh', 'derecho', ['alice'], T0)

    def test_batch_larger_than_one_chunk(self, status_session):
        names = [f'user{i:04d}' for i in range(1200)]
        assert record_seen_at(status_session, 'pbs', 'derecho', names, T0) == 1200
        status_session.commit()
        assert status_session.query(UserLastSeen).count() == 1200


class TestGetLastSeen:

    def test_one_entry_per_source_most_recent_first(self, status_session):
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], T0)
        record_seen_at(status_session, 'webapp', 'samuel', ['alice'], LATER)
        record_seen_at(status_session, 'pbs', 'derecho', ['bob'], LATER)
        status_session.commit()
        got = get_last_seen(status_session, 'alice')
        assert [(g['kind'], g['system']) for g in got] == [('webapp', 'samuel'), ('pbs', 'derecho')]

    def test_unknown_user_is_empty(self, status_session):
        assert get_last_seen(status_session, 'nobody') == []


class TestNeverPurged:
    """A user last seen years ago is the answer this table exists to give."""

    PROTECTED = {'user_last_seen', 'access_sources', 'status_users'}

    def test_not_a_snapshot_table(self):
        assert not self.PROTECTED & {name for _model, name in retention.SNAPSHOT_TABLES}

    def test_no_retention_override(self):
        assert not self.PROTECTED & set(retention.RETENTION_DAYS)

    def test_cleanup_leaves_ancient_rows_alone(self, status_session):
        ancient = datetime(2020, 1, 1)
        record_seen_at(status_session, 'pbs', 'derecho', ['alice'], ancient)
        status_session.commit()
        retention.cleanup_old_data(cutoff=T0, session=status_session)
        status_session.commit()
        assert _row(status_session, 'alice').last_seen == ancient
