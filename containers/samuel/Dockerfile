# One minor for both stages, so compiled wheels from `build` match `base`'s ABI.
ARG PY_MINOR=3.14

#-------------------------------------------------------------------------------
# build - full image (git, ssh, compilers) installs every dependency; only
# site-packages and the console scripts reach the runtime image.
#-------------------------------------------------------------------------------
FROM python:${PY_MINOR} AS build
WORKDIR /build

# Dependencies from pyproject.toml alone, before any source is copied, so this
# layer is reused by every commit that leaves the dependency list unchanged.
COPY pyproject.toml .
RUN <<'EOF'
set -eu
python - > requirements.txt <<'PY'
import tomllib
project = tomllib.load(open('pyproject.toml', 'rb'))['project']
print('\n'.join(project['dependencies'] + project['optional-dependencies']['test']))
PY
pip install --no-cache-dir setuptools wheel -r requirements.txt
EOF

# Branch / tag / SHA of hpc-usage-queries to install. Override at build time
# to test against an in-flight peer branch:
#   HPC_USAGE_QUERIES_REF=my-branch docker compose build samuel-dev
ARG HPC_USAGE_QUERIES_REF=main
RUN pip install --no-cache-dir "hpc-usage-queries[postgres] @ git+https://github.com/benkirk/hpc-usage-queries.git@${HPC_USAGE_QUERIES_REF}"

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

# Console scripts only (python shebang); the interpreter binaries stay slim's own.
RUN mkdir /opt/scripts && for f in /usr/local/bin/*; do \
        head -c 64 "$f" | head -n1 | grep -q '^#!.*python' && cp -p "$f" /opt/scripts/ || true; \
    done

#-------------------------------------------------------------------------------
# base - slim runtime shared by development and production
#-------------------------------------------------------------------------------
FROM python:${PY_MINOR}-slim AS base
ARG PY_MINOR

ENV DEBIAN_FRONTEND=noninteractive

# tzdata: without it TZ=America/Denver silently resolves to UTC and every naive
# Mountain SAM date shifts; smoke.sh asserts it. make: CI runs clone-pg-test here.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata make && \
    rm -rf /var/lib/apt/lists/*

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

# Replace slim's site-packages outright, so its pip cannot leave a second
# dist-info beside the build stage's.
RUN rm -rf /usr/local/lib/python${PY_MINOR}/site-packages
COPY --from=build /usr/local/lib/python${PY_MINOR}/site-packages /usr/local/lib/python${PY_MINOR}/site-packages
COPY --from=build /opt/scripts/ /usr/local/bin/

# capacities.json for the fairshare endpoint. Baked as a default so the plugin's
# load_config resolves in-container; prod mounts a ConfigMap over this path
# (helm) so N_cpu/N_gpu are tunable without a rebuild — the plugin repo keeps
# it out of the wheel on purpose (operator-tunable, must track sched_config).
COPY helm/files/capacities.json /etc/sam/capacities.json
ENV HPC_SCHED_CAPACITIES=/etc/sam/capacities.json

# The source and its editable install are the only per-commit layers. The
# install must happen here, not in `build`: its finder file would otherwise
# change the site-packages layer on every commit.
WORKDIR /code
COPY . .
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

# Optional build provenance - set by CI via --build-arg; empty locally.
# Placed AFTER pip install so a changing SHA does not invalidate the install layer.
ARG GIT_SHA=""
ARG BUILD_DATE=""
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}


#-------------------------------------------------------------------------------
# samuel-dev - development flask internal sever config
#-------------------------------------------------------------------------------
FROM base AS development
CMD ["python3", "./src/webapp/run.py"]

#-------------------------------------------------------------------------------
# samuel - production gunicorn config
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
CMD ["gunicorn", "-c", "containers/samuel/gunicorn_config.py", "webapp.run:create_app()"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
