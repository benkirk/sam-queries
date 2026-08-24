#!/usr/bin/env python3
"""
Generate a new API key and its bcrypt hash.

Run this script when creating or rotating API credentials for the SAM status
collectors (or any other machine-to-machine API client).

Usage:
    python scripts/gen_api_key.py
    python scripts/gen_api_key.py --username collector
    python scripts/gen_api_key.py --username myservice --rounds 14
    python scripts/gen_api_key.py --key <existing-secret>  # hash a specific key
    python scripts/gen_api_key.py --username samuel --prefix 2a --sql

Output:
    API Key  -> set as STATUS_API_KEY in the collector's .env
    Hash     -> set as API_KEYS_<USERNAME> env var in the webapp environment
               (compose.yaml, Helm values.yaml, or SSM Parameter Store)
    --sql    -> also emit the api_credentials / role_api_credentials INSERTs,
               for credentials stored in the database rather than the env

Env-var credentials (API_KEYS_<USERNAME>) take precedence over database rows;
see src/webapp/utils/api_auth.py. Legacy Java SAM only reads the database.
"""

import argparse
import secrets
import sys

try:
    import bcrypt
except ImportError:
    sys.exit("bcrypt is required: pip install bcrypt")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--username', default='collector',
        help='API key username (default: collector)',
    )
    parser.add_argument(
        '--rounds', type=int, default=12,
        help='bcrypt cost rounds, 10-14 recommended (default: 12)',
    )
    parser.add_argument(
        '--key', default=None,
        help='hash this specific key instead of generating a random one '
             '(use to re-derive the hash for an existing/rotated secret)',
    )
    parser.add_argument(
        '--prefix', choices=('2a', '2b'), default='2b',
        help="bcrypt variant marker (default: 2b, the library default). Use 2a "
             "for rows going into the api_credentials table, matching every "
             "credential legacy SAM already holds",
    )
    parser.add_argument(
        '--sql', action='store_true',
        help='also emit the api_credentials / role_api_credentials INSERTs',
    )
    parser.add_argument(
        '--role', default='ROLE_XRAS',
        help='role name granted by the --sql INSERT (default: ROLE_XRAS)',
    )
    args = parser.parse_args()

    # api_credentials.username is varchar(11); MySQL in STRICT mode rejects longer.
    if len(args.username) > 11:
        sys.exit(f"--username must be 11 characters or fewer "
                 f"(got {len(args.username)}: {args.username!r})")

    key = args.key if args.key is not None else secrets.token_urlsafe(32)
    if not key:
        sys.exit("--key must be a non-empty string")
    salt = bcrypt.gensalt(rounds=args.rounds, prefix=args.prefix.encode())
    hashed = bcrypt.hashpw(key.encode(), salt).decode()

    env_var = f"API_KEYS_{args.username.upper()}"

    verb = "Using supplied" if args.key is not None else "Generated"
    print(f"\n{verb} API key for '{args.username}':")
    print(f"  Consumer's env  →  MY_API_USER={args.username}; MY_API_KEY={key}")
    print(f"  Webapp env var  →  {env_var}={hashed}")

    if args.sql:
        # role_id and api_credentials_id are both resolved at runtime — never
        # hardcode PKs from a lookup table.
        print(f"\n  Database rows (legacy SAM reads these; the Python webapp "
              f"reads them too unless {env_var} is set):\n")
        print("    START TRANSACTION;")
        print("    INSERT INTO api_credentials (username, password, enabled)")
        print(f"    VALUES ('{args.username}', '{hashed}', 1);")
        print("    INSERT INTO role_api_credentials (role_id, api_credentials_id)")
        print("      SELECT r.role_id, ac.api_credentials_id")
        print("        FROM role r, api_credentials ac")
        print(f"       WHERE r.name = '{args.role}' AND ac.username = '{args.username}';")
        print("    -- verify one row, correct role, enabled — then COMMIT (else ROLLBACK)")
        print("    SELECT ac.api_credentials_id, ac.username, ac.enabled, r.name")
        print("      FROM api_credentials ac")
        print("      JOIN role_api_credentials rac USING (api_credentials_id)")
        print("      JOIN role r USING (role_id)")
        print(f"     WHERE ac.username = '{args.username}';")
        print("    COMMIT;")
    print()


if __name__ == '__main__':
    main()
