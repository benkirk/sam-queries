"""
Every external command the collectors run, and the two ways to get their output.

SshSource runs them over ssh from wherever the collector is (laptop, bare-metal
cron). SpoolSource reads what bin/run-manifest.sh captured on the host, so a
collector in a container needs neither ssh nor PBS. Both are keyed by the same
manifest; design: collectors/README.md § Spool mode.
"""

import os
import subprocess
import time

try:
    from .exceptions import ConfigError
except ImportError:
    from exceptions import ConfigError

PBS_COMMANDS = {
    'pbsnodes': 'pbsnodes -aj -F json',
    'qstat': 'qstat -f -F json',
    'qstat_Q': 'qstat -Q -f -F json',
    'pbs_rstat': 'pbs_rstat -f',
}

# ps (not who) sees VS Code Remote, non-interactive ssh, scp/sftp and detached tmux;
# user:32 widens the column, which otherwise prints a uid for names over 8 characters.
LOGIN_PROBE = ('cat /proc/loadavg; echo ---; who | wc -l; echo ---; nproc --all; '
               'echo ---; ps -eo uid=,user:32= | sort -u')

# Inlined /glade/u/home/csgteam/bin/jhlnodes, so spool mode needs no script on the host.
JHLNODES_SCRIPT = '/glade/u/home/csgteam/bin/jhlnodes'
JHLNODES = ('pbsnodes -Sj $(pbsnodes -a | grep -e ^c -e jhublogin '
            '| grep -B1 jhublogin | grep ^c | xargs)')


def df_command(path):
    return f'BLOCKSIZE=TiB df {path}; echo "~~~"; df -i {path}'


def manifest(pbs=False, filesystems=(), login_nodes=(), jhlnodes=False):
    """[(key, command)] for one collector; the host runs each command locally."""
    entries = []
    if pbs:
        entries += list(PBS_COMMANDS.items())
    entries += [(f'df.{i}', df_command(p)) for i, p in enumerate(filesystems)]
    for node in login_nodes:
        name = node['name'] if isinstance(node, dict) else node
        entries.append((f'login.{name}',
                        f"ssh -o BatchMode=yes -o ConnectTimeout=10 {name} '{LOGIN_PROBE}'"))
    if jhlnodes:
        entries.append(('jhlnodes', JHLNODES))
    for key, cmd in entries:
        if '\t' in cmd or '\n' in cmd:
            raise ValueError(f'manifest command for {key} must be one line without tabs')
    return entries


class SshSource:
    """Today's behavior: every command over ssh to the PBS host. Strings are frozen."""

    def __init__(self, host, pbs_timeout=30, ssh_timeout=10):
        self.host = host
        self.pbs_timeout = pbs_timeout
        self.ssh_timeout = ssh_timeout

    def _run(self, cmd, timeout):
        """(returncode, stdout, stderr); raises subprocess.TimeoutExpired."""
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def remote(self, cmd):
        return self._run(f'ssh -o ConnectTimeout={self.pbs_timeout} {self.host} "{cmd}"',
                         self.pbs_timeout)

    def login(self, node_name):
        # Nested hop: the login nodes are reachable from the PBS host, not from a laptop.
        return self._run(f"ssh -o ConnectTimeout={self.ssh_timeout} {self.host} "
                         f'"ssh {node_name} \'{LOGIN_PROBE}\'" ', self.ssh_timeout * 2)

    def jhlnodes(self):
        return self._run(f'ssh -o ConnectTimeout=10 {self.host} "{JHLNODES_SCRIPT}"', 30)


class SpoolSource:
    """Reads <key>.out/.err/.rc written by bin/run-manifest.sh."""

    def __init__(self, spool_dir, max_age=600):
        # Resolve the symlink once: run-manifest.sh may repoint it mid-parse.
        self.dir = os.path.realpath(spool_dir)
        meta = self._meta()
        finished = int(meta.get('finished', 0))
        age = time.time() - finished
        if age > max_age:
            raise ConfigError(f'spool {spool_dir} is {age:.0f}s old (limit {max_age}s); '
                              f'refusing to post stale data')
        self.host = meta.get('host', '?')

    def _meta(self):
        path = os.path.join(self.dir, 'scrape.meta')
        try:
            with open(path) as f:
                return dict(line.strip().split('=', 1) for line in f if '=' in line)
        except OSError as e:
            raise ConfigError(f'no spool at {self.dir}: {e}')

    def read(self, key):
        """(returncode, stdout, stderr) as captured; a missing key reads as rc 127."""
        def _slurp(ext):
            try:
                with open(os.path.join(self.dir, f'{key}.{ext}')) as f:
                    return f.read()
            except OSError:
                return None
        rc, out, err = _slurp('rc'), _slurp('out'), _slurp('err')
        if rc is None:
            return 127, '', f'{key} not in spool {self.dir}'
        return int(rc.strip() or 127), out or '', err or ''
