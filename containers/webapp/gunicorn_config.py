"""Gunicorn production configuration for SAM webapp.

Documentation: https://docs.gunicorn.org/en/stable/settings.html
"""
import logging
import multiprocessing
import os
import re

_HEALTH_PATH_RE = re.compile(r'^/api/v\d+/health(/|$)')


class _SkipHealthProbes(logging.Filter):
    """Drop successful /api/v<N>/health/* access-log lines (every-10s noise)."""
    def filter(self, record):
        args = record.args or {}
        if not isinstance(args, dict):
            return True
        if not _HEALTH_PATH_RE.match(args.get('U', '')):
            return True
        try:
            return int(args.get('s', 0)) >= 400  # keep failures only
        except (TypeError, ValueError):
            return True


def post_fork(server, worker):
    logging.getLogger('gunicorn.access').addFilter(_SkipHealthProbes())

# Server socket — match compose.yaml port mapping (host:7050 → container:5050)
bind = '0.0.0.0:5050'
backlog = 2048

def _effective_cpus():
    """CPUs available to THIS container, not the node.

    multiprocessing.cpu_count() returns the host's core count (e.g. 64 on the
    nwc1 nodes), which over-provisions workers and OOMs the pod. Read the
    cgroup CPU quota instead — cgroup v2 (``cpu.max``) then v1
    (``cfs_quota_us`` / ``cfs_period_us``) — and fall back to cpu_count()
    only when unconstrained or unreadable.
    """
    try:  # cgroup v2
        with open('/sys/fs/cgroup/cpu.max') as fh:
            quota, period = fh.read().split()
        if quota != 'max':
            return max(1, round(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        with open('/sys/fs/cgroup/cpu/cpu.cfs_quota_us') as fh:
            quota = int(fh.read())
        with open('/sys/fs/cgroup/cpu/cpu.cfs_period_us') as fh:
            period = int(fh.read())
        if quota > 0:
            return max(1, round(quota / period))
    except (OSError, ValueError):
        pass
    return multiprocessing.cpu_count()


# Concurrency model — gthread (threaded), not sync. SAM routes are I/O-bound
# (DB waits); the heaviest — fs-scans on-the-fly scans — measured ~76-85%
# blocked in Postgres, where psycopg2 releases the GIL, so threads overlap
# those waits and a slow scan ties up a THREAD, not a whole process. This runs
# far fewer processes than the old sync model for the same (or higher)
# concurrency → lower memory and a smaller DB-connection fan-out. All knobs are
# env-overridable; helm sets them explicitly (see deployment.yaml).
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread')
# Default workers track the cgroup CPU quota, NOT the node's core count, so the
# default is safe even if GUNICORN_WORKERS is unset. helm pins it explicitly.
workers = int(os.environ.get('GUNICORN_WORKERS', _effective_cpus() * 2 + 1))
threads = int(os.environ.get('GUNICORN_THREADS', 4))
worker_connections = 1000

# Worker lifecycle (prevents memory leaks)
max_requests = 1000       # Restart workers after N requests
max_requests_jitter = 50  # Randomize to avoid thundering herd
timeout = 120             # Kill worker if silent for this many seconds
graceful_timeout = 30     # Wait this long for workers to finish on shutdown
keepalive = 5

# App loading
preload_app = True   # Load app before forking → faster startup, shared memory
daemon = False       # Stay in foreground (required for containers)

# Trust X-Forwarded-* from the ingress. gunicorn is only reachable via the
# in-cluster ingress (ClusterIP service, never published directly), so trusting
# forwarded headers from any peer is the standard k8s setting and lets it honor
# X-Forwarded-Proto for correct https scheme detection. Real client-IP recovery
# for the app is handled by ProxyFix (see webapp/run.py); this is the gunicorn
# side of the same story.
forwarded_allow_ips = os.environ.get('FORWARDED_ALLOW_IPS', '*')

# Logging — stdout/stderr by default (Docker captures them)
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog  = os.environ.get('GUNICORN_ERROR_LOG',  '-')
loglevel  = os.environ.get('GUNICORN_LOG_LEVEL',  'info')
# %(h)s is the raw socket peer (the in-cluster proxy), so it is NOT the client.
# The trailing xff="…" logs the full X-Forwarded-For chain: its leftmost entry
# is the real client, and counting the entries tells us the correct
# PROXYFIX_X_FOR hop depth (see webapp/run.py).
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs xff="%({x-forwarded-for}i)s"'

proc_name = 'sam-webapp'
