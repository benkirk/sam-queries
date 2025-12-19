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

from lib.base_collector import BaseCollector, main_runner
from lib.parsers.jupyterhub_nodes import JupyterHubNodeParser
from lib.exceptions import SSHError

# Import requests for API calls
import requests
import urllib3

# Disable SSL warnings for JupyterHub API
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class JupyterHubCollector(BaseCollector):
    """JupyterHub-specific data collector."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        super().__init__(system_name, dry_run, json_only)

        # JupyterHub API configuration
        self.base_url = os.getenv('JUPYTERHUB_API_URL', 'https://jupyterhub.hpc.ucar.edu')
        self.instance = os.getenv('JUPYTERHUB_INSTANCE', 'stable')
        self.timeout = 30

        self.logger.info(f"Initialized JupyterHub collector for {self.instance} instance")

    def _resolve_api_token(self) -> str:
        """
        Resolve JupyterHub API token from environment or file.

        Priority:
        1. JUPYTERHUB_API_TOKEN environment variable
        2. Token file: /ncar/usr/jupyterhub.hpc.ucar.edu/.{instance}_metrics_api_token

        Returns:
            API token string

        Raises:
            RuntimeError: If no token found
        """
        # Priority 1: Environment variable
        if token := os.getenv('JUPYTERHUB_API_TOKEN'):
            self.logger.debug("Using API token from environment variable")
            return token

        # Priority 2: Token file
        token_file = Path(f'/ncar/usr/jupyterhub.hpc.ucar.edu/.{self.instance}_metrics_api_token')
        if token_file.exists():
            try:
                token = token_file.read_text().strip()
                if token:
                    self.logger.debug(f"Using API token from file: {token_file}")
                    return token
            except (IOError, OSError) as e:
                self.logger.warning(f"Failed to read token file {token_file}: {e}")

        raise RuntimeError(
            f"No API token found. Set JUPYTERHUB_API_TOKEN environment variable "
            f"or provide token file at {token_file}"
        )

    def _calculate_statistics(self, users: list) -> dict:
        """
        Calculate statistics from JupyterHub API users response.

        Parses /hub/api/users?state=active response to calculate:
        - active_users: Count of unique usernames
        - active_sessions: Total count of server sessions
        - casper_login_jobs: Sessions with resource='cr-login'
        - casper_batch_jobs: Sessions with resource='cr-batch'
        - derecho_batch_jobs: Sessions with resource='de-batch'
        - broken_jobs: Sessions missing required state fields

        Args:
            users: List of user dicts from JupyterHub API

        Returns:
            Dict with calculated statistics
        """
        unique_users = set()
        job_counts = {
            'casper_login': 0,
            'casper_batch': 0,
            'derecho_batch': 0,
            'broken': 0
        }
        total_sessions = 0

        # Parse each user from API response
        for user in users:
            username = user.get('name')
            if username:
                unique_users.add(username)

            # Each user can have multiple servers
            servers = user.get('servers', {})
            for server_name, server in servers.items():
                total_sessions += 1

                # Get resource type from server state
                try:
                    resource = server['state']['resource']
                except (KeyError, TypeError):
                    self.logger.warning(
                        f"Missing resource for user {username}, server {server_name}"
                    )
                    job_counts['broken'] += 1
                    continue

                # Classify by resource type
                if resource == 'cr-login':
                    job_counts['casper_login'] += 1
                elif resource == 'cr-batch':
                    job_counts['casper_batch'] += 1
                elif resource == 'de-batch':
                    job_counts['derecho_batch'] += 1

                # Detect broken jobs (missing required fields in child_state)
                try:
                    child_state = server['state'].get('child_state', {})
                except (KeyError, TypeError, AttributeError):
                    child_state = {}

                if resource in ('cr-login', 'cr-batch', 'de-batch'):
                    # PBS jobs require job_id
                    if 'job_id' not in child_state:
                        job_counts['broken'] += 1
                else:
                    # Other jobs require remote_ip and pid
                    if 'remote_ip' not in child_state or 'pid' not in child_state:
                        job_counts['broken'] += 1

        return {
            'active_users': len(unique_users),
            'active_sessions': total_sessions,
            'casper_login_jobs': job_counts['casper_login'],
            'casper_batch_jobs': job_counts['casper_batch'],
            'derecho_batch_jobs': job_counts['derecho_batch'],
            'broken_jobs': job_counts['broken']
        }

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

        Makes a direct API call to /hub/api/users?state=active to get real-time
        statistics about active sessions.

        Extracts: active_sessions, active_users, casper_login_jobs, casper_batch_jobs,
                  derecho_batch_jobs, broken_jobs.
        """
        try:
            self.logger.info("Collecting job data from JupyterHub API...")

            # Resolve API token
            api_token = self._resolve_api_token()

            # Make API call
            url = f'{self.base_url}/{self.instance}/hub/api/users?state=active'
            self.logger.debug(f"Making request to: {url}")

            response = requests.get(
                url,
                headers={'Authorization': f'token {api_token}'},
                verify=False,  # Matches existing behavior
                timeout=self.timeout
            )

            # Handle HTTP errors
            if response.status_code == 401:
                raise RuntimeError("Invalid API token (401 Unauthorized)")
            elif response.status_code == 403:
                raise RuntimeError("Access forbidden (403 Forbidden)")
            elif response.status_code >= 400:
                raise RuntimeError(f"API error: {response.status_code} - {response.text}")

            # Parse response
            users = response.json()

            # Calculate statistics
            stats = self._calculate_statistics(users)

            # Map stats to data dict
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

        except requests.exceptions.RequestException as e:
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
