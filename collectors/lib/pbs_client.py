"""
PBS command execution client.
"""

import json
import logging
import subprocess

try:
    from .exceptions import PBSCommandError, PBSParseError
    from .commands import PBS_COMMANDS, SshSource
except ImportError:
    from exceptions import PBSCommandError, PBSParseError
    from commands import PBS_COMMANDS, SshSource


class PBSClient:
    """
    PBS command execution: over ssh (default) or from a host-captured spool.
    Handles timeouts and error capture.
    """

    def __init__(self, host, timeout=30, source=None):
        self.host = host
        self.timeout = timeout
        self.source = source
        self.ssh = SshSource(host, pbs_timeout=timeout)
        self.logger = logging.getLogger(__name__)

    def run_command(self, cmd, json_output=False):
        """
        Execute a command on the PBS host via SSH.

        Raises:
            PBSCommandError: If command fails or times out
        """
        self.logger.debug(f"Running: {cmd}")
        try:
            rc, out, err = self.ssh.remote(cmd)
        except subprocess.TimeoutExpired:
            raise PBSCommandError(f"Command timed out after {self.timeout}s: {cmd}")
        return self._result(cmd, rc, out, err, json_output)

    def fetch(self, key, json_output=False):
        """Output of manifest key (see commands.PBS_COMMANDS) from the spool or over ssh."""
        if self.source is None:
            return self.run_command(PBS_COMMANDS[key], json_output=json_output)
        rc, out, err = self.source.read(key)
        return self._result(PBS_COMMANDS[key], rc, out, err, json_output)

    def _result(self, cmd, rc, output, stderr, json_output):
        if rc != 0:
            error_msg = stderr.strip() or output.strip()
            raise PBSCommandError(f"Command failed (exit {rc}): {cmd}\n{error_msg}")

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
        return self.fetch('pbsnodes', json_output=True)

    def get_jobs_json(self):
        """Execute qstat -f -F json"""
        return self.fetch('qstat', json_output=True)

    def get_queues_json(self):
        """Execute qstat -Q -f -F json (full queue roster, incl. routing queues)"""
        return self.fetch('qstat_Q', json_output=True)

    def get_reservations(self):
        """Execute pbs_rstat -f"""
        return self.fetch('pbs_rstat')
