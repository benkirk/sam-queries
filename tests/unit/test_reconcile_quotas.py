"""Tests for `sam-admin accounting --reconcile-quotas`.

Covers the GPFS quota reader, the dispatch factory, the mapping/classification
logic in `AccountingAdminCommand._run_reconcile_quotas`, and dry-run safety.
"""
import json
import subprocess
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from cli.accounting.commands import AccountingAdminCommand, QUOTA_TOLERANCE
from cli.accounting import quota_readers as qr_mod
from cli.accounting.quota_readers import (
    GpfsQuotaReader, QuotaEntry, get_quota_reader,
)
from cli.accounting.path_verifier import (
    PathVerifier, PathVerificationError, auto_detect_verifier,
)
from cli.core.context import Context
from sam.projects.projects import ProjectDirectory
from sam.resources.resources import Resource, ResourceType

from factories import (
    make_user, make_project, make_account, make_allocation,
    make_resource, make_resource_type, next_seq,
)


pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────
# GpfsQuotaReader (no DB)
# ─────────────────────────────────────────────────────────────────────────

class TestGpfsQuotaReader:

    def _write(self, tmp_path, data):
        p = tmp_path / 'cs_usage.json'
        p.write_text(json.dumps(data))
        return str(p)

    def test_reads_fileset_entries(self, tmp_path):
        # Real cs_usage.json sample: csg fileset = 24_696_061_952 KiB = 23 TiB.
        # (Confirmed against `df -BT /glade/campaign/cisl/csg` on derecho.)
        CSG_LIMIT_KIB = 24_696_061_952
        path = self._write(tmp_path, {
            'paths': {'csfs1': {
                'ucnn0022': '/gpfs/csfs1/univ/ucnn0022',
                'csg':      '/gpfs/csfs1/cisl/csg',
            }},
            'usage': {
                'FILESET': {
                    'ucnn0022': {'limit': '1073741824', 'usage': '100', 'files': '10'},
                    'csg':      {'limit': str(CSG_LIMIT_KIB),
                                 'usage': '5441888368', 'files': '5'},
                },
                'USR': {'someone': {'limit': 1, 'usage': 1, 'files': 1}},
            },
        })
        entries = GpfsQuotaReader(path).read()
        names = {e.fileset_name for e in entries}
        assert names == {'ucnn0022', 'csg'}   # USR excluded
        csg = next(e for e in entries if e.fileset_name == 'csg')
        assert csg.path == '/gpfs/csfs1/cisl/csg'
        # KiB → bytes (x1024), then bytes → TiB should land at exactly 23.0
        assert csg.limit_bytes == CSG_LIMIT_KIB * 1024
        assert csg.limit_tib == pytest.approx(23.0, rel=1e-9)

    def test_skips_zero_limit_umbrella_filesets(self, tmp_path):
        path = self._write(tmp_path, {
            'paths': {'csfs1': {'univ': '/gpfs/csfs1/univ'}},
            'usage': {'FILESET': {
                'univ': {'limit': '0', 'usage': '80', 'files': '4'},
            }, 'USR': {}},
        })
        assert GpfsQuotaReader(path).read() == []

    def test_missing_path_leaves_path_none(self, tmp_path):
        path = self._write(tmp_path, {
            'paths': {'csfs1': {}},
            'usage': {'FILESET': {
                'mystery': {'limit': '1024', 'usage': '0', 'files': '0'},
            }, 'USR': {}},
        })
        entries = GpfsQuotaReader(path).read()
        assert len(entries) == 1
        assert entries[0].path is None


class TestQuotaReaderFactory:

    def test_dispatches_campaign_store_to_gpfs(self, tmp_path):
        p = tmp_path / 'f.json'
        p.write_text('{}')
        reader = get_quota_reader('Campaign_Store', str(p))
        assert isinstance(reader, GpfsQuotaReader)

    def test_unknown_resource_raises_not_implemented(self, tmp_path):
        p = tmp_path / 'f.json'
        p.write_text('{}')
        with pytest.raises(NotImplementedError, match='Destor'):
            get_quota_reader('Destor', str(p))


# ─────────────────────────────────────────────────────────────────────────
# End-to-end reconcile (DB-backed; uses SAVEPOINT'd `session` fixture)
# ─────────────────────────────────────────────────────────────────────────

def _disk_resource(session, name='Campaign_Store'):
    """Get-or-create a DISK Resource with the given name."""
    existing = Resource.get_by_name(session, name)
    if existing is not None:
        return existing
    # Reuse an existing DISK ResourceType if present, else create one
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    return make_resource(session, resource_name=name, resource_type=rt)


