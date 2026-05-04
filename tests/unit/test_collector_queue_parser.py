"""Tests for collectors/lib/parsers/queues.py.

Covers both ``parse_queues`` (queue-grain rollup) and the new
``parse_user_project_queues`` (user/project/queue grain) — focusing on
the new path's user + Account_Name extraction, sentinel handling, and
counter aggregation.
"""

import sys
import os

import pytest

# Add collectors/lib to sys.path so the parser module imports cleanly
# without requiring the collector package to be installed.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "collectors", "lib"))

from parsers.queues import QueueParser  # noqa: E402


def _make_job(state, owner, account, queue, ncpus=0, ngpus=0, exec_host=None):
    """Build a single ``Jobs`` entry as it appears in qstat -f -F json."""
    job = {
        "job_state": state,
        "Job_Owner": owner,
        "queue": queue,
        "Resource_List": {"ncpus": ncpus, "ngpus": ngpus},
    }
    if account is not None:
        job["Account_Name"] = account
    if exec_host:
        job["exec_host"] = exec_host
    return job


def _qstat(jobs):
    return {"Jobs": {f"{i}.derecho-pbs": j for i, j in enumerate(jobs)}}


class TestParseUserProjectQueues:
    def test_groups_by_user_project_queue(self):
        qstat = _qstat([
            _make_job("R", "benkirk@derecho", "SCSG0001", "main", ncpus=64, exec_host="dn1/0+dn2/0"),
            _make_job("R", "benkirk@derecho", "SCSG0001", "main", ncpus=128, exec_host="dn3/0"),
            _make_job("Q", "benkirk@derecho", "SCSG0001", "main", ncpus=32),
            _make_job("R", "bdobbins@derecho", "SCSG0001", "main", ncpus=64, exec_host="dn4/0"),
            _make_job("H", "benkirk@derecho", "OTHER001", "main", ncpus=16),
        ])

        rows = QueueParser.parse_user_project_queues(qstat)
        by_key = {(r["username"], r["project_code"], r["queue_name"]): r for r in rows}

        assert len(rows) == 3  # (benkirk, SCSG0001, main), (bdobbins, ...), (benkirk, OTHER001, main)

        bk = by_key[("benkirk", "SCSG0001", "main")]
        assert bk["running_jobs"] == 2
        assert bk["pending_jobs"] == 1
        assert bk["held_jobs"] == 0
        assert bk["cores_allocated"] == 192
        assert bk["cores_pending"] == 32
        assert bk["nodes_allocated"] == 3  # dn1, dn2, dn3 unique

        bd = by_key[("bdobbins", "SCSG0001", "main")]
        assert bd["running_jobs"] == 1
        assert bd["cores_allocated"] == 64

        held = by_key[("benkirk", "OTHER001", "main")]
        assert held["held_jobs"] == 1
        assert held["cores_held"] == 16

    def test_missing_account_name_buckets_to_unknown(self):
        qstat = _qstat([
            _make_job("R", "benkirk@derecho", None, "main", ncpus=64, exec_host="dn1/0"),
            _make_job("Q", "benkirk@derecho", "", "main", ncpus=32),
            _make_job("R", "benkirk@derecho", "   ", "main", ncpus=16, exec_host="dn2/0"),
        ])

        rows = QueueParser.parse_user_project_queues(qstat)
        assert len(rows) == 1
        row = rows[0]
        assert row["username"] == "benkirk"
        assert row["project_code"] == QueueParser.UNKNOWN_PROJECT == "_unknown_"
        assert row["running_jobs"] == 2
        assert row["pending_jobs"] == 1
        assert row["cores_allocated"] == 80
        assert row["cores_pending"] == 32
        assert row["nodes_allocated"] == 2

    def test_owner_with_no_at_sign_is_kept_verbatim(self):
        """Some PBS configs report Job_Owner as just a username."""
        qstat = _qstat([
            _make_job("R", "benkirk", "SCSG0001", "main", ncpus=8, exec_host="dn1/0"),
        ])
        rows = QueueParser.parse_user_project_queues(qstat)
        assert len(rows) == 1
        assert rows[0]["username"] == "benkirk"

    def test_empty_owner_is_skipped(self):
        """If Job_Owner is missing entirely, we can't attribute the row."""
        qstat = _qstat([
            _make_job("R", "", "SCSG0001", "main", ncpus=8),
        ])
        rows = QueueParser.parse_user_project_queues(qstat)
        assert rows == []

    def test_no_jobs_returns_empty_list(self):
        assert QueueParser.parse_user_project_queues({}) == []
        assert QueueParser.parse_user_project_queues({"Jobs": {}}) == []

    def test_totals_reconcile_with_parse_queues(self):
        """Per-user totals should match the queue-grain rollup."""
        qstat = _qstat([
            _make_job("R", "benkirk@d", "SCSG0001", "main", ncpus=64, exec_host="dn1/0"),
            _make_job("R", "bdobbins@d", "SCSG0001", "main", ncpus=128, exec_host="dn2/0"),
            _make_job("Q", "benkirk@d", "OTHER001", "main", ncpus=32),
        ])
        per_user = QueueParser.parse_user_project_queues(qstat)
        per_queue = QueueParser.parse_queues("", qstat)

        assert len(per_queue) == 1
        q = per_queue[0]
        assert sum(r["running_jobs"] for r in per_user) == q["running_jobs"]
        assert sum(r["pending_jobs"] for r in per_user) == q["pending_jobs"]
        assert sum(r["cores_allocated"] for r in per_user) == q["cores_allocated"]
        assert sum(r["cores_pending"] for r in per_user) == q["cores_pending"]
