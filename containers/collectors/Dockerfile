FROM python:3

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
