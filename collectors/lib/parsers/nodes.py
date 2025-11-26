"""
PBS nodes parser with intelligent node type classification.
"""

import logging
from typing import Dict, List, Optional


def parse_memory(mem_str: str) -> int:
    """
    Convert PBS memory string (e.g., '256gb', '512mb') to KB.

    Args:
        mem_str: Memory string from PBS

    Returns:
        Memory in KB
    """
    if not mem_str:
        return 0

    mem_str = mem_str.lower()
    try:
        if 'tb' in mem_str:
            return int(float(mem_str.replace('tb', '')) * 1024 * 1024 * 1024)
        elif 'gb' in mem_str:
            return int(float(mem_str.replace('gb', '')) * 1024 * 1024)
        elif 'mb' in mem_str:
            return int(float(mem_str.replace('mb', '')) * 1024)
        elif 'kb' in mem_str:
            return int(float(mem_str.replace('kb', '')))
    except ValueError:
        pass
    return 0


def classify_node_type(node_name: str, node_data: dict, system_type: str) -> str:
    """
    Intelligently classify node type from PBS data.

    Uses resources_available fields like Qlist, cpu_type, gpu_type, ngpus to
    determine node type without hardcoded configs.

    Args:
        node_name: Node hostname
        node_data: Node data from pbsnodes JSON
        system_type: 'derecho' or 'casper'

    Returns:
        Node type string (e.g., 'cpu', 'gpu-a100', 'htc', 'largemem')
    """
    resources = node_data.get('resources_available', {})
    qlist = resources.get('Qlist', '').lower()
    ngpus = int(resources.get('ngpus', 0))
    gpu_type = resources.get('gpu_type', '').lower()
    cpu_type = resources.get('cpu_type', '').lower()
    mem_mb = parse_memory(resources.get('mem', '0mb')) / 1024

    if system_type == 'derecho':
        # Derecho: CPU vs GPU nodes
        if ngpus > 0:
            # GPU node - determine GPU type
            if 'a100' in gpu_type:
                return 'gpu-a100'
            elif 'h100' in gpu_type:
                return 'gpu-h100'
            else:
                return 'gpu'
        else:
            # CPU node
            return 'cpu'

    elif system_type == 'casper':
        # Casper: Multiple node types based on Qlist and resources
        if ngpus > 0:
            # GPU node - determine type from gpu_type field
            if 'h100' in gpu_type:
                return 'gpu-h100'
            elif 'a100' in gpu_type:
                return 'gpu-a100'
            elif 'l40' in gpu_type:
                return 'gpu-l40'
            elif 'gp100' in gpu_type:
                return 'gpu-gp100'
            else:
                return 'gpu'
        elif 'largemem' in qlist:
            return 'largemem'
        elif 'htc' in qlist or 'jhublogin' in qlist:
            return 'htc'
        else:
            # Standard compute node
            return 'standard'

    # Fallback
    return 'unknown'


