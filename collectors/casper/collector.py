#!/usr/bin/env python3
"""
Casper DAV Status Collector
"""
import sys
from pathlib import Path

# Ensure the 'lib' directory is in the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.base_collector import BaseCollector, main_runner
from lib.parsers.nodes import NodeParser


class CasperCollector(BaseCollector):
    """Casper-specific data collector."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        super().__init__(system_name, dry_run, json_only)

    def _collect_node_data(self, data: dict):
        """Collects and processes Casper-specific node data."""
        try:
            self.logger.info("Collecting node data...")
            nodes_json = self.pbs.get_nodes_json()

            # Aggregate stats for all node types (CPU, GPU, VIZ)
            node_stats = NodeParser.parse_nodes(nodes_json, 'casper')
            data.update({
                'cpu_nodes_total': node_stats.get('cpu_nodes_total', 0),
                'cpu_nodes_available': node_stats.get('cpu_nodes_available', 0),
                'cpu_nodes_down': node_stats.get('cpu_nodes_down', 0),
                'cpu_nodes_reserved': node_stats.get('cpu_nodes_reserved', 0),
                'cpu_cores_total': node_stats.get('cpu_cores_total', 0),
                'cpu_cores_allocated': node_stats.get('cpu_cores_allocated', 0),
                'cpu_cores_idle': node_stats.get('cpu_cores_idle', 0),
                'cpu_utilization_percent': node_stats.get('cpu_utilization_percent'),
                'gpu_nodes_total': node_stats.get('gpu_nodes_total', 0),
                'gpu_nodes_available': node_stats.get('gpu_nodes_available', 0),
                'gpu_nodes_down': node_stats.get('gpu_nodes_down', 0),
                'gpu_nodes_reserved': node_stats.get('gpu_nodes_reserved', 0),
                'gpu_count_total': node_stats.get('gpu_count_total', 0),
                'gpu_count_allocated': node_stats.get('gpu_count_allocated', 0),
                'gpu_count_idle': node_stats.get('gpu_count_idle', 0),
                'gpu_utilization_percent': node_stats.get('gpu_utilization_percent'),
                'viz_nodes_total': node_stats.get('viz_nodes_total', 0),
                'viz_nodes_available': node_stats.get('viz_nodes_available', 0),
                'viz_nodes_down': node_stats.get('viz_nodes_down', 0),
                'viz_nodes_reserved': node_stats.get('viz_nodes_reserved', 0),
                'viz_count_total': node_stats.get('viz_count_total', 0),
                'viz_count_allocated': node_stats.get('viz_count_allocated', 0),
                'viz_count_idle': node_stats.get('viz_count_idle', 0),
                'viz_utilization_percent': node_stats.get('viz_utilization_percent'),
                'memory_total_gb': node_stats.get('memory_total_gb', 0.0),
                'memory_allocated_gb': node_stats.get('memory_allocated_gb', 0.0),
                'memory_utilization_percent': node_stats.get('memory_utilization_percent'),
            })

            # Node type breakdown is specific to Casper
            data['node_types'] = NodeParser.parse_node_types(nodes_json, 'casper')

            self.logger.info(
                f"  CPU Nodes: {data['cpu_nodes_total']} total, "
                f"{data['cpu_nodes_available']} available"
            )
            self.logger.info(
                f"  GPU Nodes: {data['gpu_nodes_total']} total, "
                f"{data['gpu_nodes_available']} available"
            )
            self.logger.info(
                f"  VIZ Nodes: {data['viz_nodes_total']} total, "
                f"{data['viz_nodes_available']} available"
            )
            self.logger.info(f"  Node types: {len(data['node_types'])} types tracked")

        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}", exc_info=True)
            data.update({
                'cpu_nodes_total': 0, 'cpu_nodes_available': 0, 'cpu_nodes_down': 0,
                'cpu_nodes_reserved': 0, 'cpu_cores_total': 0, 'cpu_cores_allocated': 0,
                'cpu_cores_idle': 0, 'cpu_utilization_percent': None,
                'gpu_nodes_total': 0, 'gpu_nodes_available': 0, 'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0, 'gpu_count_total': 0, 'gpu_count_allocated': 0,
                'gpu_count_idle': 0, 'gpu_utilization_percent': None,
                'viz_nodes_total': 0, 'viz_nodes_available': 0, 'viz_nodes_down': 0,
                'viz_nodes_reserved': 0, 'viz_count_total': 0, 'viz_count_allocated': 0,
                'viz_count_idle': 0, 'viz_utilization_percent': None,
                'memory_total_gb': 0.0, 'memory_allocated_gb': 0.0,
                'memory_utilization_percent': None, 'node_types': []
            })


if __name__ == '__main__':
    sys.exit(main_runner(CasperCollector, 'casper', 'Casper DAV Status Collector'))
