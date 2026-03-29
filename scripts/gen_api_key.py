#!/usr/bin/env python3
"""
Generate a new API key and its bcrypt hash.

Run this script when creating or rotating API credentials for the SAM status
collectors (or any other machine-to-machine API client).

Usage:
    python tools/gen_api_key.py
    python tools/gen_api_key.py --username collector
    python tools/gen_api_key.py --username myservice --rounds 14

Output:
    API Key  → set as STATUS_API_KEY in the collector's .env
    Hash     → add to API_KEYS dict in src/webapp/config.py

Example config.py entry:
    API_KEYS = {
        'collector': '$2b$12$abc123...the_hash_printed_below...',
    }
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
    args = parser.parse_args()

    key = secrets.token_urlsafe(32)
    hashed = bcrypt.hashpw(key.encode(), bcrypt.gensalt(rounds=args.rounds))

    print(f"\nGenerated API key for '{args.username}':")
    print(f"  API Key  (→ STATUS_API_KEY in .env):               {key}")
    print(f"  Hash     (→ API_KEYS['{args.username}'] in config.py):  {hashed.decode()}")
    print()


if __name__ == '__main__':
    main()
