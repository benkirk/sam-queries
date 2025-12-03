"""
Parser for JupyterHub node data from jhlnodes command.
"""

import re
import logging
from typing import List, Dict, Optional


class JupyterHubNodeParser:
    """Parse jhlnodes command output."""

    @staticmethod
    def parse_memory(mem_str: str) -> tuple:
        """
        Parse memory string like '194gb/354gb' into (free, total) in GB.

        Returns:
            Tuple of (free_gb, total_gb) as floats
        """
        match = re.match(r'(\d+)gb/(\d+)gb', mem_str)
        if match:
            free = int(match.group(1))
            total = int(match.group(2))
            return (free, total)
        return (0, 0)

    @staticmethod
    def parse_resource(res_str: str) -> tuple:
        """
        Parse resource string like '2/34' into (free, total).

        Returns:
            Tuple of (free, total) as integers
        """
        match = re.match(r'(\d+)/(\d+)', res_str)
        if match:
            free = int(match.group(1))
            total = int(match.group(2))
            return (free, total)
        return (0, 0)

    @classmethod
    def parse_jhlnodes(cls, output: str) -> Dict:
        """
        Parse jhlnodes command output.

        Expected format:
                                                        mem       ncpus   nmics   ngpus
        vnode           state           njobs   run   susp      f/t        f/t     f/t     f/t   jobs
        --------------- --------------- ------ ----- ------ ------------ ------- ------- ------- -------
        crhtc50         free                16    16      0  194gb/354gb    2/34     0/0     0/0 662075,...

        Args:
            output: Raw output from jhlnodes command

        Returns:
            Dict with parsed node statistics and node details
        """
        logger = logging.getLogger(__name__)

        nodes = []
        total_nodes = 0
        nodes_free = 0
        nodes_busy = 0
        nodes_down = 0

        total_cpus = 0
        free_cpus = 0
        total_gpus = 0
        free_gpus = 0
        total_mem_gb = 0.0
        free_mem_gb = 0.0

        total_jobs = 0
        running_jobs = 0
        suspended_jobs = 0

        lines = output.strip().split('\n')

        # Skip header lines (first 3 lines)
        data_lines = [line for line in lines[3:] if line.strip() and not line.startswith('---')]

        for line in data_lines:
            # Split by whitespace, limit splits to handle job list at end
            parts = line.split(None, 10)

            if len(parts) < 9:
                logger.warning(f"Skipping malformed line: {line}")
                continue

            try:
                vnode = parts[0]
                state = parts[1]
                njobs = int(parts[2])
                run = int(parts[3])
                susp = int(parts[4])
                mem_str = parts[5]
                ncpus_str = parts[6]
                nmics_str = parts[7]
                ngpus_str = parts[8]
                jobs_str = parts[9] if len(parts) > 9 else ""

                # Parse memory
                mem_free, mem_total = cls.parse_memory(mem_str)

                # Parse CPUs
                cpus_free, cpus_total = cls.parse_resource(ncpus_str)

                # Parse GPUs
                gpus_free, gpus_total = cls.parse_resource(ngpus_str)

                # Build node entry
                node_entry = {
                    'name': vnode,
                    'state': state,
                    'jobs_total': njobs,
                    'jobs_running': run,
                    'jobs_suspended': susp,
                    'memory_free_gb': mem_free,
                    'memory_total_gb': mem_total,
                    'memory_used_gb': mem_total - mem_free,
                    'cpus_free': cpus_free,
                    'cpus_total': cpus_total,
                    'cpus_used': cpus_total - cpus_free,
                    'gpus_free': gpus_free,
                    'gpus_total': gpus_total,
                    'gpus_used': gpus_total - gpus_free,
                    'job_ids': jobs_str.strip() if jobs_str else ""
                }

                nodes.append(node_entry)

                # Aggregate stats
                total_nodes += 1
                if state == 'free':
                    nodes_free += 1
                elif state == 'job-busy':
                    nodes_busy += 1
                elif 'down' in state.lower():
                    nodes_down += 1

                total_cpus += cpus_total
                free_cpus += cpus_free
                total_gpus += gpus_total
                free_gpus += gpus_free
                total_mem_gb += mem_total
                free_mem_gb += mem_free

                total_jobs += njobs
                running_jobs += run
                suspended_jobs += susp

            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse line: {line} - {e}")
                continue

        # Calculate utilization percentages
        cpu_utilization = 0.0
        if total_cpus > 0:
            cpu_utilization = ((total_cpus - free_cpus) / total_cpus) * 100

        gpu_utilization = 0.0
        if total_gpus > 0:
            gpu_utilization = ((total_gpus - free_gpus) / total_gpus) * 100

        memory_utilization = 0.0
        if total_mem_gb > 0:
            memory_utilization = ((total_mem_gb - free_mem_gb) / total_mem_gb) * 100

        return {
            'nodes': nodes,
            'nodes_total': total_nodes,
            'nodes_free': nodes_free,
            'nodes_busy': nodes_busy,
            'nodes_down': nodes_down,
            'cpus_total': total_cpus,
            'cpus_free': free_cpus,
            'cpus_used': total_cpus - free_cpus,
            'cpu_utilization_percent': round(cpu_utilization, 2),
            'gpus_total': total_gpus,
            'gpus_free': free_gpus,
            'gpus_used': total_gpus - free_gpus,
            'gpu_utilization_percent': round(gpu_utilization, 2),
            'memory_total_gb': total_mem_gb,
            'memory_free_gb': free_mem_gb,
            'memory_used_gb': total_mem_gb - free_mem_gb,
            'memory_utilization_percent': round(memory_utilization, 2),
            'jobs_total': total_jobs,
            'jobs_running': running_jobs,
            'jobs_suspended': suspended_jobs,
        }

    @staticmethod
    def parse_active_sessions(output: str) -> int:
        """
        Parse active session count from jhstat command.

        Args:
            output: Raw output from jhstat | awk | uniq | wc -l

        Returns:
            Number of active sessions as integer
        """
        try:
            return int(output.strip())
        except ValueError:
            logging.getLogger(__name__).warning(
                f"Failed to parse active sessions count: {output}"
            )
            return 0
