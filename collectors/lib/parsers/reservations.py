"""
PBS reservation parser.

Parses output from 'pbs_rstat -f' to extract reservation information.
"""

import logging
from typing import List, Optional
from datetime import datetime


class ReservationParser:
    """Parse PBS pbs_rstat -f output."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def parse_reservations(rstat_output: str, system_name: str) -> List[dict]:
        """
        Parse PBS pbs_rstat -f output into reservation records.

        PBS rstat output format:
            Resv ID: R416808.casper-pbs
            Reserve_Name = casper-a100-testing
            Reserve_Owner = root@casper-pbs.hpc.ucar.edu
            reserve_state = RESV_RUNNING
            reserve_start = Wed Nov 12 12:00:00 2025
            reserve_end = Wed Dec 31 23:59:00 2025
            Resource_List.nodect = 2
            Resource_List.select = 1:host=casper41:ncpus=128:ngpus=4
            resv_nodes = (casper41:ncpus=128)+(casper44:ncpus=128)
            Authorized_Users = +user1,+user2
            partition = pbs-default

            Resv ID: R416809.casper-pbs
            Reserve_Name = maintenance-window
            ...

        Args:
            rstat_output: Output from 'pbs_rstat -f' command
            system_name: System name (e.g., 'derecho', 'casper')

        Returns:
            List of reservation dicts with keys:
                - system_name: System name (str)
                - reservation_name: Reservation name (str)
                - description: Description (uses Reserve_Name or Resv ID)
                - start_time: Start datetime in ISO format (str)
                - end_time: End datetime in ISO format (str)
                - node_count: Number of nodes reserved (int or None)
                - partition: PBS partition name (str or None)

        Example:
            >>> output = ReservationParser.parse_reservations(rstat_output, 'derecho')
            [
                {
                    'system_name': 'derecho',
                    'reservation_name': 'MONTHLY_MAINTENANCE',
                    'description': 'MONTHLY_MAINTENANCE',
                    'start_time': '2025-02-01T06:00:00',
                    'end_time': '2025-02-01T18:00:00',
                    'node_count': 2488,
                    'partition': 'pbs-default'
                },
                ...
            ]
        """
        logger = logging.getLogger(__name__)

        # Handle empty output
        if not rstat_output or not rstat_output.strip():
            logger.debug(f"No reservation data for {system_name}")
            return []

        reservations = []

        # Split into reservation blocks (separated by blank lines)
        blocks = rstat_output.strip().split('\n\n')

        for block in blocks:
            if not block.strip():
                continue

            try:
                resv_data = ReservationParser._parse_reservation_block(block, system_name)
                if resv_data:
                    reservations.append(resv_data)
            except Exception as e:
                logger.warning(f"Failed to parse reservation block: {e}")
                logger.debug(f"Block content:\n{block}")
                continue

        logger.debug(f"Parsed {len(reservations)} reservations for {system_name}")
        return reservations

    @staticmethod
    def _parse_reservation_block(block: str, system_name: str) -> Optional[dict]:
        """
        Parse a single reservation block.

        Args:
            block: Single reservation text block
            system_name: System name

        Returns:
            Reservation dict or None if required fields missing

        Raises:
            ValueError: If parsing fails
        """
        logger = logging.getLogger(__name__)

        # Parse key-value pairs
        data = {}
        for line in block.split('\n'):
            line = line.strip()
            if not line:
                continue

            # Handle "key = value" format
            if ' = ' in line:
                key, value = line.split(' = ', 1)
                data[key.strip()] = value.strip()
            # Handle "Resv ID: value" format
            elif line.startswith('Resv ID:'):
                data['Resv ID'] = line.split(':', 1)[1].strip()

        # Extract required fields
        reservation_name = data.get('Reserve_Name')
        if not reservation_name:
            logger.warning("Skipping reservation: missing Reserve_Name")
            return None
        elif reservation_name == "NULL":
            reservation_name = data.get('queue')

        # Parse start and end times
        start_time_str = data.get('reserve_start')
        end_time_str = data.get('reserve_end')

        if not start_time_str or not end_time_str:
            logger.warning(f"Skipping reservation {reservation_name}: missing start/end time")
            return None

        try:
            start_time = ReservationParser._parse_pbs_datetime(start_time_str)
            end_time = ReservationParser._parse_pbs_datetime(end_time_str)
        except ValueError as e:
            logger.warning(f"Skipping reservation {reservation_name}: datetime parse error: {e}")
            return None

        # Extract node count (priority: Resource_List.nodect > parse resv_nodes > None)
        node_count = ReservationParser._extract_node_count(data)

        # Extract description (use Reserve_Name or Resv ID)
        description = data.get('Resv ID')

        # Extract partition (may be missing)
        partition = data.get('partition')

        return {
            'system_name': system_name,
            'reservation_name': reservation_name,
            'description': description,
            'start_time': start_time,
            'end_time': end_time,
            'node_count': node_count,
            'partition': partition,
        }

    @staticmethod
    def _parse_pbs_datetime(time_str: str) -> str:
        """
        Parse PBS datetime string to ISO format.

        PBS format: "Wed Nov 12 12:00:00 2025"
        Output: "2025-11-12T12:00:00"

        Args:
            time_str: PBS datetime string

        Returns:
            ISO format datetime string

        Raises:
            ValueError: If datetime format is invalid
        """
        # PBS datetime format: "Wed Nov 12 12:00:00 2025"
        dt = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
        return dt.isoformat()

    @staticmethod
    def _extract_node_count(data: dict) -> Optional[int]:
        """
        Extract node count from reservation data.

        Priority:
        1. Resource_List.nodect (if present)
        2. Parse resv_nodes field (count '+' separated entries)
        3. None (if neither available)

        Args:
            data: Parsed reservation key-value pairs

        Returns:
            Node count or None
        """
        logger = logging.getLogger(__name__)

        # Priority 1: Resource_List.nodect
        nodect_str = data.get('Resource_List.nodect')
        if nodect_str:
            try:
                return int(nodect_str)
            except ValueError:
                logger.warning(f"Invalid Resource_List.nodect value: {nodect_str}")

        # Priority 2: Parse resv_nodes
        # Format: "(node1:ncpus=128)+(node2:ncpus=128)+(node3:ncpus=128)"
        resv_nodes = data.get('resv_nodes')
        if resv_nodes:
            try:
                # Split by '+', filter empty strings
                nodes = [n.strip() for n in resv_nodes.split('+') if n.strip()]
                if nodes:
                    return len(nodes)
            except Exception as e:
                logger.warning(f"Failed to parse resv_nodes: {e}")

        # Fallback: None
        return None
