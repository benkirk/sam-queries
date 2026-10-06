#!/usr/bin/env bash
# A second SAM dev server from another worktree, for before/after browser checks.
#
#   scripts/dev_server_alt.sh <worktree> <port> [redis-db]
#
# Serves <worktree>/src on the host (local MySQL :3306, Redis :6379 on its own DB index, which
# it flushes so a previous run's fragments cannot leak in). Every outbound lever is forced off;
# ALT_FS_SCANS_ENABLED=1 turns fs-scans on (read-only; default 0). ALT_SAM_DB_PORT=3307 serves the
# obfuscated test DB instead of the real local one: the deck's screenshots need it.
# Loads .env (searched upward from this repo) and NEVER prints a value from it: hand-copying a
# running server's env printed a Jira token into a session transcript.
set -eo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WT=$(cd "${1:?usage: dev_server_alt.sh <worktree> <port> [redis-db]}" && pwd)
PORT=${2:?usage: dev_server_alt.sh <worktree> <port> [redis-db]}
REDIS_DB=${3:-$((2 + PORT % 14))}   # never 0/1, which samuel-dev uses for cache and rate limits

dir=$ROOT
while [[ ! -f $dir/.env && $dir != / ]]; do dir=$(dirname "$dir"); done
[[ -f $dir/.env ]] || { echo "no .env above $ROOT" >&2; exit 1; }
set -a
# shellcheck source=/dev/null
source "$dir/.env" >/dev/null 2>&1
set +a

PY=${SAM_PYTHON:-$ROOT/conda-env/bin/python}
[[ -x $PY ]] || PY=$(command -v python3)

export SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER:-127.0.0.1} SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME:-root}
export SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD:-root} SAM_DB_DRIVER=mysql SAM_DB_NAME=sam SAM_DB_PORT=${ALT_SAM_DB_PORT:-}
export STATUS_DB_DRIVER=mysql STATUS_DB_SERVER=127.0.0.1 STATUS_DB_USERNAME=root STATUS_DB_PASSWORD=root
export CACHE_REDIS_URL=redis://127.0.0.1:6379/$REDIS_DB RATELIMIT_STORAGE_URI=memory://
export NOTIFY_ENABLED=0 NOTIFY_TRANSPORT=null XRAS_API_KEY= XRAS_OUTGOING_ENABLED=0 XRAS_WRITE_ENABLED=0
export XRAS_ACTIONS_ENABLED=0 JIRA_ENABLED=0 JIRA_WRITE_ENABLED=0
export FS_SCANS_ENABLED=${ALT_FS_SCANS_ENABLED:-0}
unset JIRA_TOKEN
export FLASK_CONFIG=development AUTH_PROVIDER=stub TZ=America/Denver MPLBACKEND=Agg
export WEBAPP_PORT=$PORT PYTHONPATH=$WT/src PYTHONDONTWRITEBYTECODE=1

"$PY" -c "import os, redis; redis.Redis.from_url(os.environ['CACHE_REDIS_URL']).flushdb()" \
    || echo "warning: could not flush redis db $REDIS_DB" >&2
echo "serving $WT on http://localhost:$PORT (redis db $REDIS_DB flushed, outbound off)"
cd "$WT"
exec "$PY" src/webapp/run.py
