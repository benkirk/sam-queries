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
delimiter. Each filesystem is queried for space and inode usage.

        Args:
            ssh_runner: Object with run_command(cmd) method (e.g., PBSClient)
            mount_paths: List of mount paths to check (e.g., ['/glade/u/home', ...])

        Returns:
            List of filesystem status dicts with keys for space and inode usage.
        """
        logger = logging.getLogger(__name__)

        # Build command:
        # BLOCKSIZE=TiB df path1; echo '~~~'; df -i path1; echo '---'; ...
        df_commands = []
        for path in mount_paths:
            df_commands.append(f'BLOCKSIZE=TiB df {path}; echo "~~~"; df -i {path}')

        full_command = '; echo "---"; '.join(df_commands)

        try:
            output = ssh_runner.run_command(full_command)
        except Exception as e:
            logger.error(f"Failed to run df commands: {e}")
            return [
                {
                    'filesystem_name': path,
                    'available': False,
                    'degraded': True,
                    'capacity_tb': None,
                    'used_tb': None,
                    'utilization_percent': None,
                    'capacity_inodes': None,
                    'used_inodes': None,
                    'inodes_utilization_percent': None,
                }
                for path in mount_paths
            ]

        filesystems = []
        blocks = output.split('---')

        for i, block in enumerate(blocks):
            if i >= len(mount_paths):
                break

            mount_path = mount_paths[i]

            try:
                if '~~~' not in block:
                    logger.warning(f"Delimiter '~~~' not found in df output block for {mount_path}. Skipping inode metrics.")
                    space_block = block
                    inode_block = None
                else:
                    space_block, inode_block = block.split('~~~', 1)

                fs_data = FilesystemParser._parse_df_block(space_block.strip(), mount_path)

                if inode_block:
                    try:
                        inode_data = FilesystemParser._parse_df_inode_block(inode_block.strip())
                        fs_data.update(inode_data)
                    except Exception as e:
                        logger.warning(f"Could not parse inode data for {mount_path}: {e}")
                        fs_data.update({
                            'capacity_inodes': None,
                            'used_inodes': None,
                            'inodes_utilization_percent': None,
                        })
                else:
                    fs_data.update({
                        'capacity_inodes': None,
                        'used_inodes': None,
                        'inodes_utilization_percent': None,
                    })


                filesystems.append(fs_data)

                debug_msg = (
                    f"Parsed {mount_path}: "
                    f"{fs_data.get('used_tb', 0):.1f}TiB / {fs_data.get('capacity_tb', 0):.1f}TiB "
                    f"({fs_data.get('utilization_percent', 'N/A')}%)"
                )
                if 'capacity_inodes' in fs_data and fs_data['capacity_inodes'] is not None:
                    debug_msg += (
                        f" | Inodes: {fs_data.get('used_inodes', 0):.0f} / {fs_data.get('capacity_inodes', 0):.0f} "
                        f"({fs_data.get('inodes_utilization_percent', 'N/A')}%)"
                    )
                logger.debug(debug_msg)

            except Exception as e:
                logger.warning(f"Failed to parse {mount_path}: {e}")
                filesystems.append({
                    'filesystem_name': mount_path,
                    'available': False,
                    'degraded': True,
                    'capacity_tb': None,
                    'used_tb': None,
                    'utilization_percent': None,
                    'capacity_inodes': None,
                    'used_inodes': None,
                    'inodes_utilization_percent': None,
                })

        return filesystems

    @staticmethod
    def _parse_df_block(df_output: str, mount_path: str) -> dict:
        """
        Parse single df output block (already in TiB).
        """
        lines = df_output.strip().split('\n')
        if len(lines) < 2:
            raise ValueError(f"Invalid df output: expected at least 2 lines, got {len(lines)}")

        # Skip header, parse first data line
        parts = lines[-1].split()

        try:
            # Flexible parsing based on expected `df` output format.
            # Handles `filesystem size used avail use% mounted_on`
            # We care about size, used, and use%
            use_pct = float(parts[-2].replace('%', ''))
            used_tib = float(parts[-4].replace('TiB', '').replace('TB', ''))
            size_tib = float(parts[-5].replace('TiB', '').replace('TB', ''))
            if size_tib > 0.:
                use_pct = used_tib / size_tib * 100.
        except (ValueError, IndexError) as e:
            raise ValueError(f"Failed to parse numeric fields from df: {e} in '{' '.join(parts)}'")

        return {
            'filesystem_name': mount_path,
            'available': True,
            'degraded': use_pct > 90,
            'capacity_tb': round(size_tib, 2),
            'used_tb': round(used_tib, 2),
            'utilization_percent': round(use_pct, 2),
        }

    @staticmethod
    def _parse_df_inode_block(df_output: str) -> dict:
        """
        Parse single df -i output block.
        """
        lines = df_output.strip().split('\n')
        if len(lines) < 2:
            raise ValueError(f"Invalid df -i output: expected at least 2 lines, got {len(lines)}")

        # Skip header, parse first data line
        parts = lines[-1].split()

        try:
            # Flexible parsing for `df -i`
            # Handles `filesystem inodes iused ifree iuse% mounted_on`
            use_pct = float(parts[-2].replace('%', ''))
            used_inodes = int(parts[-4])
            capacity_inodes = int(parts[-5])
            if capacity_inodes > 0:
                use_pct = float(used_inodes) / float(capacity_inodes) * 100.
        except (ValueError, IndexError) as e:
            raise ValueError(f"Failed to parse numeric fields from df -i: {e} in '{' '.join(parts)}'")

        return {
            'capacity_inodes': float(capacity_inodes),
            'used_inodes': float(used_inodes),
            'inodes_utilization_percent': float(use_pct),
        }
