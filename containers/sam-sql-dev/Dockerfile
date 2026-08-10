FROM mysql:9

# Copy init script that creates databases, sets up remote root access,
# and optionally restores from /backup.sql.xz if provided at runtime.
COPY init-db.sh /docker-entrypoint-initdb.d/init-db.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-db.sh

# Post-restore DDL for tables that do not exist in the production snapshot yet.
# The entrypoint globs this directory in LC_ALL=C order, so `zz-` prefixed files
# run after init-db.sh's restore. See initdb.d/zz-90-xras_action_log.sql for why.
COPY initdb.d/ /docker-entrypoint-initdb.d/
