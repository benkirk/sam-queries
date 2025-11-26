"""
Filesystem usage parser.
"""

import logging
import re
from typing import List, Dict


class FilesystemParser:
    """Parse df output for filesystem status."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def parse_filesystems(df_output: str, fs_config: List[str]) -> List[dict]:
        """
        Parse df output into filesystem status.

        Args:
            df_output: Output from 'df' command
            fs_config: List of filesystem names to track

        Returns:
            List of filesystem status dicts
        """
        filesystems = []

        for line in df_output.strip().split('\n'):
            if line.startswith('Filesystem') or not line.strip():
                continue

            parts = line.split()
            if len(parts) < 6:
                continue

            # df output: Filesystem  Size  Used  Avail  Use%  Mounted
            filesystem = parts[0]
            size_str = parts[1]
            used_str = parts[2]
            avail_str = parts[3]
            use_pct_str = parts[4]
            mountpoint = parts[5]

            # Parse size (could be in K, M, G, T)
            def parse_size(s):
                """Convert size string to TB."""
                s = s.upper()
                if 'T' in s:
                    return float(s.replace('T', ''))
                elif 'G' in s:
                    return float(s.replace('G', '')) / 1024
                elif 'M' in s:
                    return float(s.replace('M', '')) / (1024 * 1024)
                elif 'K' in s:
                    return float(s.replace('K', '')) / (1024 * 1024 * 1024)
                else:
                    # Assume bytes
                    try:
                        return float(s) / (1024 ** 4)
                    except ValueError:
                        return 0.0

            try:
                size_tb = parse_size(size_str)
                used_tb = parse_size(used_str)
                utilization = int(use_pct_str.replace('%', ''))
            except (ValueError, IndexError):
                continue

            # Match to configured filesystem with specific mapping
            # Map mountpoints to filesystem names to avoid duplicates
            fs_name = None
            if '/glade/u' in mountpoint:
                fs_name = 'glade' if 'glade' in fs_config else None
            elif '/glade/work' in mountpoint:
                fs_name = 'glade' if 'glade' in fs_config else None
            elif '/glade/campaign' in mountpoint:
                fs_name = 'campaign' if 'campaign' in fs_config else None
            elif '/glade/derecho/scratch' in mountpoint:
                fs_name = 'derecho_scratch' if 'derecho_scratch' in fs_config else None
            else:
                # Fallback to substring match for other filesystems
                for configured_fs in fs_config:
                    if configured_fs.lower() in mountpoint.lower():
                        fs_name = configured_fs
                        break

            if fs_name:
                # Check if we already have this filesystem (avoid duplicates)
                if not any(fs['filesystem_name'] == fs_name for fs in filesystems):
                    filesystems.append({
                        'filesystem_name': fs_name,
                        'available': True,
                        'degraded': utilization > 90,  # Mark degraded if >90% full
                        'capacity_tb': round(size_tb, 2),
                        'used_tb': round(used_tb, 2),
                        'utilization_percent': float(utilization),
                    })

        return filesystems
