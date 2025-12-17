#!/usr/bin/env python3
"""
JupyterHub Status Collector

Collects JupyterHub node status and active sessions
using the JupyterHub Hub API.
"""
import sys
import os
import subprocess
import logging
from pathlib import Path

# Ensure the 'lib' directory is in the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Add webapp to path for JupyterHub API client
webapp_path = Path(__file__).resolve().parent.parent.parent / 'src'
sys.path.insert(0, str(webapp_path))

from lib.base_collector import BaseCollector, main_runner
from lib.parsers.jupyterhub_nodes import JupyterHubNodeParser
from lib.exceptions import SSHError
from webapp.clients.jupyterhub import JupyterHubClient, JupyterHubAPIError


class JupyterHubCollector(BaseCollector):
    """JupyterHub-specific data collector."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        super().__init__(system_name, dry_run, json_only)

        # Initialize JupyterHub API client
        base_url = os.getenv('JUPYTERHUB_API_URL', 'https://jupyterhub.hpc.ucar.edu')
        instance = os.getenv('JUPYTERHUB_INSTANCE', 'stable')
        cache_ttl = int(os.getenv('JUPYTERHUB_CACHE_TTL', '0'))  # No cache for collector

        self.jupyterhub_client = JupyterHubClient(
            base_url=base_url,
            instance=instance,
            cache_ttl=cache_ttl
        )
        self.logger.info(f"Initialized JupyterHub API client for {instance} instance")

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
        Collect JupyterHub job metrics using the JupyterHub Hub API.

        Uses the JupyterHub API to get real-time statistics about active sessions.
        Extracts: active_sessions, active_users, casper_login_jobs, casper_batch_jobs,
                  derecho_batch_jobs, broken_jobs.
        """
        try:
            self.logger.info("Collecting job data from JupyterHub API...")

            # Get statistics from JupyterHub API (no caching for collector)
            stats = self.jupyterhub_client.get_statistics(use_cache=False)

            # Map API response to data dict
            data['active_sessions'] = stats['active_sessions']
            data['active_users'] = stats['active_users']
            data['casper_login_jobs'] = stats['casper_login_jobs']
            data['casper_batch_jobs'] = stats['casper_batch_jobs']
            data['derecho_batch_jobs'] = stats['derecho_batch_jobs']
            data['jobs_suspended'] = stats['broken_jobs']  # broken_jobs stored as jobs_suspended

            self.logger.info(f"  Active sessions: {data['active_sessions']}")
            self.logger.info(f"  Active users: {data['active_users']}")
            self.logger.info(f"  Casper login jobs: {data['casper_login_jobs']}")
            self.logger.info(f"  Casper batch jobs: {data['casper_batch_jobs']}")
            self.logger.info(f"  Derecho batch jobs: {data['derecho_batch_jobs']}")
            self.logger.info(f"  Broken jobs: {data['jobs_suspended']}")

        except JupyterHubAPIError as e:
            self.logger.error(f"Failed to collect job data from API: {e}", exc_info=True)
            data.update({
                'active_sessions': 0,
                'active_users': 0,
                'casper_login_jobs': 0,
                'casper_batch_jobs': 0,
                'derecho_batch_jobs': 0,
                'jobs_suspended': 0
            })
        except Exception as e:
            self.logger.error(f"Unexpected error collecting job data: {e}", exc_info=True)
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
        self._collect_node_data(data)        # Uses SSH + jhlnodes (nodes, CPUs, memory, GPUs)
        self._collect_job_data(data)         # Uses JupyterHub API (sessions, users, job types)

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
