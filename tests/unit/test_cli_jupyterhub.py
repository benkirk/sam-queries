"""Tests for the sam-status JupyterHub CLI display.

Ported from tests/unit/test_cli_jupyterhub.py. Mocks out the session,
engine, console, and Table factory — zero database dependency.
Dropped the legacy `sys.path.insert` (unnecessary under new_tests/).
Converted from unittest.TestCase to plain pytest for consistency with
the rest of new_tests/.
"""
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.unit


@patch('system_status.cli.create_status_engine')
@patch('system_status.cli.Console')
@patch('system_status.cli.Table')
def test_display_jupyterhub_content(mock_table_cls, mock_console_cls, mock_create_engine):
    """`SystemStatusCLI._display_jupyterhub` renders the expected rows.

    The CLI queries for the latest JupyterHubStatus, then builds four
    rich Tables (Service, Job Breakdown, Node Summary, Node List). We
    intercept Table() construction and inspect the add_row() calls to
    verify the expected content ends up in each table.
    """
    # Console mock — not inspected, just kept out of stdout.
    mock_console_cls.return_value = MagicMock()

    # Session mock returning a canned JupyterHubStatus row.
    mock_session = MagicMock()
    mock_session_local = MagicMock(return_value=mock_session)
    mock_create_engine.return_value = (MagicMock(), mock_session_local)

    mock_status = MagicMock()
    mock_status.timestamp.strftime.return_value = "2100-01-01 12:00:00"
    mock_status.available = True
    mock_status.active_users = 10
    mock_status.active_sessions = 5
    mock_status.casper_login_jobs = 111
    mock_status.casper_batch_jobs = 222
    mock_status.derecho_batch_jobs = 333
    mock_status.jobs_suspended = 444
    mock_status.nodes_free = 100
    mock_status.nodes_busy = 50
    mock_status.nodes_down = 10
    mock_status.nodes = [{
        'name': 'testnode1',
        'state': 'free',
        'jobs_running': 5,
        'cpus_used': 4,
        'cpus_total': 32,
        'memory_used_gb': 10,
        'memory_total_gb': 100,
    }]
    mock_session.query.return_value.order_by.return_value.first.return_value = mock_status

    # Four Table() instances in order: service, job breakdown, node summary, node list.
    mock_service_table = MagicMock()
    mock_job_table = MagicMock()
    mock_node_summary_table = MagicMock()
    mock_node_list_table = MagicMock()
    mock_table_cls.side_effect = [
        mock_service_table, mock_job_table, mock_node_summary_table, mock_node_list_table
    ]

    from system_status.cli import SystemStatusCLI
    SystemStatusCLI()._display_jupyterhub()

    # Job breakdown — should include a "Casper Login Jobs" row with value 111.
    found_casper_login = False
    for call in mock_job_table.add_row.call_args_list:
        args_str = [str(a) for a in call[0]]
        if 'Casper Login Jobs' in args_str and '111' in args_str:
            found_casper_login = True
    assert found_casper_login, "Did not find 'Casper Login Jobs' row in job table"

    # Node summary — should include a "Free Nodes" row with value 100
    # (possibly wrapped in a rich Text or color markup).
    found_free_nodes = False
    for call in mock_node_summary_table.add_row.call_args_list:
        args_str = [str(a) for a in call[0]]
        if 'Free Nodes' in args_str and '100' in args_str[1]:
            found_free_nodes = True
    assert found_free_nodes, "Did not find 'Free Nodes' row in node summary table"

    # Node list — should include the mock node.
    found_testnode = False
    for call in mock_node_list_table.add_row.call_args_list:
        args_str = [str(a) for a in call[0]]
        if 'testnode1' in args_str:
            found_testnode = True
    assert found_testnode, "Did not find 'testnode1' in node list table"
