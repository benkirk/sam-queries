FROM python:3 AS base

ENV DEBIAN_FRONTEND=noninteractive

# In-image debug helper for investigating image bloat / unexpected
# on-disk state from inside a running container. Invoked manually via
# `docker compose exec`; not called by repo code.
RUN <<'FILE_LLD' cat > /usr/bin/list-large-directories && \
    chmod a+rx /usr/bin/list-large-directories
#!/bin/bash

dirs="/home /mnt /tmp /opt /usr /var /container"
if [[ ${#} -ge 1 ]]; then
    dirs=${@}
fi

THRESHOLD_SIZE_MB=4

echo "[${0}]: Finding large files & directories >${THRESHOLD_SIZE_MB}MiB in ${dirs} - output is MiB"
for dir in ${dirs}; do
    dir=$(realpath ${dir})
    if [[ -d ${dir} ]]; then
        find 2>/dev/null ${dir} -type f -size +${THRESHOLD_SIZE_MB}M -print0 | xargs -0 --no-run-if-empty du --block-size=1MiB | sort -n
        du 2>/dev/null --block-size=1MiB --threshold=${THRESHOLD_SIZE_MB}MiB --separate-dirs ${dir} | sort -n
    fi
done
FILE_LLD

WORKDIR /code
COPY . .

# Branch / tag / SHA of hpc-usage-queries to install. Override at build time
# to test against an in-flight peer branch:
#   HPC_USAGE_QUERIES_REF=my-branch docker compose build webdev
ARG HPC_USAGE_QUERIES_REF=main
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[test]" && \
    pip install --no-cache-dir "hpc-usage-queries[postgres] @ git+https://github.com/benkirk/hpc-usage-queries.git@${HPC_USAGE_QUERIES_REF}"

# hpc-scheduling-tools (private NCAR repo) — optional plugin, installed over SSH.
# The read-only deploy key arrives as a BuildKit secret (never a layer); an
# absent key (local build without creds) skips the install. required=false.
ARG HPC_SCHEDULING_TOOLS_REF=main
RUN --mount=type=secret,id=hpc_sched_deploy_key,target=/run/secrets/hpc_sched,required=false <<'EOF'
set -eu
if [ -s /run/secrets/hpc_sched ]; then
  # Copy the read-only secret out and normalize it: strip CR and guarantee a
  # trailing newline, or OpenSSH rejects the key ("error in libcrypto") —
  # GitHub secret storage strips the key's final newline. Removed same-layer.
  tr -d '\r' < /run/secrets/hpc_sched > /root/hpc_sched_key
  [ -n "$(tail -c1 /root/hpc_sched_key)" ] && printf '\n' >> /root/hpc_sched_key || true
  chmod 600 /root/hpc_sched_key
  export GIT_SSH_COMMAND="ssh -i /root/hpc_sched_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
  pip install --no-cache-dir "hpc-scheduling-tools @ git+ssh://git@github.com/NCAR/hpc-scheduling-tools.git@${HPC_SCHEDULING_TOOLS_REF}"
  rm -f /root/hpc_sched_key
else
  echo "NOTE: no hpc_sched deploy key mounted; skipping hpc-scheduling-tools (optional plugin)."
fi
EOF

# Optional build provenance - set by CI via --build-arg; empty locally.
# Placed AFTER pip install so a changing SHA does not invalidate the install layer.
ARG GIT_SHA=""
ARG BUILD_DATE=""
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}


#-------------------------------------------------------------------------------
# webdev - development flask internal sever config
#-------------------------------------------------------------------------------
FROM base AS development
CMD ["python3", "./src/webapp/run.py"]

#-------------------------------------------------------------------------------
# webapp - production gunicorn config
#-------------------------------------------------------------------------------
FROM base AS production
# Explicitly clear dev-auth vars — these must never be set in the production image
ENV DISABLE_AUTH=0
ENV DEV_AUTO_LOGIN_USER=

# Run as a fixed non-root UID (pairs with helm runAsNonRoot/runAsUser).
# - /var/log/sam: audit-log dir — chown so RotatingFileHandler writes there in
#   k8s (locally a bind mount overrides it; see compose.yaml).
# - /code (non-recursive): CI runs pytest inside this container and writes
#   /code/.coverage + /code/htmlcov — the dir inode must be owned by the app UID.
RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin sam && \
    mkdir -p /var/log/sam && \
    chown sam:sam /var/log/sam /code

# Matplotlib builds a font cache at import; point it at world-writable /tmp.
ENV MPLCONFIGDIR=/tmp/mpl

# Numeric (not "USER sam") so the kubelet's runAsNonRoot check can verify the UID.
USER 1000
CMD ["gunicorn", "-c", "containers/webapp/gunicorn_config.py", "webapp.run:create_app()"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