def _isolated_disk_resource(session, monkeypatch):
    """Create a unique DISK resource and register a GPFS quota reader for it.

    Tests that assert on write-side effects need isolation from the ~600
    pre-existing Campaign_Store allocations in the snapshot DB. A unique
    resource per test keeps the reconcile scope to just the allocations
    the test itself created.
    """
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    name = next_seq('QRES')
    resource = make_resource(session, resource_name=name, resource_type=rt)
    # Patch the dispatch registry so get_quota_reader(name) returns a GPFS reader
    monkeypatch.setitem(qr_mod._READERS, name, GpfsQuotaReader)
    return resource


def _ctx_with_session(session):
    ctx = Context()
    ctx.session = session
    ctx.console = MagicMock()
    ctx.stderr_console = MagicMock()
    ctx.verbose = False
    return ctx


def _kib_for(tib: float) -> int:
    """Convert TiB → KiB — the unit cs_usage.json stores quota values in."""
    return int(tib * (1024 ** 3))


def _write_quota_file(tmp_path, fileset_to_entry, paths=None):
    data = {
        'paths': {'csfs1': paths or {}},
        'usage': {'FILESET': fileset_to_entry, 'USR': {}},
    }
    p = tmp_path / 'cs_usage.json'
    p.write_text(json.dumps(data))
    return str(p)


class TestReconcileMapping:
    """Verify the projcode-vs-ProjectDirectory mapping logic."""

    def test_direct_projcode_match_identifies_matched_allocation(
        self, session, tmp_path,
    ):
        resource = _disk_resource(session)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        make_allocation(session, account=account, amount=10.0)  # 10 TiB

        fileset = project.projcode.lower()
        quota_path = _write_quota_file(tmp_path, {
            fileset: {
                'limit': str(_kib_for(10.0)),
                'usage': '0', 'files': '0',
            },
        }, paths={fileset: f'/gpfs/csfs1/univ/{fileset}'})

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name='Campaign_Store',
            quota_path=quota_path,
            dry_run=True, force=False,
        )
        assert rc == 0

    def test_project_directory_match_via_mount_path(self, session, tmp_path):
        resource = _disk_resource(session)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=2.73)

        # Use a unique directory and fileset name so the quota file key
        # cannot collide with the projcode fast-path.
        fs_name = next_seq('qfs').lower()
        mount_path = f'/gpfs/csfs1/cgd/{fs_name}'
        ProjectDirectory.create(
            session,
            project_id=project.project_id,
            directory_name=mount_path,
        )

        quota_path = _write_quota_file(tmp_path, {
            fs_name: {
                'limit': str(_kib_for(2.73)),   # matches within 1%
                'usage': '0', 'files': '0',
            },
        }, paths={fs_name: mount_path})

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        # Direct inspection via the command's internals — easier than
        # parsing Rich console output.
        rc = cmd._run_reconcile_quotas(
            resource_name='Campaign_Store',
            quota_path=quota_path,
            dry_run=True, force=False,
        )
        assert rc == 0
        # No side effects in dry-run — allocation should be unchanged
        session.refresh(alloc)
        assert alloc.amount == 2.73
        assert alloc.end_date > datetime.now()   # still active


class TestReconcileClassification:

    def test_tolerance_below_threshold_is_matched(
        self, session, tmp_path, monkeypatch,
    ):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=100.0)
        # 0.5 % under threshold → matched (no write)
        quota_tib = 100.5
        fileset = project.projcode.lower()
        quota_path = _write_quota_file(tmp_path, {
            fileset: {'limit': str(_kib_for(quota_tib)),
                      'usage': '0', 'files': '0'},
        })
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.amount == 100.0  # untouched — within tolerance

    def test_tolerance_above_threshold_triggers_update(
        self, session, tmp_path, monkeypatch,
    ):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=100.0)
        quota_tib = 150.0  # 50% bigger — well over 1% tolerance
        fileset = project.projcode.lower()
        quota_path = _write_quota_file(tmp_path, {
            fileset: {'limit': str(_kib_for(quota_tib)),
                      'usage': '0', 'files': '0'},
        })
        # Ensure audit-trail admin user exists and is discoverable
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.amount == pytest.approx(quota_tib, rel=1e-9)

    def test_orphan_gets_end_date_set(self, session, tmp_path, monkeypatch):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=5.0)
        # Quota file does NOT mention this project → orphan
        quota_path = _write_quota_file(tmp_path, {})

        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.end_date is not None
        # end_date normalized to today 23:59:59 by SAM convention; allocation
        # is no longer active from tomorrow onward.
        assert alloc.end_date.date() == date.today()
        tomorrow = datetime.now() + timedelta(days=1)
        assert not alloc.is_active_at(tomorrow)

    def test_dry_run_makes_no_changes(self, session, tmp_path):
        resource = _disk_resource(session)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=5.0)

        fileset = project.projcode.lower()
        quota_path = _write_quota_file(tmp_path, {
            fileset: {'limit': str(_kib_for(99.0)),
                      'usage': '0', 'files': '0'},
        })

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name='Campaign_Store',
            quota_path=quota_path, dry_run=True, force=False,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.amount == 5.0   # dry-run: no write


