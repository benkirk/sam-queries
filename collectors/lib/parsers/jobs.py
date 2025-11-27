"""
PBS jobs parser.
"""

import logging
from typing import Dict


class JobParser:
    """Parse qstat job data."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def parse_jobs(qstat_json: dict) -> dict:
        """
        Parse qstat JSON into job statistics.

        Args:
            qstat_json: Output from qstat -f -F json

        Returns:
            Dict with job statistics
        """
        jobs = qstat_json.get('Jobs', {})

        running = 0
        pending = 0
        held = 0
        users = set()

        for job_id, job_data in jobs.items():
            state = job_data.get('job_state', '')
            owner = job_data.get('Job_Owner', '')

            # Extract username from owner (format: username@hostname)
            if '@' in owner:
                user = owner.split('@')[0]
            else:
                user = owner

            if user:
                users.add(user)

            if state == 'R':  # Running
                running += 1
            elif state == 'Q':  # Queued
                pending += 1
            elif state == 'H':  # Held
                held += 1
            # Other states (H=held, E=exiting, etc.) not counted

        return {
            'running_jobs': running,
            'pending_jobs': pending,
            'held_jobs': held,
            'active_users': len(users),
        }
