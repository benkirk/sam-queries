FROM python:3

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y \
               rsync git git-lfs \
    && pip install --upgrade pip \
    && rm -rf /var/lib/apt/lists/*

RUN echo "Creatning default .env" && \
    <<'FILE1' cat > /.env && \
    echo "Done"
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
STATUS_DB_SERVER=mysql
STATUS_DB_USERNAME=root
STATUS_DB_PASSWORD=root
FILE1

WORKDIR /code
COPY . .
RUN pip install -e ".[test]"

CMD ["python3", "./src/webapp/run.py"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
