FROM python:3 AS base

ENV DEBIAN_FRONTEND=noninteractive

# Bake an image-default /.env so the runtime's `load_dotenv()` (in
# sam.session, system_status.session, src/config.py) has a sane fallback
# for in-stack DB connection settings (hostname `mysql`, root/root) and
# the dev FLASK_SECRET_KEY. compose.yaml's environment block injects the
# user-tunable knobs (JUPYTERHUB_*, GOOGLE_CALENDAR_*, etc.) — those
# take precedence because python-dotenv defaults to override=False.
# Also bake a small in-image debug helper for ad-hoc image-size
# investigations from inside a running container (not called by code).
RUN <<'FILE_ENV' cat > /.env
#-------------------------
# local container instance
LOCAL_SAM_DB_SERVER=mysql
LOCAL_SAM_DB_USERNAME=root
LOCAL_SAM_DB_PASSWORD=root
SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}
SAM_DB_REQUIRE_SSL=false

#-------------------------
# system status database
STATUS_DB_DRIVER=mysql
STATUS_DB_SERVER=mysql
STATUS_DB_USERNAME=root
STATUS_DB_PASSWORD=root

#-------------------------
# Flask application security
# WARNING: dev-only key - change for production
FLASK_SECRET_KEY=dev-only-insecure-key-change-for-production
FILE_ENV

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
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[test]"

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
CMD ["gunicorn", "-c", "containers/webapp/gunicorn_config.py", "webapp.run:create_app()"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
