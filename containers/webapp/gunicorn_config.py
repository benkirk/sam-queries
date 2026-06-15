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

# Worker processes: (2 × cores) + 1, overridable via env
workers = int(os.environ.get('GUNICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'sync')
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