class TestReconcileInputValidation:

    def test_missing_resource_fails_cleanly(self, session, tmp_path):
        quota_path = _write_quota_file(tmp_path, {})
        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=None, quota_path=quota_path,
            dry_run=True, force=False,
        )
        assert rc == 2

    def test_unknown_resource_fails_cleanly(self, session, tmp_path):
        quota_path = _write_quota_file(tmp_path, {})
        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name='NoSuchResource_xyz',
            quota_path=quota_path, dry_run=True, force=False,
        )
        assert rc == 2

    def test_unsupported_resource_raises_via_factory(self, session, tmp_path):
        # Register resource in SAM but don't add a quota reader for it
        _disk_resource(session, name='Destor')
        quota_path = _write_quota_file(tmp_path, {})
        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name='Destor', quota_path=quota_path,
            dry_run=True, force=False,
        )
        assert rc == 2


def test_quota_tolerance_constant():
    # Sanity: threshold matches the plan (1%)
    assert QUOTA_TOLERANCE == 0.01


# ─────────────────────────────────────────────────────────────────────────
# Tree-aware roll-up tests (NestedSetMixin subtree sums)
# ─────────────────────────────────────────────────────────────────────────

def _reconcile_dry_run(session, resource, quota_path, *, verbose=False):
    """Run a dry-run reconcile and return (rc, ctx) so the caller can inspect
    the records pushed into the console MagicMock (via display_quota_reconcile_plan).
    """
    ctx = _ctx_with_session(session)
    ctx.verbose = verbose
    cmd = AccountingAdminCommand(ctx)
    rc = cmd._run_reconcile_quotas(
        resource_name=resource.resource_name,
        quota_path=quota_path,
        dry_run=True, force=False,
    )
    return rc, ctx


def _captured_plan_call(ctx):
    """Return (matched, mismatched, orphaned, unmapped) args passed to
    display_quota_reconcile_plan by sniffing AccountingAdminCommand's dispatch.

    Rather than patch a moving target, the tests assert directly on the DB state
    or the command-returned classification by re-running the bookkeeping. This
    helper is a placeholder for future refactors; the tests below use DB asserts.
    """
    raise NotImplementedError  # intentionally — tests below assert via DB state


