"""End-to-end test for `sam-admin accounting --disk`.

Builds a minimal SAM graph (User -> Project -> Account -> DISK Resource),
feeds a small acct.glade-shaped CSV + cs_usage.json-shaped JSON, runs
the CLI via CliRunner, and asserts the resulting disk_charge_summary
rows include both real per-user rows AND the synthetic gap row
attributed to the project lead with ``act_username='<unidentified>'``.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


# Every test here writes disk_charge_summary / disk_activity rows under the
# same fixed date; two workers deadlock on that index gap and InnoDB's rollback
# destroys the per-test SAVEPOINT ("sa_savepoint_N does not exist"). See
# `serial_file_lock` in tests/conftest.py for why a lock, not `--dist loadgroup`.
@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('disk_admin_fixed_dates'):
        yield

from cli.cmds.admin import cli
from cli.accounting import disk_usage as disk_usage_mod
from cli.accounting import quota_readers as quota_readers_mod
from sam.resources.resources import ResourceType
from sam.activity.disk import DiskActivity, DiskCharge
from sam.summaries.disk_summaries import (
    DISK_CHARGING_TIB_EPOCH,
    DiskChargeSummary,
    DiskChargeSummaryStatus,
)
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_seq




def _build_campaign_store_graph(session, monkeypatch, *, funded=True):
    """Build a project on a uniquely-named DISK resource and register
    GladeCsvReader / GpfsQuotaReader for it so the CLI dispatch works.
    ``funded`` gives the account an active allocation."""
    lead = make_user(session)
    project = make_project(session, lead=lead)
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    resource_name = f"Campaign_Store_{next_seq('cs')}"
    resource = make_resource(session, resource_type=rt, resource_name=resource_name)
    account = make_account(session, project=project, resource=resource)
    if funded:
        make_allocation(session, account=account)
    # Patch the reader registries so the CLI finds a reader for this name.
    monkeypatch.setitem(
        disk_usage_mod._READERS, resource_name, disk_usage_mod.GladeCsvReader,
    )
    monkeypatch.setitem(
        quota_readers_mod._READERS, resource_name, quota_readers_mod.GpfsQuotaReader,
    )
    return lead, project, resource


def _write_acct(tmp_path: Path, snap: date, projcode: str, username: str,
                kib: int) -> Path:
    """Single-row acct.glade.YYYY-MM-DD."""
    f = tmp_path / f"acct.glade.{snap.isoformat()}"
    # CSV columns (8): date, path, projcode, username, nfiles, KiB, interval, cos
    f.write_text(
        f'"{snap.isoformat()}","/gpfs/csfs1/{projcode.lower()}",'
        f'"{projcode.lower()}","{username}","100","{kib}","7","0"\n'
    )
    return f


def _write_acct_multifileset(tmp_path: Path, snap: date, projcode: str,
                              rows: list[tuple[str, str, int, int]]) -> Path:
    """Multi-row acct.glade.YYYY-MM-DD.

    Each row is (directory_path, username, nfiles, kib).
    """
    f = tmp_path / f"acct.glade.{snap.isoformat()}"
    lines = [
        f'"{snap.isoformat()}","{dir_path}",'
        f'"{projcode.lower()}","{username}","{nfiles}","{kib}","7","0"\n'
        for (dir_path, username, nfiles, kib) in rows
    ]
    f.write_text(''.join(lines))
    return f


def _write_quotas(tmp_path: Path, snap: date, fileset: str,
                  usage_kib: int, limit_kib: int) -> Path:
    f = tmp_path / "cs_usage.json"
    body = {
        "date": f"Mon Apr 27 08:00:00 MDT {snap.year}",
        "paths": {"csfs1": {fileset: f"/gpfs/csfs1/{fileset}"}},
        "usage": {
            "FILESET": {
                fileset: {
                    "limit": str(limit_kib),
                    "usage": str(usage_kib),
                    "files": "100",
                }
            },
            "USR": {},
        },
        "groups": {},
    }
    f.write_text(json.dumps(body))
    return f


class TestDiskAdminCli:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        with patch('sam.session.create_sam_engine') as mock_engine, \
             patch('cli.core.context.Session') as mock_session_cls:
            mock_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    def test_dry_run_produces_no_rows(self, runner, mock_db_session, tmp_path, session, monkeypatch):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        # 1 GiB written by the lead (KiB count = 1024 * 1024)
        f = _write_acct(tmp_path, snap, project.projcode, lead.username,
                        kib=1024 * 1024)

        n_before = session.query(DiskChargeSummary).count()
        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
            '--dry-run',
        ])
        assert result.exit_code == 0, result.output
        n_after = session.query(DiskChargeSummary).count()
        assert n_after == n_before

    def test_live_run_writes_user_row(self, runner, mock_db_session, tmp_path, session, monkeypatch):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, project.projcode, lead.username,
                        kib=1024 * 1024)  # 1 GiB

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        rows = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.user_id == lead.user_id,
        ).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.bytes == 1024 * 1024 * 1024            # 1 GiB
        # 1 GiB × 7 / 365 / 1024⁴ = 7 / (365 × 1024) ≈ 1.873e-5 TiB-yr.
        # DB column is FLOAT (~7 decimal digits) — generous tolerance.
        expected_ty = 7 / (365 * 1024)
        assert row.terabyte_years == pytest.approx(expected_ty, abs=1e-7)
        assert row.charges == pytest.approx(row.terabyte_years, abs=1e-7)
        # Normal rows: act_username carries the parsed username (used by
        # the resolver). Gap rows would carry '<unidentified>'.
        assert row.act_username == lead.username

        # Snapshot status updated.
        currents = session.query(DiskChargeSummaryStatus).filter(
            DiskChargeSummaryStatus.current == True  # noqa: E712
        ).all()
        assert any(r.activity_date == snap for r in currents)

    def test_gap_reconciliation_creates_unidentified_row(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """FILESET total > Σuser_bytes -> emit synthetic '<unidentified>' row
        attributed to project lead."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH

        # User row: 1 GiB. FILESET total: 3 GiB -> 2 GiB gap.
        # The fileset_name keys on uppercased projcode, so use that here.
        kib_user = 1 * 1024 * 1024
        kib_fileset = 3 * 1024 * 1024
        f = _write_acct(tmp_path, snap, project.projcode, lead.username,
                        kib=kib_user)
        q = _write_quotas(tmp_path, snap, project.projcode.lower(),
                          usage_kib=kib_fileset, limit_kib=10 * 1024 * 1024)

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--quotas', str(q),
            '--reconcile-quota-gap',
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        rows = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.account_id == project.accounts[0].account_id,
        ).all()
        # One per-user row, one synthetic gap row.
        assert len(rows) == 2
        gap = [r for r in rows if r.act_username == '<unidentified>']
        normal = [r for r in rows if r.act_username != '<unidentified>']
        assert len(gap) == 1
        assert len(normal) == 1
        # The gap FK side points at the lead — not a synthetic user.
        assert gap[0].user_id == lead.user_id
        # The gap bytes equal the difference (1024⁴ × 2 = 2 GiB).
        assert gap[0].bytes == 2 * 1024 ** 3
        # The normal row stores the parsed username as act_username.
        assert normal[0].act_username == lead.username

    def test_multi_directory_rows_sum_per_user(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Multi-fileset projects: two acct.glade rows for the same user
        on different directories must SUM into one disk_charge_summary
        row, not UPDATE-overwrite. Mirrors legacy SAM's
        ``calculateDiskChargeSummaries`` SUM-by-(date, user, account)
        behavior. Without the import-time aggregation, the natural-key
        UPDATE silently keeps only the last fileset's bytes."""
        from sam.projects.projects import ProjectDirectory
        from datetime import datetime

        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        # Backdate ProjectDirectory.start_date by a day so is_active is
        # unambiguous despite Python/MySQL TZ skew.
        backdate = datetime.now() - timedelta(days=1)
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name=f"/gpfs/csfs1/{project.projcode.lower()}/data",
            start_date=backdate,
        )
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name=f"/gpfs/csfs1/{project.projcode.lower()}/work",
            start_date=backdate,
        )
        snap = DISK_CHARGING_TIB_EPOCH
        # 2 GiB on /data + 3 GiB on /work, same user -> expect ONE row
        # with SUM = 5 GiB.
        kib_data = 2 * 1024 * 1024
        kib_work = 3 * 1024 * 1024
        f = _write_acct_multifileset(tmp_path, snap, project.projcode, [
            (f"/gpfs/csfs1/{project.projcode.lower()}/data",
             lead.username, 200, kib_data),
            (f"/gpfs/csfs1/{project.projcode.lower()}/work",
             lead.username, 300, kib_work),
        ])

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        rows = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.account_id == project.accounts[0].account_id,
        ).all()
        # Exactly ONE row — the per-(user, project) aggregate.
        assert len(rows) == 1, [
            (r.act_username, r.bytes, r.number_of_files) for r in rows
        ]
        row = rows[0]
        # Bytes are summed across both filesets.
        assert row.bytes == 5 * 1024 ** 3
        # Files are summed too.
        assert row.number_of_files == 500
        # FK side points at the user.
        assert row.user_id == lead.user_id

    def test_rollup_total_row_attributed_to_project_lead(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Quasar-style feeds ship one project-rollup row with
        ``username='total'`` (no per-user breakdown). The import path
        must attribute the row to the project lead while keeping
        ``act_username='total'`` as the audit label."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        # 5 GiB rollup row, username='total'.
        kib = 5 * 1024 * 1024
        f = _write_acct(tmp_path, snap, project.projcode, 'total', kib=kib)

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        rows = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.account_id == project.accounts[0].account_id,
        ).all()
        assert len(rows) == 1
        row = rows[0]
        # FK side points at the project lead — that's what makes user
        # resolution and downstream charging math work.
        assert row.user_id == lead.user_id
        # Audit label is preserved so rollup rows are distinguishable
        # from real per-user rows.
        assert row.act_username == 'total'
        # Resolved (mirrored) username is the lead's, per the act_*
        # mirror-when-known convention.
        assert row.username == lead.username
        # Bytes match the input (5 GiB).
        assert row.bytes == 5 * 1024 ** 3

    # ------------------------------------------------------------------
    # Layer 2: tier-1 (disk_activity) + tier-2 (disk_charge) writes
    # ------------------------------------------------------------------

    def test_import_writes_disk_activity_per_directory(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Multi-fileset project: per-directory rows land in disk_activity
        (one per fileset) and disk_charge (1:1)."""
        from sam.projects.projects import ProjectDirectory
        from datetime import datetime

        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        backdate = datetime.now() - timedelta(days=1)
        for sub in ('data', 'work'):
            ProjectDirectory.create(
                session, project_id=project.project_id,
                directory_name=f"/gpfs/csfs1/{project.projcode.lower()}/{sub}",
                start_date=backdate,
            )
        snap = DISK_CHARGING_TIB_EPOCH
        kib_data = 2 * 1024 * 1024  # 2 GiB
        kib_work = 3 * 1024 * 1024  # 3 GiB
        f = _write_acct_multifileset(tmp_path, snap, project.projcode, [
            (f"/gpfs/csfs1/{project.projcode.lower()}/data",
             lead.username, 200, kib_data),
            (f"/gpfs/csfs1/{project.projcode.lower()}/work",
             lead.username, 300, kib_work),
        ])

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        activities = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).all()
        # One row per directory (per-fileset granularity preserved).
        assert len(activities) == 2, [
            (a.directory_name, a.bytes) for a in activities
        ]
        by_dir = {a.directory_name: a for a in activities}
        data_path = f"/gpfs/csfs1/{project.projcode.lower()}/data"
        work_path = f"/gpfs/csfs1/{project.projcode.lower()}/work"
        assert by_dir[data_path].bytes == 2 * 1024 ** 3
        assert by_dir[work_path].bytes == 3 * 1024 ** 3
        assert by_dir[data_path].number_of_files == 200
        assert by_dir[work_path].number_of_files == 300
        # file_size_total mirrors bytes (legacy column parity).
        assert by_dir[data_path].file_size_total == by_dir[data_path].bytes
        # Resolved cleanly.
        assert all(a.processing_status is True for a in activities)
        assert all(a.error_comment is None for a in activities)

        charges = session.query(DiskCharge).filter(
            DiskCharge.disk_activity_id.in_(
                [a.disk_activity_id for a in activities]
            )
        ).all()
        # 1:1 with disk_activity.
        assert len(charges) == 2
        assert all(c.user_id == lead.user_id for c in charges)
        assert all(c.account_id == project.accounts[0].account_id
                   for c in charges)
        # tib_years computed per-row at the input's bytes (no rollup).
        # 2 GiB × 7 / 365 / 1024⁴ ≈ 3.745e-5 ; 3 GiB -> 5.617e-5.
        ty_data = (2 * 1024 ** 3) * 7 / 365 / (1024 ** 4)
        ty_work = (3 * 1024 ** 3) * 7 / 365 / (1024 ** 4)
        ch_by_dir = {
            c.activity.directory_name: c for c in charges
        }
        assert ch_by_dir[data_path].terabyte_year == pytest.approx(
            ty_data, abs=1e-7
        )
        assert ch_by_dir[work_path].terabyte_year == pytest.approx(
            ty_work, abs=1e-7
        )

    def test_disk_activity_re_import_replaces_per_resource_date(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Re-running the same (resource, snap_date) deletes pre-existing
        tier-1/tier-2 rows and re-inserts — no append, no duplicates."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        # First run: 1 GiB.
        f1 = _write_acct(tmp_path, snap, project.projcode, lead.username,
                         kib=1 * 1024 * 1024)
        r1 = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f1),
            '--date', snap.isoformat(),
        ])
        assert r1.exit_code == 0, r1.output
        n_after_first = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).count()
        assert n_after_first == 1

        # Second run with different bytes: should REPLACE, not append.
        f2 = _write_acct(tmp_path, snap, project.projcode, lead.username,
                         kib=4 * 1024 * 1024)
        r2 = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f2),
            '--date', snap.isoformat(),
        ])
        assert r2.exit_code == 0, r2.output

        activities = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).all()
        # Still exactly one row — the prior one was deleted.
        assert len(activities) == 1
        assert activities[0].bytes == 4 * 1024 ** 3

        # disk_charge mirrors the new tier-1.
        charges = session.query(DiskCharge).filter(
            DiskCharge.disk_activity_id == activities[0].disk_activity_id
        ).all()
        assert len(charges) == 1

    def test_disk_activity_skipped_for_total_rollup(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Quasar-style ``username='total'`` rollup rows produce no
        tier-1 row (no real directory binding), even though tier-3 still
        attributes them to the project lead."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, project.projcode, 'total',
                        kib=5 * 1024 * 1024)

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        # No tier-1 row for the rollup sentinel.
        n_act = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).count()
        assert n_act == 0
        # But tier-3 still has the row (Layer 1 path unchanged).
        n_summary = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.account_id == project.accounts[0].account_id,
        ).count()
        assert n_summary == 1

    def test_disk_activity_skipped_for_unidentified_gap_rows(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Synthetic ``<unidentified>`` gap rows — no real directory —
        produce no tier-1 row."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        kib_user = 1 * 1024 * 1024
        kib_fileset = 3 * 1024 * 1024
        f = _write_acct(tmp_path, snap, project.projcode, lead.username,
                        kib=kib_user)
        q = _write_quotas(tmp_path, snap, project.projcode.lower(),
                          usage_kib=kib_fileset, limit_kib=10 * 1024 * 1024)

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--quotas', str(q),
            '--reconcile-quota-gap',
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        # tier-1: only the real (lead) row, NOT the <unidentified> gap row.
        activities = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).all()
        assert len(activities) == 1
        assert activities[0].username == lead.username
        # tier-3 still has both rows (gap row tier-3-only).
        n_summary = session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.account_id == project.accounts[0].account_id,
        ).count()
        assert n_summary == 2

    def test_import_creates_missing_snapshot_status_row(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """A brand-new snapshot date (no pre-existing
        ``disk_charge_summary_status`` row) must import cleanly — the
        importer registers the FK parent row up-front, before inserting the
        ``disk_charge_summary`` children.

        Regression for the off-cadence Destor 05-11 failure: that date fell
        outside the GPFS Saturday cadence, so no prior import had seeded the
        status row, and the old import order (register AFTER insert) tripped
        ``fk_disk_charge_summary_date``.
        """
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = date(2026, 5, 11)   # off-cadence; no status row in the dump

        # Precondition: guarantee no parent status row exists for this date.
        session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
        ).delete(synchronize_session=False)
        session.query(DiskChargeSummaryStatus).filter(
            DiskChargeSummaryStatus.activity_date == snap,
        ).delete(synchronize_session=False)
        session.flush()
        assert session.get(DiskChargeSummaryStatus, snap) is None

        f = _write_acct(tmp_path, snap, project.projcode, lead.username,
                        kib=1024 * 1024)

        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output

        # Parent status row created up-front; child summary row inserted.
        assert session.get(DiskChargeSummaryStatus, snap) is not None
        assert session.query(DiskChargeSummary).filter(
            DiskChargeSummary.activity_date == snap,
            DiskChargeSummary.user_id == lead.user_id,
        ).count() == 1

    def test_import_ends_with_snapshot_current_after_legacy_triggers(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Prod's legacy disk_charge triggers flip the date to current=FALSE mid-import; the run must end TRUE."""
        from cli.accounting.commands import AccountingAdminCommand

        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, project.projcode, lead.username, kib=1024 * 1024)

        real_write = AccountingAdminCommand._write_disk_activity_and_charge

        def write_then_trigger(self, *args, **kwargs):
            out = real_write(self, *args, **kwargs)
            self.session.get(DiskChargeSummaryStatus, kwargs['snap_date']).current = False
            self.session.flush()
            return out

        monkeypatch.setattr(AccountingAdminCommand, '_write_disk_activity_and_charge',
                            write_then_trigger)
        result = runner.invoke(cli, [
            'accounting', '--disk',
            '--resource', resource.resource_name,
            '--user-usage', str(f),
            '--date', snap.isoformat(),
        ])
        assert result.exit_code == 0, result.output
        session.expire_all()
        assert session.get(DiskChargeSummaryStatus, snap).current is True



