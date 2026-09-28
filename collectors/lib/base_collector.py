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
    from .commands import SpoolSource, manifest
    from .exceptions import ConfigError
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
    from commands import SpoolSource, manifest
    from exceptions import ConfigError


class BaseCollector:
    """Base class for system-specific data collectors."""

    def __init__(self, system_name, dry_run=False, json_only=False, source=None):
        self.system_name = system_name
        self.config = CollectorConfig(system_name)
        self.dry_run = dry_run
        self.json_only = json_only
        self.source = source  # SpoolSource, or None to run commands over ssh
        self.logger = logging.getLogger(__name__)

        # Initialize clients
        self.pbs = PBSClient(self.config.pbs_host, timeout=self.config.pbs_timeout, source=source)
        self.api = SAMAPIClient(
            self.config.api_url,
            self.config.api_user,
            self.config.api_password,
            timeout=self.config.api_timeout
        )
        self.login_collector = LoginNodeCollector(self.config.pbs_host, timeout=self.config.ssh_timeout,
                                                  source=source)

    def manifest(self):
        """Commands this collector reads in spool mode, for bin/run-manifest.sh."""
        return manifest(pbs=True, filesystems=self.config.filesystems,
                        login_nodes=self.config.login_nodes)

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
            self._check_jobs_visible(data)  # here, not in collect(): a failed qstat also reads as 0

            data['queues'] = QueueParser.parse_queues(jobs_json)
            data['user_project_queues'] = QueueParser.parse_user_project_queues(jobs_json)

            self.logger.info(
                f"  Jobs: {job_stats.get('running_jobs', 0)} running, {job_stats.get('pending_jobs', 0)} pending, {job_stats.get('held_jobs', 0)} held"
            )
            self.logger.info(
                f"  User/project rollups: {len(data['user_project_queues'])} rows"
            )

            # Full qstat -Q roster (incl. routing/idle queues that never hold
            # jobs) — its own try/except so a qstat -Q hiccup doesn't cost us
            # the job-derived metrics above.
            try:
                queues_json = self.pbs.get_queues_json()
                data['queue_definitions'] = QueueParser.parse_queue_definitions(queues_json)
                self.logger.info(
                    f"  Queue roster: {len(data['queue_definitions'])} defined queues"
                )
            except Exception as e:
                self.logger.error(f"Failed to collect queue roster: {e}")
                data['queue_definitions'] = []
        except Exception as e:
            self.logger.error(f"Failed to collect job data: {e}")
            data.update({
                'running_jobs': 0,
                'pending_jobs': 0,
                'held_jobs': 0,
                'active_users': 0,
                'queues': [],
                'user_project_queues': [],
                'queue_definitions': [],
            })

    def _check_jobs_visible(self, data: dict):
        # The bare /opt/pbs/bin/qstat lists only the caller's own jobs; the site qstat on
        # the login PATH lists all. The wrong one exits 0 with an empty list, silently.
        if data.get('running_jobs') == 0 and (data.get('cpu_cores_allocated') or 0) > 0:
            self.logger.error(
                f"qstat shows no running jobs but {data['cpu_cores_allocated']} cores are "
                f"allocated: qstat is probably not the site wrapper (check PATH)")

    def _collect_login_node_data(self, data: dict):
        """Collect common login node data."""
        try:
            self.logger.info("Collecting login node data...")
            login_nodes = self.login_collector.collect_login_node_data(
                self.config.login_nodes
            )
            # Per-node process owners become one system-wide set for the last-seen ledger.
            data['login_users'] = sorted(set().union(*(n.pop('users', ()) for n in login_nodes)))
            data['login_nodes'] = login_nodes
            available = sum(1 for n in login_nodes if n.get('available'))
            self.logger.info(f"  Login nodes: {available}/{len(login_nodes)} available, "
                             f"{len(data['login_users'])} users with processes")
        except Exception as e:
            self.logger.error(f"Failed to collect login node data: {e}")
            data['login_nodes'] = []
            data['login_users'] = []

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


class _ErrorCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1


def main_runner(collector_class, system_name, description):
    """Generic main function for running a collector."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--dry-run', action='store_true', help='Collect data but do not post to API')
    parser.add_argument('--json-only', action='store_true', help='Output JSON to stdout and exit (no API call)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--print-manifest', action='store_true',
                        help='Print the key<TAB>command lines bin/run-manifest.sh runs on the host, and exit')
    parser.add_argument('--spool', metavar='DIR',
                        help='Read command output captured by bin/run-manifest.sh instead of running ssh')
    parser.add_argument('--max-spool-age', type=int, default=600, metavar='SECONDS',
                        help='Refuse a spool older than this (default 600)')
    parser.add_argument('--strict', action='store_true',
                        help='Exit 3 if any part of the collection logged an ERROR')

    args = parser.parse_args()

    if args.print_manifest:
        collector = collector_class(system_name, json_only=True)
        for key, cmd in collector.manifest():
            print(f'{key}\t{cmd}')
        return 0

    if not args.json_only:
        setup_logging(log_file=args.log_file, verbose=args.verbose)
    errors = _ErrorCounter()
    if args.strict:
        # A handler on root also retires logging.lastResort, which is what prints
        # warnings under --json-only; keep that path on stderr.
        if args.json_only:
            stderr = logging.StreamHandler(sys.stderr)
            stderr.setFormatter(logging.Formatter('%(levelname)s [%(name)s] %(message)s'))
            logging.getLogger().addHandler(stderr)
        logging.getLogger().addHandler(errors)

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info(f"{description} - Starting")
    logger.info("=" * 60)

    try:
        source = SpoolSource(args.spool, max_age=args.max_spool_age) if args.spool else None
        collector = collector_class(system_name, dry_run=args.dry_run, json_only=args.json_only,
                                    source=source)
        if not (args.dry_run or args.json_only or collector.config.api_password):
            raise ConfigError("STATUS_API_KEY required in .env or environment")
        if source is not None:
            logger.info(f"Reading spool {args.spool} (scraped on {source.host})")
        exit_code = collector.run()
        if exit_code == 0 and args.strict and errors.count:
            logger.error(f"--strict: {errors.count} error(s) logged during collection")
            exit_code = 3

        logger.info("=" * 60)
        logger.info(f"{description} - {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 2