class TestTreeRollup:
    """Verify subtree roll-up works correctly across parent/child projects."""

    def test_parent_own_plus_child_sums_to_expected(
        self, session, tmp_path, monkeypatch,
    ):
        """Parent has own fileset (5 TiB) + child with fileset (10 TiB).
        Parent SAM amount = 15 TiB → matched (expected = 15 TiB).
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        child = make_project(session, parent=parent)

        # Each project gets its own Account + (for parent only) an Allocation
        p_account = make_account(session, project=parent, resource=resource)
        parent_alloc = make_allocation(session, account=p_account, amount=15.0)
        # Child has an account but no SAM allocation — just a fileset quota
        make_account(session, project=child, resource=resource)

        # Wire filesets: parent → own projcode, child → own projcode
        quota_path = _write_quota_file(tmp_path, {
            parent.projcode.lower(): {
                'limit': str(_kib_for(5.0)), 'usage': '0', 'files': '0',
            },
            child.projcode.lower(): {
                'limit': str(_kib_for(10.0)), 'usage': '0', 'files': '0',
            },
        })

        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        assert parent_alloc.amount == 15.0  # within tolerance, no update

    def test_parent_with_zero_own_quota_rolls_up_child(
        self, session, tmp_path, monkeypatch,
    ):
        """NMMM0003 scenario: parent's direct fileset has limit=0 (skipped
        by the reader) but its child has a nonzero quota. Parent should
        NOT be orphaned — its expected value is the child's quota.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        child = make_project(session, parent=parent)

        p_account = make_account(session, project=parent, resource=resource)
        # Parent SAM amount = 10 TiB; parent has no fileset of its own;
        # child supplies 10 TiB → expected = 10 TiB → matched.
        parent_alloc = make_allocation(session, account=p_account, amount=10.0)
        make_account(session, project=child, resource=resource)

        # Parent's /root path has limit=0 (umbrella); only child has quota.
        parent_path = f'/gpfs/csfs1/fake/{next_seq("root").lower()}'
        child_fs = next_seq('cfs').lower()
        child_path = f'{parent_path}/{child_fs}'
        ProjectDirectory.create(
            session, project_id=parent.project_id, directory_name=parent_path,
        )
        ProjectDirectory.create(
            session, project_id=child.project_id, directory_name=child_path,
        )

        quota_path = _write_quota_file(tmp_path, {
            # Parent umbrella skipped by reader (limit==0)
            'parent_umbrella': {
                'limit': '0', 'usage': '5', 'files': '1',
            },
            child_fs: {
                'limit': str(_kib_for(10.0)), 'usage': '0', 'files': '0',
            },
        }, paths={
            'parent_umbrella': parent_path,
            child_fs: child_path,
        })

        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        # Parent alloc untouched — matched, and still active (not orphaned).
        assert parent_alloc.amount == 10.0
        assert parent_alloc.is_active

    def test_rollup_does_not_cross_tree_roots(
        self, session, tmp_path, monkeypatch,
    ):
        """Two independent project trees must not pollute each other's
        subtree sums. Parent A with fileset 7 TiB; parent B with fileset
        3 TiB. Their allocations must each resolve independently.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        a = make_project(session)     # its own tree root
        b = make_project(session)     # a separate tree root

        a_acct = make_account(session, project=a, resource=resource)
        b_acct = make_account(session, project=b, resource=resource)
        a_alloc = make_allocation(session, account=a_acct, amount=7.0)
        b_alloc = make_allocation(session, account=b_acct, amount=3.0)

        quota_path = _write_quota_file(tmp_path, {
            a.projcode.lower(): {'limit': str(_kib_for(7.0)),
                                 'usage': '0', 'files': '0'},
            b.projcode.lower(): {'limit': str(_kib_for(3.0)),
                                 'usage': '0', 'files': '0'},
        })

        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(a_alloc)
        session.refresh(b_alloc)
        # Each stays at its own quota — cross-tree contamination would
        # have pushed both to 10 TiB.
        assert a_alloc.amount == 7.0
        assert b_alloc.amount == 3.0

    def test_leaf_project_own_quota_is_expected(
        self, session, tmp_path, monkeypatch,
    ):
        """Sanity check: leaf (no children) with own fileset works as before."""
        resource = _isolated_disk_resource(session, monkeypatch)
        proj = make_project(session)
        acct = make_account(session, project=proj, resource=resource)
        alloc = make_allocation(session, account=acct, amount=4.0)
        quota_path = _write_quota_file(tmp_path, {
            proj.projcode.lower(): {'limit': str(_kib_for(4.0)),
                                    'usage': '0', 'files': '0'},
        })
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.amount == 4.0

    def test_orphan_requires_entire_subtree_empty(
        self, session, tmp_path, monkeypatch,
    ):
        """Orphan requires that neither the project nor any descendant has
        a fileset. A parent with just one quota-bearing child is NOT orphan.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        child = make_project(session, parent=parent)

        p_acct = make_account(session, project=parent, resource=resource)
        parent_alloc = make_allocation(session, account=p_acct, amount=5.0)

        # Only child has a fileset
        quota_path = _write_quota_file(tmp_path, {
            child.projcode.lower(): {'limit': str(_kib_for(5.0)),
                                     'usage': '0', 'files': '0'},
        })
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        # Expected = 5 TiB (child) == SAM 5 TiB → matched, alloc unchanged,
        # allocation still active (NOT orphaned).
        assert parent_alloc.amount == 5.0
        assert parent_alloc.is_active

    def test_true_orphan_is_still_orphan(
        self, session, tmp_path, monkeypatch,
    ):
        """Control: parent + descendants all without filesets → orphan."""
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        make_project(session, parent=parent)  # quiet child, no fileset

        p_acct = make_account(session, project=parent, resource=resource)
        parent_alloc = make_allocation(session, account=p_acct, amount=5.0)
        quota_path = _write_quota_file(tmp_path, {})  # no quotas at all
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        assert parent_alloc.end_date is not None
        assert parent_alloc.end_date.date() == date.today()

    def test_mismatched_updates_to_rolled_up_value_not_own(
        self, session, tmp_path, monkeypatch,
    ):
        """Mismatch updates SAM amount to the full subtree sum (parent's
        own quota + every descendant), not just the parent's own fileset.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        child1 = make_project(session, parent=parent)
        child2 = make_project(session, parent=parent)

        p_acct = make_account(session, project=parent, resource=resource)
        # Start SAM = 1 TiB; expected subtree = 2 + 20 + 30 = 52 TiB
        parent_alloc = make_allocation(session, account=p_acct, amount=1.0)

        quota_path = _write_quota_file(tmp_path, {
            parent.projcode.lower(): {'limit': str(_kib_for(2.0)),
                                      'usage': '0', 'files': '0'},
            child1.projcode.lower(): {'limit': str(_kib_for(20.0)),
                                      'usage': '0', 'files': '0'},
            child2.projcode.lower(): {'limit': str(_kib_for(30.0)),
                                      'usage': '0', 'files': '0'},
        })
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        # Amount updated to the rolled-up value (52 TiB), not parent's own 2 TiB.
        assert parent_alloc.amount == pytest.approx(52.0, rel=1e-9)

    def test_orphan_record_carries_project_directories(
        self, session, tmp_path, monkeypatch,
    ):
        """Orphaned projects should surface their active ProjectDirectory paths
        so the admin can see what the deactivated allocation used to map to.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        make_allocation(session, account=account, amount=5.0)

        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/defunct/xyz',
        )
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/archived/old',
        )

        quota_path = _write_quota_file(tmp_path, {})  # no quota → orphan

        captured = {}
        import cli.accounting.commands as cmd_mod

        def _spy(ctx, resource_name, matched, mismatched, orphaned,
                 unmapped, *, dry_run, path_exists=None):
            captured['orphaned'] = orphaned

        monkeypatch.setattr(cmd_mod, 'display_quota_reconcile_plan', _spy)
        # Stub summary so it doesn't fail on missing args either
        monkeypatch.setattr(cmd_mod, 'display_quota_reconcile_summary',
                            lambda *a, **kw: None)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=True, force=False,
        )
        assert rc == 0
        # One orphan — tuple shape is (projcode, sam_tib, directories)
        assert len(captured['orphaned']) == 1
        projcode, sam_tib, directories = captured['orphaned'][0]
        assert projcode == project.projcode
        assert set(directories) == {
            '/gpfs/csfs1/defunct/xyz', '/gpfs/csfs1/archived/old',
        }

    def test_child_with_own_allocation_reconciles_independently(
        self, session, tmp_path, monkeypatch,
    ):
        """When both parent and child have Campaign_Store allocations, each
        is compared against its own subtree — not aggregated together.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        parent = make_project(session)
        child = make_project(session, parent=parent)

        p_acct = make_account(session, project=parent, resource=resource)
        c_acct = make_account(session, project=child, resource=resource)
        # Parent owns 5 TiB directly + rolls up child's 10 TiB → expected 15
        parent_alloc = make_allocation(session, account=p_acct, amount=15.0)
        # Child expected = its own 10 TiB (no descendants of its own)
        child_alloc = make_allocation(session, account=c_acct, amount=10.0)

        quota_path = _write_quota_file(tmp_path, {
            parent.projcode.lower(): {'limit': str(_kib_for(5.0)),
                                      'usage': '0', 'files': '0'},
            child.projcode.lower(): {'limit': str(_kib_for(10.0)),
                                     'usage': '0', 'files': '0'},
        })
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(parent_alloc)
        session.refresh(child_alloc)
        assert parent_alloc.amount == 15.0   # untouched
        assert child_alloc.amount == 10.0    # untouched

    def test_project_with_multiple_filesets_sums_all(
        self, session, tmp_path, monkeypatch,
    ):
        """A standalone (non-tree) project that owns several
        ProjectDirectory rows pointing at distinct filesets should have
        its expected quota = sum of every matching fileset's limit, not
        just one. Regression for P43713000 (rda_data + collections/gdex
        siblings) where single-valued mapping silently dropped all but
        one fileset.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        proj = make_project(session)
        acct = make_account(session, project=proj, resource=resource)
        # SAM = 30 TiB ; truth = 10 + 20 = 30 TiB across two filesets
        alloc = make_allocation(session, account=acct, amount=30.0)

        # Both filesets map to the same project via different paths;
        # neither projcode-named, so both must hit pass 2.
        ProjectDirectory.create(
            session, project_id=proj.project_id,
            directory_name='/gpfs/csfs1/collections/gdex/data',
        )
        ProjectDirectory.create(
            session, project_id=proj.project_id,
            directory_name='/gpfs/csfs1/collections/gdex/work',
        )
        quota_path = _write_quota_file(
            tmp_path,
            {
                'rda_data': {'limit': str(_kib_for(10.0)),
                             'usage': '0', 'files': '0'},
                'rda_work': {'limit': str(_kib_for(20.0)),
                             'usage': '0', 'files': '0'},
            },
            paths={
                'rda_data': '/gpfs/csfs1/collections/gdex/data',
                'rda_work': '/gpfs/csfs1/collections/gdex/work',
            },
        )
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        # Pre-fix: only one fileset (10 or 20 TiB) won → SAM 30 forced
        # down to that single value. Post-fix: 30 TiB matches the
        # 10+20 sum → no update.
        assert alloc.amount == 30.0
        assert alloc.is_active

    def test_projcode_match_and_path_match_for_same_fileset_not_double_counted(
        self, session, tmp_path, monkeypatch,
    ):
        """A fileset that matches BOTH by projcode (pass 1) and by
        ProjectDirectory path (pass 2) must contribute exactly once.
        """
        resource = _isolated_disk_resource(session, monkeypatch)
        proj = make_project(session)
        acct = make_account(session, project=proj, resource=resource)
        alloc = make_allocation(session, account=acct, amount=7.0)

        ProjectDirectory.create(
            session, project_id=proj.project_id,
            directory_name='/gpfs/csfs1/double',
        )
        quota_path = _write_quota_file(
            tmp_path,
            {
                proj.projcode.lower(): {  # matches pass 1
                    'limit': str(_kib_for(7.0)),
                    'usage': '0', 'files': '0',
                },
            },
            paths={proj.projcode.lower(): '/gpfs/csfs1/double'},
        )
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path, dry_run=False, force=True,
        )
        assert rc == 0
        session.refresh(alloc)
        # 7 TiB SAM vs 7 TiB truth (NOT 14) → matched, untouched.
        assert alloc.amount == 7.0


