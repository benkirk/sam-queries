"""
PBS command execution client.
"""

import json
import logging
import subprocess

try:
    from .exceptions import PBSCommandError, PBSParseError
except ImportError:
    from exceptions import PBSCommandError, PBSParseError


class PBSClient:
    """
    Wrapper for PBS command execution.
    Handles SSH invocation, timeouts, and error capture.
    """

    def __init__(self, host, timeout=30):
        self.host = host
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def run_command(self, cmd, json_output=False):
        """
        Execute PBS command via SSH.

        Args:
            cmd: Command to run (e.g., "pbsnodes -aj -F json")
            json_output: If True, parse JSON response

        Returns:
            Parsed JSON dict or raw string output

        Raises:
            PBSCommandError: If command fails or times out
        """
        full_cmd = f'ssh -o ConnectTimeout={self.timeout} {self.host} "{cmd}"'

        self.logger.debug(f"Running: {full_cmd}")

        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            raise PBSCommandError(f"Command timed out after {self.timeout}s: {cmd}")

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            raise PBSCommandError(f"Command failed (exit {result.returncode}): {cmd}\n{error_msg}")

        output = result.stdout

        if json_output:
            try:
                return json.loads(output)
            except json.JSONDecodeError as e:
                self.logger.error(f"JSON parse error: {e}")
                self.logger.error(f"Output (first 500 chars): {output[:500]}")
                raise PBSParseError(f"Invalid JSON from {cmd}: {e}")

        return output

    def get_nodes_json(self):
        """Execute pbsnodes -aj -F json"""
        return self.run_command("pbsnodes -aj -F json", json_output=True)

    def get_jobs_json(self):
        """Execute qstat -f -F json"""
        return self.run_command("qstat -f -F json", json_output=True)

    def get_queue_summary(self):
        """Execute qstat -Qa"""
        return self.run_command("qstat -Qa")

    def get_reservations(self):
        """Execute pbs_rstat -f"""
        return self.run_command("pbs_rstat -f")
