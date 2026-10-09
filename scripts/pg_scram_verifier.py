#!/usr/bin/env python3
"""Print a Postgres SCRAM-SHA-256 verifier for a password, so CREATE ROLE never sees the plaintext.

    scripts/pg_scram_verifier.py              # prompts twice; prints the verifier only
    scripts/pg_scram_verifier.py --generate   # 64-hex password to stderr, verifier to stdout

Stdlib only. Same construction as libpq's PQencryptPasswordConn (RFC 7677, 4096 iterations).
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import secrets
import sys

ITERATIONS = 4096


def scram_verifier(password: str, salt: bytes = None, iterations: int = ITERATIONS) -> str:
    """``SCRAM-SHA-256$<iter>:<salt>$<StoredKey>:<ServerKey>``, as pg_authid stores it."""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    client_key = hmac.new(salted, b'Client Key', 'sha256').digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b'Server Key', 'sha256').digest()
    b64 = lambda b: base64.b64encode(b).decode('ascii')
    return f'SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--generate', action='store_true',
                        help='generate a 64-hex password and print it to stderr')
    args = parser.parse_args()
    if args.generate:
        password = secrets.token_hex(32)
        print(f'password (store it in OpenBao now): {password}', file=sys.stderr)
    else:
        password = getpass.getpass('password: ')
        if password != getpass.getpass('again: '):
            print('passwords differ', file=sys.stderr)
            return 2
    print(scram_verifier(password))
    return 0


if __name__ == '__main__':
    sys.exit(main())