def _write_acct_rows(tmp_path: Path, snap: date,
                     rows: list[tuple[str, str, str, int]]) -> Path:
    """acct.glade.YYYY-MM-DD from (directory_path, label, username, kib) rows."""
    f = tmp_path / f"acct.glade.{snap.isoformat()}"
    f.write_text(''.join(
        f'"{snap.isoformat()}","{path}","{label.lower()}","{user}","10","{kib}","7","0"\n'
        for (path, label, user, kib) in rows
    ))
    return f


class TestDiskImportTriage:
    """Each report category, its exit code, and the JSON envelope."""

    GIB = 1024 * 1024  # KiB

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        with patch('sam.session.create_sam_engine') as mock_engine, \
             patch('cli.core.context.Session') as mock_session_cls:
            mock_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    def _run(self, runner, resource, f, *extra, json_out=False):
        args = (['--format', 'json'] if json_out else []) + [
            'accounting', '--disk', '--resource', resource.resource_name,
            '--user-usage', str(f), *extra,
        ]
        return runner.invoke(cli, args)

    def _summary_rows(self, session, resource, snap):
        from sam.accounting.accounts import Account
        return session.query(DiskChargeSummary).join(
            Account, Account.account_id == DiskChargeSummary.account_id,
        ).filter(
            DiskChargeSummary.activity_date == snap,
            Account.resource_id == resource.resource_id,
        ).all()

    def test_unknown_projcode_is_no_project_and_exits_2(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        label = f"ZZNP{next_seq('np')}".upper()
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{label.lower()}", label, lead.username, self.GIB),
        ])
        result = self._run(runner, resource, f, '--skip-errors', '--dry-run', json_out=True)
        assert result.exit_code == 2, result.output
        data = json.loads(result.stdout)
        assert data['counts']['no_project'] == 1
        [item] = data['categories']['no_project']
        assert item['projcode'] == label and item['project_id'] is None

    def test_project_without_account_is_no_account_and_exits_2(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, _project, resource = _build_campaign_store_graph(session, monkeypatch)
        other = make_project(session, lead=lead)   # no account on the resource
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, other.projcode, lead.username, kib=self.GIB)
        result = self._run(runner, resource, f, '--skip-errors', json_out=True)
        assert result.exit_code == 2, result.output
        data = json.loads(result.stdout)
        [item] = data['categories']['no_account']
        assert item['sam_projcode'] == other.projcode
        assert item['project_id'] == other.project_id
        assert self._summary_rows(session, resource, snap) == []

    def test_known_unowned_is_skipped_and_exits_0(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, _project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        path = f"/gpfs/csfs1/ral/nral{next_seq('ku')}"
        f = _write_acct_rows(tmp_path, snap, [(path, 'ROOT', lead.username, self.GIB)])
        result = self._run(runner, resource, f, '--verbose')
        assert result.exit_code == 0, result.output
        assert 'known unowned' in result.output
        assert self._summary_rows(session, resource, snap) == []
        [act] = session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).all()
        assert act.error_comment.startswith('unresolved(known_unowned):')

    def test_projcode_fallback_charges_and_reports_unlinked_directory(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, project.projcode, lead.username, kib=self.GIB)
        result = self._run(runner, resource, f, json_out=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        [item] = data['categories']['unlinked_directory']
        assert item['project_id'] == project.project_id
        assert item['path'] == f"/gpfs/csfs1/{project.projcode.lower()}"
        assert data['created'] == 1
        assert len(self._summary_rows(session, resource, snap)) == 1

    def test_linked_directory_is_not_reported(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        from datetime import datetime
        from sam.projects.projects import ProjectDirectory
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        ProjectDirectory.create(session, project_id=project.project_id,
                                directory_name=path,
                                start_date=datetime.now() - timedelta(days=1))
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct(tmp_path, snap, project.projcode, lead.username, kib=self.GIB)
        result = self._run(runner, resource, f, '--dry-run', json_out=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert all(not items for items in data['categories'].values())

    def test_unknown_user_exits_2(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        _lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        ghost = f"ghost{next_seq('gu')}"
        f = _write_acct(tmp_path, snap, project.projcode, ghost, kib=self.GIB)
        result = self._run(runner, resource, f, '--skip-errors', json_out=True)
        assert result.exit_code == 2, result.output
        data = json.loads(result.stdout)
        [item] = data['categories']['unknown_user']
        assert item['username'] == ghost and item['projcodes'] == [project.projcode]
        assert data['errors'] == 0

    def test_skip_errors_still_loads_resolvable_rows(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """The lane's contract: known data lands even when other rows fail."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        label = f"ZZNP{next_seq('np')}".upper()
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{project.projcode.lower()}", project.projcode, lead.username, self.GIB),
            (f"/gpfs/csfs1/{label.lower()}", label, lead.username, self.GIB),
            ("/gpfs/csfs1/cisl/HDIG/jamtest", 'ROOT', lead.username, self.GIB),
        ])
        result = self._run(runner, resource, f, '--skip-errors')
        assert result.exit_code == 2, result.output
        assert label in result.output
        [row] = self._summary_rows(session, resource, snap)
        assert row.act_projcode == project.projcode
        n_charge = session.query(DiskCharge).join(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).count()
        assert n_charge == 1

    def test_without_skip_errors_an_unexpected_gap_writes_nothing(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        label = f"ZZNP{next_seq('np')}".upper()
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{project.projcode.lower()}", project.projcode, lead.username, self.GIB),
            (f"/gpfs/csfs1/{label.lower()}", label, lead.username, self.GIB),
        ])
        result = self._run(runner, resource, f)
        assert result.exit_code == 2, result.output
        assert 'nothing written' in result.output
        assert self._summary_rows(session, resource, snap) == []
        assert session.query(DiskActivity).filter(
            DiskActivity.activity_date == snap,
            DiskActivity.resource_name == resource.resource_name,
        ).count() == 0

    def test_dry_run_prints_categories_and_writes_nothing(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{project.projcode.lower()}", project.projcode, lead.username, self.GIB),
            ("/quasar/ral_risc", 'RISC', lead.username, self.GIB),
            ("/gpfs/csfs1/x", project.projcode, 'sshd', self.GIB),
        ])
        result = self._run(runner, resource, f, '--dry-run')
        assert result.exit_code == 0, result.output
        assert 'nothing written' in result.output
        assert 'directory not linked' in result.output
        assert 'RISC' not in result.output          # listed only under --verbose
        result = self._run(runner, resource, f, '--dry-run', '--verbose')
        assert result.exit_code == 0, result.output
        assert 'RISC' in result.output
        assert 'System accounts dropped: sshd' in result.output
        assert self._summary_rows(session, resource, snap) == []

    def test_json_envelope_shape(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{project.projcode.lower()}", project.projcode, lead.username, self.GIB),
            ("/gpfs/csfs1/y", project.projcode, 'systemd-coredump', self.GIB),
        ])
        result = self._run(runner, resource, f, '--dry-run', json_out=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['kind'] == 'disk_import'
        assert data['resource'] == resource.resource_name
        assert data['snapshot_date'] == snap.isoformat()
        assert data['dry_run'] is True and data['written'] is False
        assert set(data['categories']) == {
            'no_project', 'no_account', 'unknown_user',
            'known_unowned', 'unlinked_directory', 'expired_directory',
        }
        assert data['counts']['system_account_rows'] == 1
        assert data['counts']['unexpected'] == 0
        assert data['system_accounts'] == {'systemd-coredump': 1}

    def _link(self, session, project, path):
        from datetime import datetime
        from sam.projects.projects import ProjectDirectory
        ProjectDirectory.create(session, project_id=project.project_id,
                                directory_name=path,
                                start_date=datetime.now() - timedelta(days=1))

    def test_unlinked_path_borrows_the_project_of_a_linked_sibling_label(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        """Quasar shape: /quasar/rda is linked, /quasar/rda_dr is not, both labeled 'decs'."""
        _lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        label = f"ZZSB{next_seq('sb')}".upper()
        linked = f"/quasar/{label.lower()}"
        self._link(session, project, linked)
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct_rows(tmp_path, snap, [
            (linked, label, 'total', self.GIB),
            (f"{linked}_dr", label, 'total', self.GIB),
        ])
        result = self._run(runner, resource, f, '--skip-errors', json_out=True)
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['counts']['no_project'] == 0
        [item] = data['categories']['unlinked_directory']
        assert item['path'] == f"{linked}_dr"
        assert item['project_id'] == project.project_id
        [row] = self._summary_rows(session, resource, snap)
        assert row.bytes == 2 * 1024 ** 3

    def test_label_linked_to_two_projects_is_not_borrowed(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        other = make_project(session, lead=lead)
        make_account(session, project=other, resource=resource)
        label = f"ZZAM{next_seq('am')}".upper()
        self._link(session, project, f"/gpfs/csfs1/{label.lower()}/a")
        self._link(session, other, f"/gpfs/csfs1/{label.lower()}/b")
        snap = DISK_CHARGING_TIB_EPOCH
        f = _write_acct_rows(tmp_path, snap, [
            (f"/gpfs/csfs1/{label.lower()}/a", label, lead.username, self.GIB),
            (f"/gpfs/csfs1/{label.lower()}/b", label, lead.username, self.GIB),
            (f"/gpfs/csfs1/{label.lower()}/c", label, lead.username, self.GIB),
        ])
        result = self._run(runner, resource, f, '--dry-run', json_out=True)
        assert result.exit_code == 2, result.output
        [item] = json.loads(result.stdout)['categories']['no_project']
        assert item['path'] == f"/gpfs/csfs1/{label.lower()}/c"


class TestReconcileDirectories:
    """Directory actions in the report, and --reconcile-directories applying them."""

    GIB = 1024 * 1024  # KiB

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_db_session(self, session):
        with patch('sam.session.create_sam_engine') as mock_engine, \
             patch('cli.core.context.Session') as mock_session_cls:
            mock_engine.return_value = (MagicMock(), None)
            mock_session_cls.return_value = session
            yield session

    def _dir(self, session, project, path, *, ended=False):
        from datetime import datetime
        from sam.projects.projects import ProjectDirectory
        pd = ProjectDirectory.create(session, project_id=project.project_id,
                                     directory_name=path,
                                     start_date=datetime.now() - timedelta(days=30))
        if ended:
            pd.end_date = datetime.now() - timedelta(days=2)
            session.flush()
        return pd

    def _dry_item(self, runner, resource, f):
        result = runner.invoke(cli, [
            '--format', 'json', 'accounting', '--disk', '--resource',
            resource.resource_name, '--user-usage', str(f), '--dry-run'])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        items = data['categories']['unlinked_directory']
        return items[0] if items else None, data

    def _apply(self, runner, resource, f):
        result = runner.invoke(cli, [
            '--format', 'json', 'accounting', '--disk', '--resource',
            resource.resource_name, '--user-usage', str(f), '--reconcile-directories'])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    def _rows(self, session, path):
        from sam.projects.projects import ProjectDirectory
        return session.query(ProjectDirectory).filter(
            ProjectDirectory.directory_name == path).all()

    def test_ended_row_is_reopened(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        pd = self._dir(session, project, path, ended=True)
        f = _write_acct(tmp_path, DISK_CHARGING_TIB_EPOCH, project.projcode,
                        lead.username, kib=self.GIB)
        item, _ = self._dry_item(runner, resource, f)
        assert item['action'] == 'reopen'
        assert item['project_directory_id'] == pd.project_directory_id
        assert pd.end_date is not None                   # the dry run wrote nothing

        data = self._apply(runner, resource, f)
        assert data['directories'] == {'reopen': 1, 'rename': 0, 'create': 0}
        session.refresh(pd)
        assert pd.end_date is None
        assert len(self._rows(session, path)) == 1       # no duplicate row
        assert self._dry_item(runner, resource, f)[0] is None   # now linked

    def test_case_variant_row_is_renamed(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        pd = self._dir(session, project, path.upper())
        f = _write_acct(tmp_path, DISK_CHARGING_TIB_EPOCH, project.projcode,
                        lead.username, kib=self.GIB)
        assert self._dry_item(runner, resource, f)[0]['action'] == 'rename'
        self._apply(runner, resource, f)
        session.refresh(pd)
        assert pd.directory_name == path and pd.end_date is None

    def test_missing_row_is_created(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        f = _write_acct(tmp_path, DISK_CHARGING_TIB_EPOCH, project.projcode,
                        lead.username, kib=self.GIB)
        assert self._dry_item(runner, resource, f)[0]['action'] == 'create'
        assert self._rows(session, path) == []
        data = self._apply(runner, resource, f)
        assert data['directories']['create'] == 1
        [pd] = self._rows(session, path)
        assert pd.project_id == project.project_id and pd.end_date is None

    def test_path_owned_by_another_project_is_left_for_review(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        other = make_project(session, lead=lead)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        self._dir(session, other, path, ended=True)
        f = _write_acct(tmp_path, DISK_CHARGING_TIB_EPOCH, project.projcode,
                        lead.username, kib=self.GIB)
        assert self._dry_item(runner, resource, f)[0]['action'] == 'review'
        data = self._apply(runner, resource, f)
        assert data['directories'] == {'reopen': 0, 'rename': 0, 'create': 0}
        [pd] = self._rows(session, path)
        assert pd.project_id == other.project_id and pd.end_date is not None

    def test_expired_allocation_is_its_own_bucket_and_untouched(
        self, runner, mock_db_session, tmp_path, session, monkeypatch,
    ):
        lead, project, resource = _build_campaign_store_graph(
            session, monkeypatch, funded=False)
        path = f"/gpfs/csfs1/{project.projcode.lower()}"
        pd = self._dir(session, project, path, ended=True)
        f = _write_acct(tmp_path, DISK_CHARGING_TIB_EPOCH, project.projcode,
                        lead.username, kib=self.GIB)
        item, data = self._dry_item(runner, resource, f)
        assert item is None
        [expired] = data['categories']['expired_directory']
        assert expired['path'] == path
        applied = self._apply(runner, resource, f)
        assert applied['directories'] == {'reopen': 0, 'rename': 0, 'create': 0}
        assert applied['created'] == 1                   # still charged
        session.refresh(pd)
        assert pd.end_date is not None

    def test_flag_is_rejected_outside_disk(self, runner, mock_db_session):
        result = runner.invoke(cli, [
            'accounting', '--comp', '--machine', 'derecho', '--reconcile-directories'])
        assert result.exit_code == 1
        assert '--reconcile-directories only applies to --disk' in result.output