# ─────────────────────────────────────────────────────────────────────────
# Next-slice: snapshot metadata + high-util + --verify-paths
# ─────────────────────────────────────────────────────────────────────────

class TestGpfsSnapshotDate:

    def _write(self, tmp_path, data):
        p = tmp_path / 'cs_usage.json'
        p.write_text(json.dumps(data))
        return str(p)

    def test_parses_mdt_timestamp(self, tmp_path):
        path = self._write(tmp_path, {
            'date': 'Fri Apr 24 07:05:10 MDT 2026',
            'paths': {'csfs1': {}},
            'usage': {'FILESET': {}, 'USR': {}},
        })
        r = GpfsQuotaReader(path)
        r.read()
        assert r.snapshot_date == datetime(2026, 4, 24, 7, 5, 10)

    def test_garbage_date_yields_none(self, tmp_path):
        path = self._write(tmp_path, {
            'date': 'banana',
            'paths': {'csfs1': {}},
            'usage': {'FILESET': {}, 'USR': {}},
        })
        r = GpfsQuotaReader(path)
        r.read()
        assert r.snapshot_date is None

    def test_mount_metadata_defaults(self):
        r = GpfsQuotaReader('/does/not/matter')
        assert r.mount_root == '/gpfs/csfs1'
        assert r.mount_hosts == ['derecho', 'casper']


