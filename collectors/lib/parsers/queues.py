"""
PBS queue parser.
"""

import logging
from typing import List, Dict


class QueueParser:
    """Parse qstat queue data."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    UNKNOWN_PROJECT = '_unknown_'
    """Sentinel project_code used for jobs with no Account_Name set."""

    @staticmethod
    def _extract_username(job_data: dict) -> str:
        """Pull the username out of ``Job_Owner`` (format: ``user@host``)."""
        owner = job_data.get('Job_Owner', '')
        if '@' in owner:
            return owner.split('@')[0]
        return owner

    @staticmethod
    def _extract_project_code(job_data: dict) -> str:
        """Pull ``Account_Name`` (PBS project code), bucketing missing values."""
        account = job_data.get('Account_Name')
        if account is None:
            return QueueParser.UNKNOWN_PROJECT
        account = str(account).strip()
        return account or QueueParser.UNKNOWN_PROJECT

    @staticmethod
    def parse_queues(qstat_output: str, qstat_json: dict) -> List[dict]:
        """
        Parse queue summary and detailed job data.

        Args:
            qstat_output: Text output from qstat -Qa (not currently used)
            qstat_json: JSON from qstat -f -F json (for per-queue breakdown)

        Returns:
            List of queue status dicts
        """
        queues = {}

        # Parse jobs by queue
        for job_id, job_data in qstat_json.get('Jobs', {}).items():
            queue = job_data.get('queue', 'unknown')
            state = job_data.get('job_state', '')
            user = QueueParser._extract_username(job_data)

            # Get resource requests
            resources = job_data.get('Resource_List', {})
            ncpus = int(resources.get('ncpus', 0))
            ngpus = int(resources.get('ngpus', 0))

            if queue not in queues:
                queues[queue] = {
                    'queue_name': queue,
                    'running_jobs': 0,
                    'pending_jobs': 0,
                    'held_jobs' : 0,
                    'active_users': set(),
                    'cores_allocated': 0,
                    'gpus_allocated': 0,
                    'nodes_allocated': set(),  # Track unique nodes
                    'cores_pending' : 0,
                    'gpus_pending' : 0,
                    'cores_held' : 0,
                    'gpus_held' : 0,
                }

            if state == 'R':  # Running
                queues[queue]['running_jobs'] += 1
                queues[queue]['cores_allocated'] += ncpus
                queues[queue]['gpus_allocated'] += ngpus

                # Count unique nodes from exec_host
                # Format: "node1/cpu+node2/cpu" or "node1/cpu*count"
                exec_host = job_data.get('exec_host', '')
                if exec_host:
                    # Split by '+' for multi-node jobs
                    for node_spec in exec_host.split('+'):
                        # Extract node name (before '/')
                        node_name = node_spec.split('/')[0]
                        if node_name:
                            queues[queue]['nodes_allocated'].add(node_name)
            elif state == 'Q':  # Queued
                queues[queue]['pending_jobs'] += 1
                queues[queue]['cores_pending'] += ncpus
                queues[queue]['gpus_pending'] += ngpus

            elif state == 'H':  # Held
                queues[queue]['held_jobs'] += 1
                queues[queue]['cores_held'] += ncpus
                queues[queue]['gpus_held'] += ngpus

            if user:
                queues[queue]['active_users'].add(user)

        # Convert sets to counts
        result = []
        for q_data in queues.values():
            q_data['active_users'] = len(q_data['active_users'])
            q_data['nodes_allocated'] = len(q_data['nodes_allocated'])  # Count unique nodes
            result.append(q_data)

        return result

    @staticmethod
    def parse_user_project_queues(qstat_json: dict) -> List[dict]:
        """
        Aggregate job rollups keyed by ``(username, project_code, queue_name)``.

        Same counter shape as ``parse_queues`` minus ``active_users`` (one row =
        one user, by definition). Jobs with no ``Account_Name`` are bucketed
        under the ``_unknown_`` sentinel (see ``QueueParser.UNKNOWN_PROJECT``)
        so totals stay reconcilable with the queue-grain rollup.

        Args:
            qstat_json: JSON from qstat -f -F json (per-job detail)

        Returns:
            List of dicts, one per unique (user, project, queue) tuple.
        """
        groups = {}

        for job_id, job_data in qstat_json.get('Jobs', {}).items():
            queue = job_data.get('queue', 'unknown')
            state = job_data.get('job_state', '')
            user = QueueParser._extract_username(job_data)
            project_code = QueueParser._extract_project_code(job_data)

            if not user:
                # Job_Owner missing entirely — skip, since we can't
                # attribute the rollup to anyone.
                continue

            resources = job_data.get('Resource_List', {})
            ncpus = int(resources.get('ncpus', 0))
            ngpus = int(resources.get('ngpus', 0))

            key = (user, project_code, queue)
            if key not in groups:
                groups[key] = {
                    'username': user,
                    'project_code': project_code,
                    'queue_name': queue,
                    'running_jobs': 0,
                    'pending_jobs': 0,
                    'held_jobs': 0,
                    'cores_allocated': 0,
                    'gpus_allocated': 0,
                    'nodes_allocated': set(),  # unique nodes for this user/project/queue
                    'cores_pending': 0,
                    'gpus_pending': 0,
                    'cores_held': 0,
                    'gpus_held': 0,
                }

            row = groups[key]

            if state == 'R':
                row['running_jobs'] += 1
                row['cores_allocated'] += ncpus
                row['gpus_allocated'] += ngpus

                exec_host = job_data.get('exec_host', '')
                if exec_host:
                    for node_spec in exec_host.split('+'):
                        node_name = node_spec.split('/')[0]
                        if node_name:
                            row['nodes_allocated'].add(node_name)
            elif state == 'Q':
                row['pending_jobs'] += 1
                row['cores_pending'] += ncpus
                row['gpus_pending'] += ngpus
            elif state == 'H':
                row['held_jobs'] += 1
                row['cores_held'] += ncpus
                row['gpus_held'] += ngpus

        result = []
        for row in groups.values():
            row['nodes_allocated'] = len(row['nodes_allocated'])
            result.append(row)

        return result
