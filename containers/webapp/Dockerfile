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
