"""
Base class for HPC system status collectors.
"""
import sys
import json
import argparse
import logging
from datetime import datetime

# This try/except block allows the file to be imported when 'collectors' is a package,
# or run directly, where 'lib' is in the python path.
try:
    from .pbs_client import PBSClient
    from .api_client import SAMAPIClient
    from .config import CollectorConfig
    from .logging_utils import setup_logging
    from .parsers.nodes import NodeParser
    from .parsers.jobs import JobParser
    from .parsers.queues import QueueParser
    from .parsers.filesystems import FilesystemParser
    from .parsers.reservations import ReservationParser
    from .ssh_utils import LoginNodeCollector
except ImportError:
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


class BaseCollector:
    """Base class for system-specific data collectors."""

    def __init__(self, system_name, dry_run=False, json_only=False):
        self.system_name = system_name
        self.config = CollectorConfig(system_name)
        self.dry_run = dry_run
        self.json_only = json_only
        self.logger = logging.getLogger(__name__)

        # Initialize clients
        self.pbs = PBSClient(self.config.pbs_host, timeout=self.config.pbs_timeout)
        self.api = SAMAPIClient(
            self.config.api_url,
            self.config.api_user,
            self.config.api_password,
            timeout=self.config.api_timeout
        )
        self.login_collector = LoginNodeCollector(self.config.pbs_host, timeout=self.config.ssh_timeout)

    def _collect_node_data(self, data: dict):
        """
        Collect system-specific node data.
        This method must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement _collect_node_data")

    def _collect_job_data(self, data: dict):
        """Collect common job data."""
        try:
            self.logger.info("Collecting job data...")
            jobs_json = self.pbs.get_jobs_json()
            job_stats = JobParser.parse_jobs(jobs_json)
            data.update(job_stats)

            queue_summary = self.pbs.get_queue_summary()
            data['queues'] = QueueParser.parse_queues(queue_summary, jobs_json)

            self.logger.info(
                f"  Jobs: {job_stats.get('running_jobs', 0)} running, {job_stats.get('pending_jobs', 0)} pending, {job_stats.get('held_jobs', 0)} held"
            )
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'held_jobs': 0,
                'active_users': 0,
                'queues': []
            })

    def _collect_login_node_data(self, data: dict):
        """Collect common login node data."""
        try:
            self.logger.info("Collecting login node data...")
            login_nodes = self.login_collector.collect_login_node_data(
                self.config.login_nodes
            )
            data['login_nodes'] = login_nodes
            available = sum(1 for n in login_nodes if n.get('available'))
            self.logger.info(f"  Login nodes: {available}/{len(login_nodes)} available")
        except Exception as e:
            self.logger.error(f"Failed to collect login node data: {e}")
            data['login_nodes'] = []

    def _collect_filesystem_data(self, data: dict):
        """Collect common filesystem data."""
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

    def _collect_reservation_data(self, data: dict):
        """Collect common reservation data."""
        try:
            self.logger.info("Collecting reservation data...")
            rstat_output = self.pbs.get_reservations()
            data['reservations'] = ReservationParser.parse_reservations(
                rstat_output,
                self.system_name
            )
            self.logger.info(f"  Reservations: {len(data['reservations'])} active")
        except Exception as e:
            self.logger.error(f"Failed to collect reservation data: {e}")
            data['reservations'] = []

    def collect(self):
        """
        Collect all system metrics.
        Returns a complete data dict ready for API posting.
        """
        data = {'timestamp': datetime.now().isoformat()}
        self._collect_node_data(data)
        self._collect_job_data(data)
        self._collect_login_node_data(data)
        self._collect_filesystem_data(data)
        self._collect_reservation_data(data)
        return data

    def run(self):
        """Execute collection and posting."""
        try:
            data = self.collect()

            if self.json_only:
                print(json.dumps(data, indent=2))
                return 0

            result = self.api.post_status(self.system_name, data, dry_run=self.dry_run)

            if not self.dry_run:
                self.logger.info(f"✓ Success: status_id={result.get('status_id')}")

            return 0
        except Exception as e:
            self.logger.error(f"✗ Collection failed: {e}", exc_info=True)
            return 1


def main_runner(collector_class, system_name, description):
    """Generic main function for running a collector."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--dry-run', action='store_true', help='Collect data but do not post to API')
    parser.add_argument('--json-only', action='store_true', help='Output JSON to stdout and exit (no API call)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--log-file', help='Log file path')

    args = parser.parse_args()

    if not args.json_only:
        setup_logging(log_file=args.log_file, verbose=args.verbose)

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info(f"{description} - Starting")
    logger.info("=" * 60)

    try:
        collector = collector_class(system_name, dry_run=args.dry_run, json_only=args.json_only)
        exit_code = collector.run()

        logger.info("=" * 60)
        logger.info(f"{description} - {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 2
