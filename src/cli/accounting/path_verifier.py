"""Path existence probe for reconcile-quotas — local or remote via SSH.

Separating this from ``commands.py`` keeps the SSH protocol in one
testable place and lets future quota readers plug in without touching
the reconcile flow.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Literal, Optional


# Reject paths with control characters — the SSH stdin protocol is
# newline-delimited, so any embedded \n would corrupt the batch.
_MAX_PATH_LEN = 4096


class PathVerificationError(Exception):
    """Raised when the verifier cannot produce authoritative answers.

    Callers should abort the reconcile rather than treat an empty/partial
    result as "everything missing" — that would misclassify every orphan
    as safe-to-deactivate.
    """


class PathVerifier:
    """Probe a batch of paths for existence, either locally or via SSH.

    Local mode: ``os.path.exists`` per path.

    SSH mode: one non-interactive SSH invocation pipes the paths (one per
    line) into ``bash -s`` on the remote host; the remote loop emits
    ``EXISTS <path>`` / ``MISSING <path>`` per line. One handshake, not N.
    """

    def __init__(
        self,
        mode: Literal['local', 'ssh'],
        host: Optional[str] = None,
        *,
        timeout: int = 60,
    ):
        if mode == 'ssh' and not host:
            raise ValueError("SSH mode requires a host")
        self.mode = mode
        self.host = host
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Auto-detect helpers
    # ------------------------------------------------------------------

    @staticmethod
    def probe_host(host: str, mount_root: str, *, timeout: int = 5) -> bool:
        """Return True if ``host`` is reachable over SSH and has ``mount_root``."""
        try:
            result = subprocess.run(
                ['ssh',
                 '-o', 'BatchMode=yes',
                 '-o', f'ConnectTimeout={timeout}',
                 host, f'test -d {mount_root!r}'],
                capture_output=True, timeout=timeout + 2,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    # ------------------------------------------------------------------
    # Core probe
    # ------------------------------------------------------------------

    def check(self, paths: list[str]) -> dict[str, bool]:
        if not paths:
            return {}
        self._sanity_check(paths)
        if self.mode == 'local':
            return {p: os.path.exists(p) for p in paths}
        return self._check_ssh(paths)

    @staticmethod
    def _sanity_check(paths: list[str]) -> None:
        for p in paths:
            if '\n' in p or '\r' in p:
                raise PathVerificationError(
                    f"Path contains newline/carriage-return, refusing to probe: {p!r}"
                )
            if len(p) > _MAX_PATH_LEN:
                raise PathVerificationError(
                    f"Path exceeds {_MAX_PATH_LEN} chars, refusing to probe"
                )

    def _check_ssh(self, paths: list[str]) -> dict[str, bool]:
        # The script is the *program*; paths flow through stdin to its
        # `while read` loop. Send the program via `bash -c '<script>'`
        # (shell-quoted to survive SSH's command-string flattening on
        # the remote side) rather than `bash -s`, which reads the
        # program itself from stdin and would consume our paths.
        # `|| [ -n "$p" ]` keeps the loop running for the final line
        # even when stdin lacks a trailing newline (read returns non-zero
        # on EOF mid-line).
        script = (
            'while IFS= read -r p || [ -n "$p" ]; do '
            '  if [ -e "$p" ]; then echo "EXISTS $p"; else echo "MISSING $p"; fi; '
            'done'
        )
        remote_cmd = f'bash -c {shlex.quote(script)}'
        stdin_payload = '\n'.join(paths) + '\n'
        try:
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', self.host, remote_cmd],
                input=stdin_payload,
                capture_output=True, text=True,
                timeout=self.timeout, check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise PathVerificationError(
                f"SSH path verification timed out after {self.timeout}s "
                f"against host {self.host!r}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or '').strip()
            raise PathVerificationError(
                f"SSH path verification failed (host={self.host!r}, "
                f"exit={exc.returncode}): {stderr or '<no stderr>'}"
            ) from exc
        except OSError as exc:
            raise PathVerificationError(
                f"Could not launch ssh to {self.host!r}: {exc}"
            ) from exc

        out: dict[str, bool] = {}
        for line in result.stdout.splitlines():
            state, _, p = line.partition(' ')
            if state in ('EXISTS', 'MISSING') and p:
                out[p] = (state == 'EXISTS')
        # Every input path must have produced exactly one line
        missing_from_output = [p for p in paths if p not in out]
        if missing_from_output:
            raise PathVerificationError(
                f"SSH verification output was incomplete — "
                f"{len(missing_from_output)} path(s) had no response. "
                f"First missing: {missing_from_output[0]!r}"
            )
        return out


def auto_detect_verifier(
    *,
    mount_root: Optional[str],
    mount_hosts: list[str],
    explicit_host: Optional[str],
) -> tuple[PathVerifier, str]:
    """Pick local vs ssh mode according to the approved rules.

    Returns (verifier, banner) where ``banner`` is a short human-readable
    description of which mode was chosen — e.g. ``"local"`` or
    ``"via ssh derecho"``.

    Raises PathVerificationError if no usable source is found.
    """
    if mount_root and os.path.ismount(mount_root):
        return PathVerifier('local'), 'local'

    if explicit_host:
        return PathVerifier('ssh', host=explicit_host), f'via ssh {explicit_host}'

    if not mount_root:
        raise PathVerificationError(
            "Path verification requested but this reader has no mount_root; "
            "pass --verify-host <host> to specify an SSH target."
        )

    for host in mount_hosts:
        if PathVerifier.probe_host(host, mount_root):
            return PathVerifier('ssh', host=host), f'via ssh {host}'

    tried = ', '.join(mount_hosts) or '(none)'
    raise PathVerificationError(
        f"--verify-paths: {mount_root!r} is not mounted locally and no "
        f"configured host responded (tried: {tried}). "
        "Pass --verify-host <host> for a manual override."
    )
