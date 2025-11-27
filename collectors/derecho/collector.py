#!/usr/bin/env python3
"""
Derecho HPC Status Collector

Collects system metrics from Derecho and posts to SAM Status Dashboard.
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
from parsers.reservations import ReservationParser
from ssh_utils import LoginNodeCollector


class DerechoCollector:
    """Main Derecho data collector."""

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
        Collect all Derecho metrics.

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
            node_stats = NodeParser.parse_nodes(nodes_json, 'derecho')
            data.update(node_stats)
            self.logger.info(
                f"  CPU nodes: {node_stats['cpu_nodes_total']} total, "
                f"{node_stats['cpu_nodes_available']} available"
            )
            self.logger.info(
                f"  GPU nodes: {node_stats['gpu_nodes_total']} total, "
                f"{node_stats['gpu_nodes_available']} available"
            )
        except Exception as e:
            self.logger.error(f"Failed to collect node data: {e}")
            # Set defaults to allow partial collection
            data.update({
                'cpu_nodes_total': 0,
                'cpu_nodes_available': 0,
                'cpu_nodes_down': 0,
                'cpu_nodes_reserved': 0,
                'gpu_nodes_total': 0,
                'gpu_nodes_available': 0,
                'gpu_nodes_down': 0,
                'gpu_nodes_reserved': 0,
                'cpu_cores_total': 0,
                'cpu_cores_allocated': 0,
                'cpu_cores_idle': 0,
                'gpu_count_total': 0,
                'gpu_count_allocated': 0,
                'gpu_count_idle': 0,
                'memory_total_gb': 0.0,
                'memory_allocated_gb': 0.0,
            })

        # Collect job data
        try:
            self.logger.info("Collecting job data...")
            jobs_json = self.pbs.get_jobs_json()
            job_stats = JobParser.parse_jobs(jobs_json)
            data.update(job_stats)
            self.logger.info(
                f"  Jobs: {job_stats['running_jobs']} running, {job_stats['pending_jobs']} pending, {job_stats['held_jobs']} held"
            )

            # Parse queues
            queue_summary = self.pbs.get_queue_summary()
            data['queues'] = QueueParser.parse_queues(queue_summary, jobs_json)
            self.logger.info(f"  Queues: {len(data['queues'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'held_jobs': 0,
                'active_users': 0,
                'queues': []
            })

        # Collect login node data
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
            data['filesystems'] = FilesystemParser.collect_and_parse(
                self.pbs,
                self.config.filesystems
            )
            self.logger.info(f"  Filesystems: {len(data['filesystems'])} tracked")
        except Exception as e:
            self.logger.error(f"Failed to collect filesystem data: {e}")
            data['filesystems'] = []

        # Collect reservation data
        try:
            self.logger.info("Collecting reservation data...")
            rstat_output = self.pbs.get_reservations()
            data['reservations'] = ReservationParser.parse_reservations(
                rstat_output,
                'derecho'
            )
            self.logger.info(f"  Reservations: {len(data['reservations'])} active")
        except Exception as e:
            self.logger.error(f"Failed to collect reservation data: {e}")
            data['reservations'] = []

        return data

    def run(self):
        """
        Execute collection and posting.

        Returns:
            Exit code (0 = success, 1 = failure)
        """
        try:
            data = self.collect()

            if self.json_only:
                print(json.dumps(data, indent=2))
                return 0

            result = self.api.post_status('derecho', data, dry_run=self.dry_run)

            if not self.dry_run:
                self.logger.info(f"✓ Success: status_id={result.get('status_id')}")

            return 0

        except Exception as e:
            self.logger.error(f"✗ Collection failed: {e}", exc_info=True)
            return 1


def main():
    parser = argparse.ArgumentParser(description='Derecho HPC Status Collector')
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
    logger.info("Derecho Status Collector - Starting")
    logger.info("=" * 60)

    try:
        config = CollectorConfig('derecho')
        collector = DerechoCollector(
            config,
            dry_run=args.dry_run,
            json_only=args.json_only
        )
        exit_code = collector.run()

        logger.info("=" * 60)
        logger.info(f"Derecho Status Collector - {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