class TestQuotaEntryUtilization:

    def test_ratio(self):
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=970, file_count=1)
        assert 0.96 < qe.utilization < 0.98

    def test_zero_limit_returns_zero(self):
        qe = QuotaEntry('fs', None, limit_bytes=0, usage_bytes=100, file_count=1)
        assert qe.utilization == 0.0


class TestUtilSuffix:
    """Bookend annotations: ⚠ for high util, ↓ for low util."""

    def test_high_util_above_threshold(self):
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=970, file_count=1)
        out = _util_suffix(qe)
        assert '⚠' in out and '97%' in out

    def test_high_util_just_below_threshold_no_marker(self):
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=940, file_count=1)
        assert _util_suffix(qe) == ''

    def test_low_util_below_threshold(self):
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=30, file_count=1)
        out = _util_suffix(qe)
        assert '↓' in out and '3%' in out

    def test_low_util_just_above_threshold_no_marker(self):
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=60, file_count=1)
        assert _util_suffix(qe) == ''

    def test_empty_fileset_with_limit_is_flagged_low(self):
        # 0% usage with a non-zero limit → under-used; flag it.
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=1000, usage_bytes=0, file_count=0)
        out = _util_suffix(qe)
        assert '↓' in out and '0%' in out

    def test_zero_limit_skips_low_util(self):
        # limit==0 → utilization clamps to 0.0, but we MUST NOT treat that
        # as "under-used" (an umbrella with no quota carries no meaning).
        from cli.accounting.display import _util_suffix
        qe = QuotaEntry('fs', None, limit_bytes=0, usage_bytes=0, file_count=0)
        assert _util_suffix(qe) == ''


