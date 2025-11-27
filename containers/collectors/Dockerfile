FROM python:3

WORKDIR /collectors
COPY collectors/ .
RUN find . -ls \
    && pip install -r requirements.txt \
    && echo "done2"

RUN ./casper/collector.py --verbose
CMD ["sh", "collector_loop.sh"]

#-------------------------------------------------------------------------------
# emacs cruft follows
#-------------------------------------------------------------------------------
# Local Variables:
# mode: sh
# End:
