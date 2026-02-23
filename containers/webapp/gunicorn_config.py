"""Gunicorn production configuration for SAM webapp.

Documentation: https://docs.gunicorn.org/en/stable/settings.html
"""
import multiprocessing
import os

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

# Logging — stdout/stderr by default (Docker captures them)
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog  = os.environ.get('GUNICORN_ERROR_LOG',  '-')
loglevel  = os.environ.get('GUNICORN_LOG_LEVEL',  'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'

proc_name = 'sam-webapp'