class TestPathVerifierLocal:

    def test_mix_of_present_and_missing(self, tmp_path):
        real = tmp_path / 'real'
        real.mkdir()
        missing = tmp_path / 'nope'
        v = PathVerifier('local')
        out = v.check([str(real), str(missing)])
        assert out == {str(real): True, str(missing): False}

    def test_rejects_newline_in_path(self):
        v = PathVerifier('local')
        with pytest.raises(PathVerificationError):
            v.check(['/tmp/with\nnewline'])

    def test_ssh_requires_host(self):
        with pytest.raises(ValueError):
            PathVerifier('ssh')


@pytest.mark.timeout(15)
class TestPathVerifierSSH:
    """SSH mode with `subprocess.run` mocked — no real network calls.

    The class-level timeout is defense in depth: all tests mock
    `subprocess.run` so no SSH actually runs, but a regression that
    accidentally bypasses the mock would otherwise hang on a real DNS
    lookup.
    """

    def _run_ok(self, paths_present: set[str]):
        def _run(cmd, *, input, capture_output, text, timeout, check):
            # The real code sends '\n'.join(paths) + '\n'; split handles both.
            paths = [p for p in input.splitlines() if p]
            lines = [
                f"{'EXISTS' if p in paths_present else 'MISSING'} {p}"
                for p in paths
            ]
            return subprocess.CompletedProcess(
                cmd, 0, stdout='\n'.join(lines), stderr='',
            )
        return _run

    def test_happy_path(self):
        v = PathVerifier('ssh', host='derecho')
        with patch('subprocess.run', side_effect=self._run_ok({'/a', '/c'})):
            out = v.check(['/a', '/b', '/c'])
        assert out == {'/a': True, '/b': False, '/c': True}

    def test_ssh_nonzero_raises(self):
        v = PathVerifier('ssh', host='derecho')
        def _run(*a, **kw):
            raise subprocess.CalledProcessError(
                255, a[0], output='', stderr='Permission denied'
            )
        with patch('subprocess.run', side_effect=_run):
            with pytest.raises(PathVerificationError,
                               match='SSH path verification failed'):
                v.check(['/a'])

    def test_ssh_timeout_raises(self):
        v = PathVerifier('ssh', host='derecho')
        def _run(*a, **kw):
            raise subprocess.TimeoutExpired(a[0], 60)
        with patch('subprocess.run', side_effect=_run):
            with pytest.raises(PathVerificationError,
                               match='timed out'):
                v.check(['/a'])

    def test_incomplete_output_raises(self):
        v = PathVerifier('ssh', host='derecho')
        def _run(*a, **kw):
            return subprocess.CompletedProcess(
                a[0], 0, stdout='EXISTS /a\n', stderr='',
            )
        with patch('subprocess.run', side_effect=_run):
            with pytest.raises(PathVerificationError,
                               match='incomplete'):
                v.check(['/a', '/b'])

    def test_stdin_has_trailing_newline_for_last_path(self):
        """Regression: the final path was being dropped because
        `read` returned non-zero on EOF-mid-line. We now append a
        trailing newline AND use `|| [ -n "$p" ]` in the loop.
        """
        captured = {}
        def _run(cmd, *, input, **kw):
            captured['input'] = input
            # Respond for every path so the parser doesn't raise
            paths = [p for p in input.splitlines() if p]
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout='\n'.join(f'MISSING {p}' for p in paths),
                stderr='',
            )
        v = PathVerifier('ssh', host='derecho')
        with patch('subprocess.run', side_effect=_run):
            v.check(['/a', '/b', '/c'])
        assert captured['input'].endswith('\n'), (
            "stdin must end with newline so remote read sees the last path"
        )


