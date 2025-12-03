#!/usr/bin/env python3
"""
JupyterHub Status Collector

Collects JupyterHub node status and active sessions
by SSH'ing to casper and running custom scripts.
"""
import sys
import subprocess
import logging
from pathlib import Path

# Ensure the 'lib' directory is in the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.base_collector import BaseCollector, main_runner
from lib.parsers.jupyterhub_nodes import JupyterHubNodeParser
from lib.exceptions import SSHError


class JupyterHubCollector(BaseCollector):
    """JupyterHub-specific data collector."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        super().__init__(system_name, dry_run, json_only)

    def _run_ssh_command(self, command: str, timeout: int = 30) -> str:
        """
        Run a command on casper via SSH.

        Args:
            command: The command to run
            timeout: Command timeout in seconds

        Returns:
            Command output as string

        Raises:
            SSHError: If command fails or times out
        """
        ssh_cmd = f'ssh -o ConnectTimeout=10 {self.config.pbs_host} "{command}"'

        self.logger.debug(f"Running SSH command: {ssh_cmd}")

        try:
            result = subprocess.run(
                ssh_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise SSHError(f"Command timed out: {command}")

        if result.returncode != 0:
            raise SSHError(f"Command failed: {command}\nError: {result.stderr}")

        return result.stdout

    def _collect_node_data(self, data: dict):
        """
        Collect JupyterHub node data by running jhlnodes on casper.

        This replaces the standard PBS node collection.
        """
        try:
            self.logger.info("Collecting JupyterHub node data...")

            # Run jhlnodes on casper
            output = self._run_ssh_command(
                '/glade/u/home/csgteam/bin/jhlnodes',
                timeout=30
            )

            # Parse the output
            node_stats = JupyterHubNodeParser.parse_jhlnodes(output)

            # Update data with parsed stats
            data.update({
                'nodes_total': node_stats.get('nodes_total', 0),
                'nodes_free': node_stats.get('nodes_free', 0),
                'nodes_busy': node_stats.get('nodes_busy', 0),
                'nodes_down': node_stats.get('nodes_down', 0),
                'cpus_total': node_stats.get('cpus_total', 0),
                'cpus_free': node_stats.get('cpus_free', 0),
                'cpus_used': node_stats.get('cpus_used', 0),
                'cpu_utilization_percent': node_stats.get('cpu_utilization_percent', 0.0),
                'gpus_total': node_stats.get('gpus_total', 0),
                'gpus_free': node_stats.get('gpus_free', 0),
                'gpus_used': node_stats.get('gpus_used', 0),
                'gpu_utilization_percent': node_stats.get('gpu_utilization_percent', 0.0),
                'memory_total_gb': node_stats.get('memory_total_gb', 0.0),
                'memory_free_gb': node_stats.get('memory_free_gb', 0.0),
                'memory_used_gb': node_stats.get('memory_used_gb', 0.0),
                'memory_utilization_percent': node_stats.get('memory_utilization_percent', 0.0),
                'jobs_running': node_stats.get('jobs_running', 0),
                'nodes': node_stats.get('nodes', [])
            })

            self.logger.info(
                f"  Nodes: {data['nodes_total']} total, "
                f"{data['nodes_free']} free, "
                f"{data['nodes_busy']} busy"
            )
            self.logger.info(
                f"  CPUs: {data['cpus_used']}/{data['cpus_total']} used "
                f"({data['cpu_utilization_percent']}%)"
            )
            self.logger.info(
                f"  Jobs running: {data['jobs_running']}"
            )

        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}", exc_info=True)
            data.update({
                'nodes_total': 0,
                'nodes_free': 0,
                'nodes_busy': 0,
                'nodes_down': 0,
                'cpus_total': 0,
                'cpus_free': 0,
                'cpus_used': 0,
                'cpu_utilization_percent': 0.0,
                'gpus_total': 0,
                'gpus_free': 0,
                'gpus_used': 0,
                'gpu_utilization_percent': 0.0,
                'memory_total_gb': 0.0,
                'memory_free_gb': 0.0,
                'memory_used_gb': 0.0,
                'memory_utilization_percent': 0.0,
                'jobs_running': 0,
                'nodes': []
            })

    def _collect_job_data(self, data: dict):
        """
        Collect JupyterHub job metrics from jhstat.

        Runs jhstat once and parses locally for efficiency.
        Extracts: active_sessions, active_users, casper_login_jobs, casper_batch_jobs,
                  derecho_batch_jobs, broken_jobs.
        """
        try:
            self.logger.info("Collecting job data from jhstat...")

            # Run jhstat once to get all job data
            output = self._run_ssh_command(
                "/glade/u/home/csgteam/bin/jhstat stable",
                timeout=30
            )

            # Parse the output locally (skip first 3 header lines)
            lines = output.strip().split('\n')
            data_lines = [line.strip() for line in lines[3:] if line.strip()]

            # Active sessions = total number of jobs
            data['active_sessions'] = len(data_lines)

            # Active users = unique usernames (first column)
            usernames = []
            casper_login_jobs = 0
            casper_batch_jobs = 0
            derecho_batch_jobs = 0
            broken_jobs = 0

            for line in data_lines:
                parts = line.split()
                if len(parts) > 0:
                    username = parts[0]
                    usernames.append(username)

                # Count job types by checking line content
                line_lower = line.lower()
                if 'cr-login' in line_lower:
                    casper_login_jobs += 1
                if 'cr-batch' in line_lower:
                    casper_batch_jobs += 1
                if 'de-batch' in line_lower:
                    derecho_batch_jobs += 1
                if 'broken' in line_lower:
                    broken_jobs += 1

            # Get unique users (preserving order with dict.fromkeys for Python 3.7+)
            data['active_users'] = len(list(dict.fromkeys(usernames)))
            data['casper_login_jobs'] = casper_login_jobs
            data['casper_batch_jobs'] = casper_batch_jobs
            data['derecho_batch_jobs'] = derecho_batch_jobs
            data['jobs_suspended'] = broken_jobs  # broken_jobs stored as jobs_suspended

            self.logger.info(f"  Active sessions: {data['active_sessions']}")
            self.logger.info(f"  Active users: {data['active_users']}")
            self.logger.info(f"  Casper login jobs: {data['casper_login_jobs']}")
            self.logger.info(f"  Casper batch jobs: {data['casper_batch_jobs']}")
            self.logger.info(f"  Derecho batch jobs: {data['derecho_batch_jobs']}")
            self.logger.info(f"  Broken jobs: {data['jobs_suspended']}")

        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}", exc_info=True)
            data.update({
                'active_sessions': 0,
                'active_users': 0,
                'casper_login_jobs': 0,
                'casper_batch_jobs': 0,
                'derecho_batch_jobs': 0,
                'jobs_suspended': 0
            })

    def collect(self):
        """
        Collect all JupyterHub metrics.

        Overrides BaseCollector.collect() to skip PBS-related collections
        and add JupyterHub-specific collections.
        """
        from datetime import datetime

        data = {'timestamp': datetime.now().isoformat()}

        # Collect JupyterHub-specific data
        self._collect_node_data(data)        # Uses jhlnodes (nodes, CPUs, memory, GPUs)
        self._collect_job_data(data)         # Uses jhstat (sessions, users, job types)

        # Skip these BaseCollector methods (not applicable to JupyterHub):
        # - _collect_login_node_data (JupyterHub has different model)
        # - _collect_filesystem_data (not tracked for JupyterHub)
        # - _collect_reservation_data (not applicable)

        return data


if __name__ == '__main__':
    sys.exit(main_runner(
        JupyterHubCollector,
        'jupyterhub',
        'JupyterHub Status Collector'
    ))
