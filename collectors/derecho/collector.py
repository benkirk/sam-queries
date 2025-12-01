#!/usr/bin/env python3
"""
Derecho HPC Status Collector
"""
import sys
from pathlib import Path

# Ensure the 'lib' directory is in the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.base_collector import BaseCollector, main_runner
from lib.parsers.nodes import NodeParser


class DerechoCollector(BaseCollector):
    """Derecho-specific data collector."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        super().__init__(system_name, dry_run, json_only)

    def _collect_node_data(self, data: dict):
        """Collects and processes Derecho-specific node data."""
        try:
            self.logger.info("Collecting node data...")
            nodes_json = self.pbs.get_nodes_json()
            node_stats = NodeParser.parse_nodes(nodes_json, 'derecho')
            data.update(node_stats)
            self.logger.info(
                f"  CPU nodes: {node_stats.get('cpu_nodes_total', 0)} total, "
                f"{node_stats.get('cpu_nodes_available', 0)} available"
            )
            self.logger.info(
                f"  GPU nodes: {node_stats.get('gpu_nodes_total', 0)} total, "
                f"{node_stats.get('gpu_nodes_available', 0)} available"
            )
        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}", exc_info=True)
            data.update({
                'cpu_nodes_total': 0, 'cpu_nodes_available': 0, 'cpu_nodes_down': 0,
                'cpu_nodes_reserved': 0, 'gpu_nodes_total': 0, 'gpu_nodes_available': 0,
                'gpu_nodes_down': 0, 'gpu_nodes_reserved': 0, 'cpu_cores_total': 0,
                'cpu_cores_allocated': 0, 'cpu_cores_idle': 0, 'gpu_count_total': 0,
                'gpu_count_allocated': 0, 'gpu_count_idle': 0, 'memory_total_gb': 0.0,
                'memory_allocated_gb': 0.0,
            })


if __name__ == '__main__':
    sys.exit(main_runner(DerechoCollector, 'derecho', 'Derecho HPC Status Collector'))
