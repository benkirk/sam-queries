#!/bin/bash
# Thin cron wrapper. `exec` replaces this shell with run_ncar_accounting.sh
# so the flock(1) lock in crontab is held by one PID for the whole run
# (no parent shell lingering, no double-fork).
exec "$(dirname "${BASH_SOURCE[0]}")/run_ncar_accounting.sh" --last 2d
