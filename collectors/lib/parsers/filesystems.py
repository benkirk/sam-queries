"""
Filesystem usage parser.

Simplified design using BLOCKSIZE=TiB for direct TiB output without unit conversion.
Uses mount paths directly as filesystem names for clarity.
"""

import logging
from typing import List


class FilesystemParser:
    """
    Parse df output for filesystem status.

    Uses BLOCKSIZE=TiB environment variable to get sizes directly in TiB,
    eliminating complex unit conversion logic.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def collect_and_parse(ssh_runner, mount_paths: List[str]) -> List[dict]:
        """
        Collect filesystem metrics via SSH and parse results.

        Runs a single SSH command with multiple df calls, separated by '---'
        delimiter. Each filesystem is queried with BLOCKSIZE=TiB for direct
        TiB output.

        Args:
            ssh_runner: Object with run_command(cmd) method (e.g., PBSClient)
            mount_paths: List of mount paths to check (e.g., ['/glade/u/home', ...])

        Returns:
            List of filesystem status dicts with keys:
                - filesystem_name: Mount path (str)
                - available: Whether filesystem is accessible (bool)
                - degraded: Whether filesystem is >90% full (bool)
                - capacity_tb: Total capacity in TiB (float)
                - used_tb: Used space in TiB (float)
                - utilization_percent: Usage percentage (float)

        Example:
            >>> from pbs_client import PBSClient
            >>> pbs = PBSClient('derecho')
            >>> paths = ['/glade/work', '/glade/campaign']
            >>> FilesystemParser.collect_and_parse(pbs, paths)
            [
                {
                    'filesystem_name': '/glade/work',
                    'available': True,
                    'degraded': False,
                    'capacity_tb': 500.5,
                    'used_tb': 250.2,
                    'utilization_percent': 50.0
                },
                ...
            ]
        """
        logger = logging.getLogger(__name__)

        # Build command: BLOCKSIZE=TiB df path1; echo '---'; BLOCKSIZE=TiB df path2; ...
        df_commands = []
        for path in mount_paths:
            df_commands.append(f'BLOCKSIZE=TiB df {path}')

        # Join with delimiter
        full_command = '; echo "---"; '.join(df_commands)

        try:
            output = ssh_runner.run_command(full_command)
        except Exception as e:
            logger.error(f"Failed to run df commands: {e}")
            # Return all filesystems as unavailable
            return [
                {
                    'filesystem_name': path,
                    'available': False,
                    'degraded': True,
                    'capacity_tb': None,
                    'used_tb': None,
                    'utilization_percent': None,
                }
                for path in mount_paths
            ]

        # Parse each df block
        filesystems = []
        blocks = output.split('---')

        for i, block in enumerate(blocks):
            if i >= len(mount_paths):
                break

            mount_path = mount_paths[i]

            try:
                fs_data = FilesystemParser._parse_df_block(block.strip(), mount_path)
                filesystems.append(fs_data)
                logger.debug(
                    f"Parsed {mount_path}: {fs_data['used_tb']:.1f}TiB / "
                    f"{fs_data['capacity_tb']:.1f}TiB ({fs_data['utilization_percent']}%)"
                )
            except Exception as e:
                logger.warning(f"Failed to parse {mount_path}: {e}")
                # Add degraded entry
                filesystems.append({
                    'filesystem_name': mount_path,
                    'available': False,
                    'degraded': True,
                    'capacity_tb': None,
                    'used_tb': None,
                    'utilization_percent': None,
                })

        return filesystems

    @staticmethod
    def _parse_df_block(df_output: str, mount_path: str) -> dict:
        """
        Parse single df output block (already in TiB).

        df output with BLOCKSIZE=TiB produces:
            Filesystem      1T-blocks  Used Available Use% Mounted on
            /dev/mapper/vg  500.5      250.2 250.3    50%  /glade/work

        All sizes are already in TiB as floats - no conversion needed!

        Args:
            df_output: Single df command output (with header)
            mount_path: Mount path being queried

        Returns:
            Filesystem status dict

        Raises:
            ValueError: If output format is invalid
        """
        lines = df_output.strip().split('\n')

        if len(lines) < 2:
            raise ValueError(f"Invalid df output: expected at least 2 lines, got {len(lines)}")

        # Parse data line (skip header line)
        # Format: Filesystem 1T-blocks Used Available Use% Mounted
        parts = lines[1].split()

        if len(parts) < 6:
            raise ValueError(f"Incomplete df output: expected 6+ fields, got {len(parts)}")

        try:
            # Columns: [0]=Filesystem [1]=1T-blocks [2]=Used [3]=Available [4]=Use% [5]=Mounted
            # Note: BLOCKSIZE=TiB may still add 'TiB' suffix on some systems, so strip it
            size_tib = float(parts[1].replace('TiB', '').replace('TB', ''))
            used_tib = float(parts[2].replace('TiB', '').replace('TB', ''))
            use_pct_str = parts[4].replace('%', '')
            use_pct = int(use_pct_str)
        except (ValueError, IndexError) as e:
            raise ValueError(f"Failed to parse numeric fields: {e}")

        return {
            'filesystem_name': mount_path,  # Use path directly as name
            'available': True,
            'degraded': use_pct > 90,  # Mark degraded if >90% full
            'capacity_tb': round(size_tib, 2),
            'used_tb': round(used_tib, 2),
            'utilization_percent': float(use_pct),
        }
