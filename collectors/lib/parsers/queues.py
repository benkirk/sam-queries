"""
PBS queue parser.
"""

import logging
from typing import List, Dict


class QueueParser:
    """Parse qstat queue data."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

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
            owner = job_data.get('Job_Owner', '')

            # Extract username
            if '@' in owner:
                user = owner.split('@')[0]
            else:
                user = owner

            # Get resource requests
            resources = job_data.get('Resource_List', {})
            ncpus = int(resources.get('ncpus', 0))
            ngpus = int(resources.get('ngpus', 0))
            nodect = int(resources.get('nodect', 0))

            if queue not in queues:
                queues[queue] = {
                    'queue_name': queue,
                    'running_jobs': 0,
                    'pending_jobs': 0,
                    'active_users': set(),
                    'cores_allocated': 0,
                    'gpus_allocated': 0,
                    'nodes_allocated': 0,
                }

            if state == 'R':  # Running
                queues[queue]['running_jobs'] += 1
                queues[queue]['cores_allocated'] += ncpus
                queues[queue]['gpus_allocated'] += ngpus
                queues[queue]['nodes_allocated'] += nodect
            elif state == 'Q':  # Queued
                queues[queue]['pending_jobs'] += 1

            if user:
                queues[queue]['active_users'].add(user)

        # Convert sets to counts
        result = []
        for q_data in queues.values():
            q_data['active_users'] = len(q_data['active_users'])
            result.append(q_data)

        return result
