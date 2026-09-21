#!/usr/bin/env python3
"""Refresh the vendored NWSC end-user agreement from NCAR/HPC-Docs.

The agreement shown on the registration gate is vendored verbatim
(src/webapp/register/eula.md) so an acceptance records exactly what the visitor
saw. This pulls the current upstream markdown; run it deliberately and review
the diff in a PR (the legal text is what changed), the same discipline as the
vendored front-end assets. Nothing here runs at build or request time.

    python scripts/update_eula.py [--ref main]

Prints the blob SHA to record in the PR. Relative `*.md` links are left as-is;
webapp/register/eula.py maps them onto the published site at render time.
"""

import argparse
import base64
import sys
from pathlib import Path

import requests

REPO = 'NCAR/HPC-Docs'
SOURCE_PATH = 'docs/getting-started/end-user-agreement.md'
DEST = Path(__file__).resolve().parents[1] / 'src/webapp/register/eula.md'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='main', help='branch, tag or commit (default: main)')
    args = parser.parse_args()

    url = f'https://api.github.com/repos/{REPO}/contents/{SOURCE_PATH}'
    resp = requests.get(url, params={'ref': args.ref},
                        headers={'Accept': 'application/vnd.github+json'}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    content = base64.b64decode(payload['content'])

    DEST.write_bytes(content)
    print(f'wrote {DEST.relative_to(Path.cwd())} ({len(content)} bytes)')
    print(f'source: {REPO}/{SOURCE_PATH}@{args.ref}  blob {payload["sha"]}')
    print('review the diff before committing (this is the accepted legal text).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
