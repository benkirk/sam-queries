FROM mysql:9

# Copy init script that creates databases, sets up remote root access,
# and optionally restores from /backup.sql.xz if provided at runtime.
COPY init-db.sh /docker-entrypoint-initdb.d/init-db.sh
RUN chmod +x /docker-entrypoint-initdb.d/init-db.sh
