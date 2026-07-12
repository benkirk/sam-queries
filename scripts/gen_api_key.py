#!/usr/bin/env python3
"""
Generate a new API key and its bcrypt hash.

Run this script when creating or rotating API credentials for the SAM status
collectors (or any other machine-to-machine API client).

Usage:
    python tools/gen_api_key.py
    python tools/gen_api_key.py --username collector
    python tools/gen_api_key.py --username myservice --rounds 14
    python tools/gen_api_key.py --key <existing-secret>   # hash a specific key

Output:
    API Key  → set as STATUS_API_KEY in the collector's .env
    Hash     → set as API_KEYS_<USERNAME> env var in the webapp environment
               (compose.yaml, Helm values.yaml, or SSM Parameter Store)
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
    args = parser.parse_args()

    key = args.key if args.key is not None else secrets.token_urlsafe(32)
    if not key:
        sys.exit("--key must be a non-empty string")
    hashed = bcrypt.hashpw(key.encode(), bcrypt.gensalt(rounds=args.rounds))

    env_var = f"API_KEYS_{args.username.upper()}"

    verb = "Using supplied" if args.key is not None else "Generated"
    print(f"\n{verb} API key for '{args.username}':")
    print(f"  Consumer's env  →  MY_API_USER={args.username}; MY_API_KEY={key}")
    print(f"  Webapp env var  →  {env_var}={hashed.decode()}")
    print()


if __name__ == '__main__':
    main()
