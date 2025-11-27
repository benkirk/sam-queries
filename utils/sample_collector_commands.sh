#!/usr/bin/env bash

set -e

#-----------------------------------
# Derecho sample collection commands

# summary listing of queues
ssh derecho "qstat -Qa"

# full listing of all compute nodes and status
ssh derecho "pbsnodes -aj -F json"

# full details of running and queued jobs
ssh derecho "qstat -f -F json"

# filesystem statuses
ssh derecho "BLOCKSIZE=TiB df /glade/{u/home,work,campaign,derecho/scratch}"

# login node statuses
for login in 1 2 3 4 5 6 7 8; do
    ssh derecho "hostname && ssh derecho${login} uptime"
done

# reservation information:
ssh derecho "pbs_rstat -f"

#----------------------------------
# Casper sample collection commands

# summary listing of queues
ssh casper "qstat -Qa"

# full listing of all compute nodes and status
ssh casper "pbsnodes -aj -F json"

# full details of running and queued jobs
ssh casper "qstat -f -F json"

# filesystem statuses
ssh casper "BLOCKSIZE=TiB df /glade/{u/home,work,campaign,derecho/scratch}"

for login in 1 2; do
    ssh casper "hostname && ssh casper-login${login} uptime"
done

# reservation information:
ssh casper "pbs_rstat -f"
