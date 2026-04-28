"""End-to-end test for `sam-admin accounting --disk`.

Builds a minimal SAM graph (User → Project → Account → DISK Resource),
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

from cli.cmds.admin import cli
from cli.accounting import disk_usage as disk_usage_mod
from cli.accounting import quota_readers as quota_readers_mod
from sam.resources.resources import ResourceType
from sam.summaries.disk_summaries import (
    DISK_CHARGING_TIB_EPOCH,
    DiskChargeSummary,
    DiskChargeSummaryStatus,
)
from factories.core import make_user
from factories.projects import make_account, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_seq


pytestmark = pytest.mark.unit


def _build_campaign_store_graph(session, monkeypatch):
    """Build a project on a uniquely-named DISK resource and register
    GladeCsvReader / GpfsQuotaReader for it so the CLI dispatch works."""
    lead = make_user(session)
    project = make_project(session, lead=lead)
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    resource_name = f"Campaign_Store_{next_seq('cs')}"
    resource = make_resource(session, resource_type=rt, resource_name=resource_name)
    make_account(session, project=project, resource=resource)
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
             patch('cli.cmds.admin.Session') as mock_session_cls:
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
        """FILESET total > Σuser_bytes → emit synthetic '<unidentified>' row
        attributed to project lead."""
        lead, project, resource = _build_campaign_store_graph(session, monkeypatch)
        snap = DISK_CHARGING_TIB_EPOCH

        # User row: 1 GiB. FILESET total: 3 GiB → 2 GiB gap.
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
        # 2 GiB on /data + 3 GiB on /work, same user → expect ONE row
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

