import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

class TestJupyterHubCLI(unittest.TestCase):

    @patch('system_status.cli.create_status_engine')
    @patch('system_status.cli.Console')
    @patch('system_status.cli.Table')
    def test_display_jupyterhub_content(self, mock_table_cls, mock_console_cls, mock_create_engine):
        # Setup Mocks
        mock_console = MagicMock()
        mock_console_cls.return_value = mock_console
        
        mock_session = MagicMock()
        mock_session_local = MagicMock(return_value=mock_session)
        mock_create_engine.return_value = (MagicMock(), mock_session_local)

        # Mock Status Object
        mock_status = MagicMock()
        mock_status.timestamp.strftime.return_value = "2025-01-01 12:00:00"
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
        mock_status.nodes = [{'name': 'testnode1', 'state': 'free', 'jobs_running': 5, 'cpus_used': 4, 'cpus_total': 32, 'memory_used_gb': 10, 'memory_total_gb': 100}]

        mock_session.query.return_value.order_by.return_value.first.return_value = mock_status

        from system_status.cli import SystemStatusCLI
        cli = SystemStatusCLI()
        
        # Mock the Table instances created
        # We expect 4 tables: Service, Job Breakdown, Node Summary, Node List
        mock_service_table = MagicMock()
        mock_job_table = MagicMock()
        mock_node_summary_table = MagicMock()
        mock_node_list_table = MagicMock()
        
        # side_effect to return different mocks for each call
        mock_table_cls.side_effect = [mock_service_table, mock_job_table, mock_node_summary_table, mock_node_list_table]

        cli._display_jupyterhub()

        # Check Job Table content
        # Job table is the 2nd table created
        
        # Verify job_table rows
        found_casper_login = False
        for call in mock_job_table.add_row.call_args_list:
            args = call[0] # tuple of args
            # We check if values are in args. Note that args might contain strings or Text objects.
            # We look for string representation.
            args_str = [str(a) for a in args]
            if 'Casper Login Jobs' in args_str and '111' in args_str:
                found_casper_login = True
        self.assertTrue(found_casper_login, "Did not find Casper Login Jobs row in table")

        # Verify Node Summary Table content
        found_free_nodes = False
        for call in mock_node_summary_table.add_row.call_args_list:
            args = call[0]
            args_str = [str(a) for a in args]
            if 'Free Nodes' in args_str:
                # The value might be wrapped in [green]...[/green]
                if '100' in args_str[1]:
                    found_free_nodes = True
        self.assertTrue(found_free_nodes, "Did not find Free Nodes row in summary table")

        # Verify Node List Table content
        found_testnode = False
        for call in mock_node_list_table.add_row.call_args_list:
            args = call[0]
            args_str = [str(a) for a in args]
            if 'testnode1' in args_str:
                found_testnode = True
        self.assertTrue(found_testnode, "Did not find testnode1 in node list table")

if __name__ == '__main__':
    unittest.main()