class NodeParser:
    """Parse pbsnodes JSON output."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def parse_nodes(pbsnodes_json: dict, system_type: str) -> dict:
        """
        Parse pbsnodes JSON into node statistics.

        Args:
            pbsnodes_json: Output from pbsnodes -aj -F json
            system_type: 'derecho' or 'casper'

        Returns:
            Dict with node statistics
        """
        nodes = pbsnodes_json.get('nodes', {})

        stats = {
            'cpu_nodes_total': 0,
            'cpu_nodes_available': 0,
            'cpu_nodes_down': 0,
            'cpu_nodes_reserved': 0,
            'cpu_cores_total': 0,
            'cpu_cores_allocated': 0,
            'cpu_cores_idle': 0,
            'memory_total_gb': 0.0,
            'memory_allocated_gb': 0.0,
        }

        # Add GPU fields for Derecho
        if system_type == 'derecho':
            stats.update({
                'gpu_nodes_total': 0,
                'gpu_nodes_available': 0,
                'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0,
                'gpu_count_total': 0,
                'gpu_count_allocated': 0,
                'gpu_count_idle': 0,
            })

        for node_name, node_data in nodes.items():
            state = node_data.get('state', '').lower()
            resources = node_data.get('resources_available', {})
            resources_assigned = node_data.get('resources_assigned', {})

            # Determine node category
            node_type = classify_node_type(node_name, node_data, system_type)
            is_gpu = node_type.startswith('gpu')

            # For Derecho, categorize as cpu/gpu
            # For Casper, everything non-GPU counts as cpu for aggregate stats
            if system_type == 'derecho':
                node_category = 'gpu' if is_gpu else 'cpu'
            else:
                node_category = 'cpu'  # Aggregate all non-GPU as cpu for Casper

            # Count nodes by state
            if 'down' in state or 'offline' in state:
                stats[f'{node_category}_nodes_down'] += 1
            elif 'resv' in state:
                stats[f'{node_category}_nodes_reserved'] += 1
            elif 'free' in state or 'idle' in state:
                stats[f'{node_category}_nodes_available'] += 1
            # job-exclusive, job-busy also count toward total but not available

            stats[f'{node_category}_nodes_total'] += 1

            # Aggregate CPU resources (all nodes have CPUs)
            ncpus = int(resources.get('ncpus', 0))
            ncpus_allocated = int(resources_assigned.get('ncpus', 0))
            stats['cpu_cores_total'] += ncpus
            stats['cpu_cores_allocated'] += ncpus_allocated
            stats['cpu_cores_idle'] += (ncpus - ncpus_allocated)

            # Memory (convert to GB)
            mem_kb = parse_memory(resources.get('mem', '0kb'))
            mem_alloc_kb = parse_memory(resources_assigned.get('mem', '0kb'))
            stats['memory_total_gb'] += mem_kb / (1024 * 1024)
            stats['memory_allocated_gb'] += mem_alloc_kb / (1024 * 1024)

            # GPUs (Derecho only in aggregate stats)
            if system_type == 'derecho' and is_gpu:
                ngpus = int(resources.get('ngpus', 0))
                ngpus_allocated = int(resources_assigned.get('ngpus', 0))
                stats['gpu_count_total'] += ngpus
                stats['gpu_count_allocated'] += ngpus_allocated
                stats['gpu_count_idle'] += (ngpus - ngpus_allocated)

        # Calculate utilization percentages
        if stats['cpu_cores_total'] > 0:
            stats['cpu_utilization_percent'] = round(
                (stats['cpu_cores_allocated'] / stats['cpu_cores_total']) * 100, 2
            )
        else:
            stats['cpu_utilization_percent'] = 0.0

        if system_type == 'derecho' and stats.get('gpu_count_total', 0) > 0:
            stats['gpu_utilization_percent'] = round(
                (stats['gpu_count_allocated'] / stats['gpu_count_total']) * 100, 2
            )
        else:
            stats['gpu_utilization_percent'] = 0.0

        if stats['memory_total_gb'] > 0:
            stats['memory_utilization_percent'] = round(
                (stats['memory_allocated_gb'] / stats['memory_total_gb']) * 100, 2
            )
        else:
            stats['memory_utilization_percent'] = 0.0

        return stats

    @staticmethod
    def parse_node_types(pbsnodes_json: dict, system_type: str) -> List[dict]:
        """
        Parse nodes by type with detailed breakdown.

        This provides per-node-type statistics (useful for Casper).

        Args:
            pbsnodes_json: Output from pbsnodes -aj -F json
            system_type: 'derecho' or 'casper'

        Returns:
            List of node type status dicts
        """
        nodes = pbsnodes_json.get('nodes', {})
        node_types = {}

        for node_name, node_data in nodes.items():
            # Classify node type
            node_type = classify_node_type(node_name, node_data, system_type)

            if node_type not in node_types:
                resources = node_data.get('resources_available', {})
                node_types[node_type] = {
                    'node_type': node_type,
                    'nodes_total': 0,
                    'nodes_available': 0,
                    'nodes_down': 0,
                    'nodes_allocated': 0,
                    # Sample hardware specs from first node of this type
                    'cores_per_node': int(resources.get('ncpus', 0)),
                    'memory_gb_per_node': round(parse_memory(resources.get('mem', '0')) / (1024 * 1024), 2),
                    'gpus_per_node': int(resources.get('ngpus', 0)),
                    'gpu_model': resources.get('gpu_type', '').split(',')[0] if resources.get('gpu_type') else None,
                }

            state = node_data.get('state', '').lower()
            node_types[node_type]['nodes_total'] += 1

            if 'down' in state or 'offline' in state:
                node_types[node_type]['nodes_down'] += 1
            elif 'free' in state or 'idle' in state:
                node_types[node_type]['nodes_available'] += 1
            elif 'job-busy' in state or 'job-exclusive' in state:
                node_types[node_type]['nodes_allocated'] += 1

        # Calculate utilization per type
        for nt_data in node_types.values():
            if nt_data['nodes_total'] > 0:
                allocated = nt_data['nodes_allocated']
                total = nt_data['nodes_total']
                nt_data['utilization_percent'] = round((allocated / total) * 100, 2)
            else:
                nt_data['utilization_percent'] = 0.0

        return list(node_types.values())
