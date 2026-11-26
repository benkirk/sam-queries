"""In-namespace log archiver: copies every samuel pod's stdout to a volume.

Pod stdout dies with the pod, so each deploy empties the only log sink we can
read. `python -m logarchive run` follows the pods through the Kubernetes API and
keeps their lines `LOGARCHIVE_RETENTION_DAYS` days; `cat` and `status` read them.

Stdlib plus `requests`. Imports nothing from `sam`, `webapp`, `system_status`
or `cli`, and the webapp never talks to it: if it dies we lose archive, not
traffic. Design, traps and the loss window: docs/plans/implemented/DURABLE_LOGS.md.
"""
