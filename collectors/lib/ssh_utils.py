"""
SSH utilities for login node collection.

Now uses parallel execution via ThreadPoolExecutor for significant speedup.
Sequential: ~2-3s per node × 8 nodes = ~20s
Parallel: ~2-3s total for all nodes
"""

import logging
import subprocess
from typing import List, Dict

try:
    from .exceptions import SSHError
    from .parallel_ssh import ParallelSSHCollector
except ImportError:
    from exceptions import SSHError
    from parallel_ssh import ParallelSSHCollector


class LoginNodeCollector:
    """Collect login node metrics via SSH in parallel."""

    def __init__(self, base_host: str, timeout: int = 10):
        self.base_host = base_host
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def collect_login_node_data(self, login_nodes: List[dict]) -> List[dict]:
        """
        Collect metrics from all login nodes in parallel.

        Args:
            login_nodes: List of dicts with 'name' and optionally 'type'
                Example: [{'name': 'derecho1', 'type': 'cpu'}, ...]

        Returns:
            List of login node status dicts
        """
        # Create parallel collector
        parallel_collector = ParallelSSHCollector(
            self.base_host,
            timeout=self.timeout,
            max_workers=10
        )

        # Run all node collections in parallel
        return parallel_collector.run_parallel(
            login_nodes,
            self._collect_single_node_safe
        )

    def _collect_single_node_safe(self, node_info: dict) -> dict:
        """
        Wrapper that handles errors and returns degraded entry on failure.

        This is called by the parallel executor for each node. It handles
        exceptions gracefully and returns a degraded entry if collection fails.

        Args:
            node_info: Dict with 'name' and optionally 'type'

        Returns:
            Node status dict (success or degraded)
        """
        # Handle both dict and string formats
        if isinstance(node_info, dict):
            node_name = node_info['name']
            node_type = node_info.get('type')
        else:
            node_name = node_info
            node_type = None

        try:
            # Collect data from single node
            data = self._collect_single_node(node_name)
            data['node_name'] = node_name
            if node_type:
                data['node_type'] = node_type

            self.logger.debug(
                f"Collected from {node_name}: users={data['user_count']}, "
                f"load={data['load_1min']}"
            )
            return data

        except Exception as e:
            self.logger.warning(f"Failed to collect from {node_name}: {e}")
            # Return degraded entry
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
            return entry

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
