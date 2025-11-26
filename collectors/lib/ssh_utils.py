"""
SSH utilities for login node collection.
"""

import logging
import subprocess
from typing import List, Dict

try:
    from .exceptions import SSHError
except ImportError:
    from exceptions import SSHError


class LoginNodeCollector:
    """Collect login node metrics via SSH."""

    def __init__(self, base_host: str, timeout: int = 10):
        self.base_host = base_host
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def collect_login_node_data(self, login_nodes: List[dict]) -> List[dict]:
        """
        Collect metrics from all login nodes.

        Args:
            login_nodes: List of dicts with 'name' and optionally 'type'
                Example: [{'name': 'derecho1', 'type': 'cpu'}, ...]

        Returns:
            List of login node status dicts
        """
        results = []

        for node_info in login_nodes:
            # Handle both dict and string formats
            if isinstance(node_info, dict):
                node_name = node_info['name']
                node_type = node_info.get('type')
            else:
                node_name = node_info
                node_type = None

            try:
                data = self._collect_single_node(node_name)
                data['node_name'] = node_name
                if node_type:
                    data['node_type'] = node_type
                results.append(data)
                self.logger.debug(f"Collected from {node_name}: users={data['user_count']}, load={data['load_1min']}")
            except Exception as e:
                self.logger.warning(f"Failed to collect from {node_name}: {e}")
                # Add degraded entry
                entry = {
                    'node_name': node_name,
                    'available': False,
                    'degraded': True,
                    'user_count': None,
                    'load_1min': None,
                    'load_5min': None,
                    'load_15min': None,
                }
                if node_type:
                    entry['node_type'] = node_type
                results.append(entry)

        return results

    def _collect_single_node(self, node_name: str) -> dict:
        """Collect metrics from a single login node."""
        # SSH through base host to login node
        # Example: ssh derecho "ssh derecho1 'cat /proc/loadavg; echo ---; who | wc -l'"

        cmd = (
            f"ssh -o ConnectTimeout={self.timeout} {self.base_host} "
            f'"ssh {node_name} \'cat /proc/loadavg; echo ---; who | wc -l\'" '
        )

        self.logger.debug(f"Running: {cmd}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout * 2  # Double timeout for nested SSH
            )
        except subprocess.TimeoutExpired:
            raise SSHError(f"Timeout connecting to {node_name}")

        if result.returncode != 0:
            raise SSHError(f"Failed to connect to {node_name}: {result.stderr}")

        # Parse output
        try:
            parts = result.stdout.strip().split('---')
            loadavg = parts[0].strip().split()
            user_count = int(parts[1].strip())

            return {
                'available': True,
                'degraded': False,
                'user_count': user_count,
                'load_1min': float(loadavg[0]),
                'load_5min': float(loadavg[1]),
                'load_15min': float(loadavg[2]),
            }
        except (IndexError, ValueError) as e:
            raise SSHError(f"Failed to parse output from {node_name}: {e}")
