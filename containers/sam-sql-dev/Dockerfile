FROM mysql:9

# Copy init script that creates databases, sets up remote root access,
# and optionally restores from /backup.sql.xz if provided at runtime.
COPY init-db.sh /docker-entrypoint-initdb.d/init-db.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-db.sh

# There is deliberately no `COPY initdb.d/` any more.
#
# It carried three `CREATE TABLE IF NOT EXISTS` scripts (`zz-90` xras_action_log,
# `zz-91` xras_activation_event, `zz-92` notification_log) that existed only
# because those tables had no production counterpart, so the restored snapshot
# could not supply them. Both halves of that are now false: the tables were
# created in production on 2026-08-10, and the committed snapshot carries them
# (empty — the anonymizer purges all three). The scripts were self-retiring by
# design and have been retired.
#
# ⚠️ If you ever need a post-restore DDL hook again, recreate the directory AND
# this COPY together. An empty `initdb.d/` is not tracked by git, so a COPY of a
# directory that does not exist fails the image build rather than being a no-op.
#
# The utf8mb3/utf8mb4 split those files documented is now asserted by
# `tests/integration/test_schema_validation.py::TestCharsetSplit`, which runs in
# the default tier. That assertion is the replacement for the deleted DDL — it is
# the only thing standing between a regenerated snapshot and silently losing
# audit rows on a 4-byte character.
