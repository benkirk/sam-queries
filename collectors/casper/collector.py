#!/usr/bin/env python3
"""
Casper DAV Status Collector

Collects system metrics from Casper and posts to SAM Status Dashboard.
"""

import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))

from pbs_client import PBSClient
from api_client import SAMAPIClient
from config import CollectorConfig
from logging_utils import setup_logging
from parsers.nodes import NodeParser
from parsers.jobs import JobParser
from parsers.queues import QueueParser
from parsers.filesystems import FilesystemParser
from ssh_utils import LoginNodeCollector


class CasperCollector:
    """Main Casper data collector."""

    def __init__(self, config, dry_run=False, json_only=False):
        self.config = config
        self.dry_run = dry_run
        self.json_only = json_only
        self.logger = logging.getLogger(__name__)

        # Initialize clients
        self.pbs = PBSClient(config.pbs_host, timeout=config.pbs_timeout)
        self.api = SAMAPIClient(
            config.api_url,
            config.api_user,
            config.api_password,
            timeout=config.api_timeout
        )
        self.login_collector = LoginNodeCollector(config.pbs_host, timeout=config.ssh_timeout)

    def collect(self):
        """
        Collect all Casper metrics.

        Returns:
            Complete data dict ready for API posting
        """
        data = {
            'timestamp': datetime.now().isoformat()
        }

        # Collect node data
        try:
            self.logger.info("Collecting node data...")
            nodes_json = self.pbs.get_nodes_json()

            # Aggregate stats for all node types (CPU, GPU, VIZ)
            node_stats = NodeParser.parse_nodes(nodes_json, 'casper')
            data.update({
                # CPU nodes
                'cpu_nodes_total': node_stats['cpu_nodes_total'],
                'cpu_nodes_available': node_stats['cpu_nodes_available'],
                'cpu_nodes_down': node_stats['cpu_nodes_down'],
                'cpu_nodes_reserved': node_stats['cpu_nodes_reserved'],
                'cpu_cores_total': node_stats['cpu_cores_total'],
                'cpu_cores_allocated': node_stats['cpu_cores_allocated'],
                'cpu_cores_idle': node_stats['cpu_cores_idle'],
                'cpu_utilization_percent': node_stats.get('cpu_utilization_percent'),
                # GPU nodes
                'gpu_nodes_total': node_stats['gpu_nodes_total'],
                'gpu_nodes_available': node_stats['gpu_nodes_available'],
                'gpu_nodes_down': node_stats['gpu_nodes_down'],
                'gpu_nodes_reserved': node_stats['gpu_nodes_reserved'],
                'gpu_count_total': node_stats['gpu_count_total'],
                'gpu_count_allocated': node_stats['gpu_count_allocated'],
                'gpu_count_idle': node_stats['gpu_count_idle'],
                'gpu_utilization_percent': node_stats.get('gpu_utilization_percent'),
                # VIZ nodes
                'viz_nodes_total': node_stats['viz_nodes_total'],
                'viz_nodes_available': node_stats['viz_nodes_available'],
                'viz_nodes_down': node_stats['viz_nodes_down'],
                'viz_nodes_reserved': node_stats['viz_nodes_reserved'],
                'viz_count_total': node_stats['viz_count_total'],
                'viz_count_allocated': node_stats['viz_count_allocated'],
                'viz_count_idle': node_stats['viz_count_idle'],
                'viz_utilization_percent': node_stats.get('viz_utilization_percent'),
                # Memory
                'memory_total_gb': node_stats['memory_total_gb'],
                'memory_allocated_gb': node_stats['memory_allocated_gb'],
                'memory_utilization_percent': node_stats.get('memory_utilization_percent'),
            })

            # Node type breakdown
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
            self.logger.error(f"Failed to collect node data: {e}")
            data.update({
                'cpu_nodes_total': 0,
                'cpu_nodes_available': 0,
                'cpu_nodes_down': 0,
                'cpu_nodes_reserved': 0,
                'cpu_cores_total': 0,
                'cpu_cores_allocated': 0,
                'cpu_cores_idle': 0,
                'cpu_utilization_percent': None,
                'gpu_nodes_total': 0,
                'gpu_nodes_available': 0,
                'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0,
                'gpu_count_total': 0,
                'gpu_count_allocated': 0,
                'gpu_count_idle': 0,
                'gpu_utilization_percent': None,
                'viz_nodes_total': 0,
                'viz_nodes_available': 0,
                'viz_nodes_down': 0,
                'viz_nodes_reserved': 0,
                'viz_count_total': 0,
                'viz_count_allocated': 0,
                'viz_count_idle': 0,
                'viz_utilization_percent': None,
                'memory_total_gb': 0.0,
                'memory_allocated_gb': 0.0,
                'memory_utilization_percent': None,
                'node_types': []
            })

        # Collect job data
        try:
            self.logger.info("Collecting job data...")
            jobs_json = self.pbs.get_jobs_json()
            job_stats = JobParser.parse_jobs(jobs_json)
            data.update(job_stats)

            queue_summary = self.pbs.get_queue_summary()
            data['queues'] = QueueParser.parse_queues(queue_summary, jobs_json)

            self.logger.info(
                f"  Jobs: {job_stats['running_jobs']} running, "
                f"{job_stats['pending_jobs']} pending"
            )
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'active_users': 0,
                'queues': []
            })

        # Collect login node data (NO node_type field for Casper)
        try:
            self.logger.info("Collecting login node data...")
            login_nodes = self.login_collector.collect_login_node_data(
                self.config.login_nodes
            )
            data['login_nodes'] = login_nodes
            available = sum(1 for n in login_nodes if n['available'])
            self.logger.info(f"  Login nodes: {available}/{len(login_nodes)} available")
        except Exception as e:
            self.logger.error(f"Failed to collect login node data: {e}")
            data['login_nodes'] = []

        # Collect filesystem data
        try:
            self.logger.info("Collecting filesystem data...")
            df_cmd = "df -h /glade/u/home /glade/work /glade/campaign"
            df_output = self.pbs.run_command(df_cmd)
            data['filesystems'] = FilesystemParser.parse_filesystems(
                df_output, self.config.filesystems
            )
            self.logger.info(f"  Filesystems: {len(data['filesystems'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect filesystem data: {e}")
            data['filesystems'] = []

        return data

    def run(self):
        """Execute collection and posting."""
        try:
            data = self.collect()

            if self.json_only:
                print(json.dumps(data, indent=2))
                return 0

            result = self.api.post_status('casper', data, dry_run=self.dry_run)

            if not self.dry_run:
                self.logger.info(f"✓ Success: status_id={result.get('status_id')}")

            return 0

        except Exception as e:
            self.logger.error(f"✗ Collection failed: {e}", exc_info=True)
            return 1


def main():
    parser = argparse.ArgumentParser(description='Casper DAV Status Collector')
    parser.add_argument('--dry-run', action='store_true',
                       help='Collect data but do not post to API')
    parser.add_argument('--json-only', action='store_true',
                       help='Output JSON to stdout and exit (no API call)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    parser.add_argument('--log-file',
                       help='Log file path (default: stdout only for development)')

    args = parser.parse_args()

    # Setup logging (disable for json-only mode)
    if not args.json_only:
        setup_logging(
            log_file=args.log_file,
            verbose=args.verbose
        )

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Casper Status Collector - Starting")
    logger.info("=" * 60)

    try:
        config = CollectorConfig('casper')
        collector = CasperCollector(
            config,
            dry_run=args.dry_run,
            json_only=args.json_only
        )
        exit_code = collector.run()

        logger.info("=" * 60)
        logger.info(f"Casper Status Collector - {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