class TestAutoDetectVerifier:

    def test_local_when_mounted(self, tmp_path, monkeypatch):
        monkeypatch.setattr('os.path.ismount', lambda p: p == str(tmp_path))
        v, banner = auto_detect_verifier(
            mount_root=str(tmp_path),
            mount_hosts=['derecho'],
            explicit_host=None,
        )
        assert v.mode == 'local'
        assert banner == 'local'

    def test_explicit_host_wins_when_unmounted(self, monkeypatch):
        monkeypatch.setattr('os.path.ismount', lambda p: False)
        v, banner = auto_detect_verifier(
            mount_root='/gpfs/csfs1',
            mount_hosts=['derecho', 'casper'],
            explicit_host='casper',
        )
        assert v.mode == 'ssh'
        assert v.host == 'casper'
        assert 'casper' in banner

    def test_probes_default_hosts_in_order(self, monkeypatch):
        monkeypatch.setattr('os.path.ismount', lambda p: False)
        tried = []
        def probe(host, mount_root, *, timeout=5):
            tried.append(host)
            return host == 'casper'      # only casper responds
        monkeypatch.setattr(PathVerifier, 'probe_host', staticmethod(probe))
        v, banner = auto_detect_verifier(
            mount_root='/gpfs/csfs1',
            mount_hosts=['derecho', 'casper'],
            explicit_host=None,
        )
        assert tried == ['derecho', 'casper']
        assert v.host == 'casper'
        assert 'ssh casper' in banner

    def test_all_probes_fail_raises(self, monkeypatch):
        monkeypatch.setattr('os.path.ismount', lambda p: False)
        monkeypatch.setattr(
            PathVerifier, 'probe_host',
            staticmethod(lambda h, m, timeout=5: False),
        )
        with pytest.raises(PathVerificationError,
                           match='not mounted locally'):
            auto_detect_verifier(
                mount_root='/gpfs/csfs1',
                mount_hosts=['derecho', 'casper'],
                explicit_host=None,
            )


@pytest.mark.timeout(30)
class TestVerifyPathsIntegration:
    """End-to-end classification with --verify-paths.

    Class-level timeout guards against accidental real-subprocess calls
    if the local-mount monkeypatches ever regress.
    """

    def test_orphan_with_live_path_is_suppressed_without_force(
        self, session, tmp_path, monkeypatch,
    ):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=5.0)

        live_dir = tmp_path / 'live'
        live_dir.mkdir()
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name=str(live_dir),
        )

        quota_path = _write_quota_file(tmp_path, {})  # orphan
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)

        # Force local verifier with a mount_root the project dir is under
        monkeypatch.setattr(qr_mod.GpfsQuotaReader, 'mount_root', str(tmp_path))
        monkeypatch.setattr('os.path.ismount', lambda p: p == str(tmp_path))
        # Auto-confirm any interactive prompt — the live-path gate, not the
        # prompt, is what should suppress deactivation here.
        monkeypatch.setattr('rich.prompt.Confirm.ask', lambda *a, **kw: True)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path,
            dry_run=False, force=False,
            verify_paths=True, verify_host=None,
        )
        assert rc == 0
        session.refresh(alloc)
        # Live path + no --force → NOT deactivated
        assert alloc.end_date is None or alloc.is_active

    def test_orphan_with_live_path_deactivates_with_force(
        self, session, tmp_path, monkeypatch,
    ):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=5.0)

        live_dir = tmp_path / 'live2'
        live_dir.mkdir()
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name=str(live_dir),
        )

        quota_path = _write_quota_file(tmp_path, {})
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)
        monkeypatch.setattr(qr_mod.GpfsQuotaReader, 'mount_root', str(tmp_path))
        monkeypatch.setattr('os.path.ismount', lambda p: p == str(tmp_path))

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path,
            dry_run=False, force=True,
            verify_paths=True, verify_host=None,
        )
        assert rc == 0
        session.refresh(alloc)
        assert alloc.end_date is not None
        assert alloc.end_date.date() == date.today()

    def test_orphan_with_missing_path_deactivates_normally(
        self, session, tmp_path, monkeypatch,
    ):
        resource = _isolated_disk_resource(session, monkeypatch)
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=5.0)
        # Register a ProjectDirectory that does NOT exist on disk
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name=str(tmp_path / 'ghost'),
        )

        quota_path = _write_quota_file(tmp_path, {})
        admin = make_user(session)
        monkeypatch.setenv('SAM_ADMIN_USER', admin.username)
        monkeypatch.setattr(qr_mod.GpfsQuotaReader, 'mount_root', str(tmp_path))
        monkeypatch.setattr('os.path.ismount', lambda p: p == str(tmp_path))
        monkeypatch.setattr('rich.prompt.Confirm.ask', lambda *a, **kw: True)

        cmd = AccountingAdminCommand(_ctx_with_session(session))
        rc = cmd._run_reconcile_quotas(
            resource_name=resource.resource_name,
            quota_path=quota_path,
            dry_run=False, force=False,
            verify_paths=True, verify_host=None,
        )
        assert rc == 0
        session.refresh(alloc)
        # Path missing → safe to deactivate even without --force
        assert alloc.end_date is not None
