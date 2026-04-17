#!/usr/bin/env bash
#
# Interactive debug launcher for the SAM webapp.
#
# Runs Flask directly (not via gunicorn, not in Docker) so you get the
# Werkzeug debugger, auto-reloader, and full tracebacks on errors.
#
# Defaults target the isolated test database (mysql-test container on
# port 3307) so this launcher can safely coexist with `docker compose up
# webdev` (port 5050) and NEVER touches production data.
#
# Port map:
#   5050  → docker compose `webdev` service
#   5051  → this debug launcher (default — override with PORT=...)
#   7050  → docker compose `webapp` (production image)
#
# Prereqs:
#   - mysql-test container running:  docker compose --profile test up -d mysql-test
#   - Conda env + .env loaded (handled by etc/config_env.sh below)
#
# Usage:
#   utils/run-webui-dbg.sh              # defaults: port 5051, test DB
#   PORT=5052 utils/run-webui-dbg.sh    # alt port
#   USE_DEV_DB=1 utils/run-webui-dbg.sh # override to main dev DB (risky — 3306)
#
#----------------------------------------------------------------------------

set -u

# Determine script directory (bash + zsh compatible)
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  SCRIPT_PATH="${(%):-%x}"
else
  echo "Unknown shell!" >&2
  exit 1
fi
SCRIPTDIR="$(cd "$(dirname "$(realpath "${SCRIPT_PATH}")")" >/dev/null 2>&1 && pwd)"

# Load conda env and .env variables
source "${SCRIPTDIR}/../etc/config_env.sh"

# --- Defaults (override via env vars) ---------------------------------------
PORT="${PORT:-5051}"

if [ "${USE_DEV_DB:-0}" = "1" ]; then
    # User explicitly requested the main dev DB (3306). Leave SAM_DB_* alone
    # so whatever .env provides takes effect.
    DB_TARGET="dev (from .env)"
else
    # Point at the isolated mysql-test container (3307). This is the safe
    # default — prevents accidental writes to whatever the .env points at.
    export SAM_DB_SERVER=127.0.0.1
    export SAM_DB_PORT=3307
    export SAM_DB_USERNAME=root
    export SAM_DB_PASSWORD=root
    export SAM_DB_NAME=sam
    export STATUS_DB_SERVER=127.0.0.1
    export STATUS_DB_PORT=3307
    export STATUS_DB_USERNAME=root
    export STATUS_DB_PASSWORD=root
    DB_TARGET="mysql-test (127.0.0.1:3307)"
fi

# --- Port collision check ---------------------------------------------------
if lsof -iTCP:${PORT} -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo "ERROR: port ${PORT} is already in use." >&2
    echo "  Another webapp is probably running there." >&2
    echo "  Either stop it, or re-run with: PORT=<other> $0" >&2
    lsof -iTCP:${PORT} -sTCP:LISTEN -P -n >&2
    exit 1
fi

# --- Test DB sanity check (when using the default safe path) ---------------
if [ "${USE_DEV_DB:-0}" != "1" ]; then
    if ! mysqladmin ping -h 127.0.0.1 -P 3307 -u root -proot --silent 2>/dev/null; then
        echo "ERROR: mysql-test container not reachable on 127.0.0.1:3307" >&2
        echo "  Start it with: docker compose --profile test up -d mysql-test" >&2
        echo "  Or override this check with: USE_DEV_DB=1 $0 (risky)" >&2
        exit 1
    fi
fi

# --- Launch -----------------------------------------------------------------
echo "================================================================"
echo "Launching SAM webapp (debug mode)"
echo "  Port:      ${PORT}"
echo "  Database:  ${DB_TARGET}"
echo "  Auth:      disabled (DEV_AUTO_LOGIN_USER=benkirk)"
echo "  Config:    development (for Werkzeug debugger)"
echo "================================================================"

exe="$(realpath "${SCRIPTDIR}/../src/webapp/run.py")"

DISABLE_AUTH=1 \
  DEV_AUTO_LOGIN_USER=benkirk \
  FLASK_DEBUG=1 \
  FLASK_CONFIG=development \
  WEBAPP_PORT="${PORT}" \
  python3 "${exe}"
