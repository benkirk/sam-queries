#!/usr/bin/env bash

set -e

# Derecho sample collection commands
ssh derecho "qstat -Qa"
ssh derecho "pbsnodes -aSj -F json"
ssh derecho "qstat -f -F json"
ssh derecho "BLOCKSIZE=TiB df /glade/{u/home,work,campaign,derecho/scratch}"

for login in 1 2 3 4 5 6 7 8; do
    ssh derecho "hostname && ssh derecho${login} uptime"
done

# Casper sample collection commands
ssh casper "qstat -Qa"
ssh casper "pbsnodes -aSj -F json"
ssh casper "qstat -f -F json"
ssh casper "BLOCKSIZE=TiB df /glade/{u/home,work,campaign,derecho/scratch}"

for login in 1 2; do
    ssh casper "hostname && ssh casper-login${login} uptime"
done
