FROM mysql:9

# Copy init script that creates databases, sets up remote root access,
# and optionally restores from /backup.sql.xz if provided at runtime.
COPY init-db.sh /docker-entrypoint-initdb.d/init-db.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-db.sh

# There is deliberately no `COPY initdb.d/`.
#
# WARNING: if you need a post-restore DDL hook, recreate the directory AND this
# COPY together. An empty `initdb.d/` is not tracked by git, so a COPY of a
# directory that does not exist fails the image build rather than being a no-op.
#
# The utf8mb3/utf8mb4 split is asserted by
# `tests/integration/test_schema_validation.py::TestCharsetSplit`, in the default
# tier. That assertion is the only thing standing between a regenerated snapshot
# and silently losing audit rows on a 4-byte character.
