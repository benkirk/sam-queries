"""lock.py FILE WAIT CMD [ARGS]: take a POSIX (fcntl) lock on FILE, then exec CMD.

flock(2) is node-local on GPFS (/glade/u): a lock taken on one login node does not exclude
another. fcntl locks are cluster-wide. The lock survives the exec (same process, fd kept
open), so CMD holds it until it exits. WAIT is seconds to keep trying (0 = one attempt);
exits 75 if the lock is still held then. CMD must not open and close FILE itself, because
closing any fd on it releases the lock.
"""
import fcntl
import os
import sys
import time

path, wait, cmd = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
deadline = time.monotonic() + wait
while True:
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except OSError:
        left = deadline - time.monotonic()
        if left <= 0:
            sys.exit(75)
        time.sleep(min(5.0, left))
os.set_inheritable(fd, True)
os.execvp(cmd[0], cmd)
