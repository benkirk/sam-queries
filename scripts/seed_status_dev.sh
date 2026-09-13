#!/bin/bash
# seed_status_dev.sh — reseed system_status_dev from prod system_status on
# csg-postgres, so samuel-dev has real status data and its OWN task_run ledger.
#
# `task_run` data is excluded on purpose: prod rows would settle dev's slots,
# and the dev dispatcher must claim its own. The schema and alembic_version
# come across with the dump, so no migration step is needed. Restoring with
# --no-owner --no-privileges leaves every object owned by the restoring role;
# the app role gets its access from the database's default privileges (set
# once, docs/plans/K8S_DEV_ENVIRONMENT.md § 6.1) plus the re-grant below.
#
# Runs from a laptop on the VPN as the Postgres SUPERUSER (OpenBao
# csg/pg-superuser); the target database must already exist. Standard libpq
# variables select the server:
#   PGHOST      (default csg-postgres.k8s.ucar.edu)
#   PGUSER      (default postgres)
#   PGPASSWORD  (required)
#   PGSSLMODE   (default require)
#
# Usage:
#   PGPASSWORD=... scripts/seed_status_dev.sh [--no-color]
#
# Exit codes: 0 seeded / 1 precondition failed / 2 dump or restore failed.

set -euo pipefail

_LIBDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/common.sh
source "${_LIBDIR}/common.sh"

SOURCE_DB="system_status"
TARGET_DB="system_status_dev"
APP_ROLE="${STATUS_DEV_APP_ROLE:-pguser}"
export PGHOST="${PGHOST:-csg-postgres.k8s.ucar.edu}"
export PGUSER="${PGUSER:-postgres}"
export PGSSLMODE="${PGSSLMODE:-require}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-color) USE_COLOR=0; shift;;
        -h|--help)  usage_from_header "$0"; exit 0;;
        *) echo "Unknown option: $1" >&2; exit 1;;
    esac
done
setup_colors

for c in pg_dump pg_restore psql; do command -v "$c" >/dev/null 2>&1 || die "$c not found in PATH"; done
[[ -n "${PGPASSWORD:-}" ]] || die "PGPASSWORD is required (the postgres superuser, OpenBao csg/pg-superuser)"

exists() { psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$1'" 2>/dev/null; }
[[ "$(exists "$TARGET_DB")" == "1" ]] || die "$TARGET_DB does not exist on $PGHOST — create it first (K8S_DEV_ENVIRONMENT.md § 6.1)"
[[ "$(exists "$SOURCE_DB")" == "1" ]] || die "$SOURCE_DB not found on $PGHOST"
info "seeding $TARGET_DB from $SOURCE_DB on $PGHOST as $PGUSER (task_run data excluded)"

# --clean --if-exists makes the reseed idempotent; a first seed into an empty
# database simply has nothing to drop.
if ! pg_dump -Fc --exclude-table-data=task_run "$SOURCE_DB" \
     | pg_restore -d "$TARGET_DB" --clean --if-exists --no-owner --no-privileges --exit-on-error; then
    echo -e "  ${RED}✘${NC} dump/restore failed; $TARGET_DB may be partially restored" >&2
    exit 2
fi

# Idempotent re-grant: covers objects that predate the default privileges.
psql -d "$TARGET_DB" -q <<SQL
GRANT USAGE ON SCHEMA public TO $APP_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $APP_ROLE;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $APP_ROLE;
SQL

tables=$(psql -d "$TARGET_DB" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
ledger=$(psql -d "$TARGET_DB" -Atc "SELECT count(*) FROM task_run")
stamp=$(psql -d "$TARGET_DB" -Atc "SELECT version_num FROM alembic_version" 2>/dev/null || echo "none")
echo -e "  ${GREEN}✔${NC} $TARGET_DB seeded: $tables tables, alembic $stamp, task_run rows=$ledger (expected 0), grants to $APP_ROLE"
