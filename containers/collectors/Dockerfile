FROM python:3

RUN echo "Creating cleanup scripts" && \
    <<'FILE1' cat > /usr/bin/list-large-directories && \
    chmod a+rx /usr/bin/list-large-directories && \
    echo "Done"
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
FILE1

RUN apt-get update && rm -rf /var/lib/apt/lists/*

WORKDIR /collectors
COPY collectors/ .
RUN find . -ls \
    && python --version \
    && pip install -r requirements.txt \
    && echo "done"

CMD ["/collectors/run_collectors.sh"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
