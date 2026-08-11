#!/usr/bin/env python3
"""Seed a local ``xras_action_log`` with rows to develop and demo against.

Why this exists
---------------
The XRAS operator page has nothing to render until the table has rows, and the table
is empty on every fresh stack for two compounding reasons:

1. **Capture mode only records posts arriving at *our* endpoint**, and XRAS still
   posts to legacy. Until cutover step 4 nothing arrives on its own.
2. **``docker compose --profile test down -v`` wipes the volume**, and the schema
   comes from a post-restore init script, so the table is recreated empty every
   time the DDL is amended — which this sprint does deliberately and repeatedly.

So seeding is not a one-off: it is a step you re-run after every rebuild. Doing it by
hand is a curl loop plus a credential-provisioning step that is easy to forget, which
is what this script packages.

The credential step is the non-obvious half
-------------------------------------------
``POST /api/xras/v1/actions`` authenticates against ``api_credentials`` rows carrying
``ROLE_XRAS``, and **the obfuscated snapshot ships that table empty** — credentials
are scrubbed, correctly. Config-based ``API_KEYS_*`` cannot substitute: a
config-sourced key resolves to ``roles=[]`` (``webapp/utils/api_auth.py``) and
``xras_api_required`` demands ``ROLE_XRAS``, so it would authenticate and then 403.

This script therefore provisions a DB credential matching ``$SAM_XRAS_USER`` /
``$SAM_XRAS_PASS`` before posting. It is idempotent, and it refuses to run against
anything but a local database.

What it produces
----------------
Four ``received`` rows from the committed real payloads, plus — with ``--errors`` —
one 400 (malformed body) and one 422 (schema rejection), because those are the states
an operator actually triages and they are trivial to produce. ``manual`` needs
``XRAS_ACTIONS_CAPTURE_ONLY=0``; ``processed`` needs a handler and is not producible
until Phase 3.

Usage
-----
::

    source etc/config_env.sh
    docker compose up webdev --watch          # in another terminal
    python scripts/xras/seed_dev_actions.py --errors

    # against the prod-like stack instead of webdev
    python scripts/xras/seed_dev_actions.py --base-url http://localhost:7050
"""

import argparse
import json
import os
import pathlib
import sys

FIXTURE_DIR = (pathlib.Path(__file__).resolve().parents[2]
               / 'tests' / 'fixtures' / 'xras' / 'actions')

#: Only ever provision credentials against a local dev database. The whole point of
#: this script is to create a working API key; pointing it at a shared host would be
#: creating one there.
_LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', 'mysql', 'samuel-mysql'}


def _error_bodies():
    """Bodies that exercise the two error paths.

    Both are deliberately minimal — the interesting thing is the status code and
    the audit row, not the payload. The request number comes from the site's token
    family rather than a bare literal, so a re-user's seeded rows look like their
    own traffic.
    """
    from sam.queries.xras_actions import XRAS_REQUEST_TOKEN_EXAMPLE

    return [
        ('malformed JSON -> 400', '{"actionType": '),
        # A bool in a String-declared field is the one thing XrasActionSchema
        # rejects; everything else about the wire format is tolerated by design.
        ('schema rejection -> 422',
         json.dumps({'actionType': 'New',
                     'requestNumber': XRAS_REQUEST_TOKEN_EXAMPLE,
                     'awardPeriod': True})),
    ]


def _db_url(args):
    """Build the SQLAlchemy URL for the local dev database."""
    return (f'mysql+pymysql://{args.db_user}:{args.db_password}'
            f'@{args.db_host}:{args.db_port}/{args.db_name}')


def ensure_credentials(args, username, password):
    """Idempotently provision an enabled ``api_credentials`` row with ``ROLE_XRAS``.

    Returns a short status string for logging. Safe to re-run: an existing row has
    its hash refreshed (so a rotated ``$SAM_XRAS_PASS`` keeps working) and a missing
    role link is added.
    """
    if args.db_host not in _LOCAL_HOSTS:
        sys.exit(f'refusing to provision credentials on non-local host '
                 f'{args.db_host!r} — this script is for local dev only')

    import bcrypt
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sam.security.roles import ApiCredentials, Role, RoleApiCredentials

    engine = create_engine(_db_url(args))
    with Session(engine) as session:
        role = session.query(Role).filter(Role.name == 'ROLE_XRAS').one_or_none()
        if role is None:
            sys.exit("no ROLE_XRAS row in `role` — the snapshot should carry it; "
                     "is this the right database?")

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cred = (session.query(ApiCredentials)
                .filter(ApiCredentials.username == username).one_or_none())
        if cred is None:
            cred = ApiCredentials(username=username, password=hashed, enabled=True)
            session.add(cred)
            session.flush()
            action = 'created'
        else:
            cred.password = hashed
            cred.enabled = True
            action = 'refreshed'

        linked = (session.query(RoleApiCredentials)
                  .filter(RoleApiCredentials.role_id == role.role_id,
                          RoleApiCredentials.api_credentials_id
                          == cred.api_credentials_id)
                  .one_or_none())
        if linked is None:
            session.add(RoleApiCredentials(role_id=role.role_id,
                                           api_credentials_id=cred.api_credentials_id))
            action += ' + ROLE_XRAS linked'

        session.commit()
    return action


def make_pending_demo(args):
    """Deactivate one XRAS-touched project so the pending-activation card has a row.

    **Local dev only, and opt-in.** Both projcodes the real Extension payloads name
    (``UCUB0166``, ``UFSU0023``) are ``active = 1`` in the obfuscated snapshot, so the
    card is correctly empty after a plain seed — an XRAS action that touched an
    *active* project has nothing pending about it.

    That is right, and it also means nobody can see the card render. This flips the
    oldest such project to ``active = 0``, which is exactly the state a real
    XRAS-created project arrives in. It mutates the dev database; the snapshot is
    disposable (``down -v`` restores it), but do not point this at anything shared —
    hence the same host guard as the credential step.
    """
    if args.db_host not in _LOCAL_HOSTS:
        sys.exit(f'refusing to deactivate projects on non-local host {args.db_host!r}')

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from sam.projects.projects import Project

    engine = create_engine(_db_url(args))
    with Session(engine) as session:
        touched = {r for (r,) in session.query(XrasActionLog.request_number)
                   .filter(XrasActionLog.request_number.isnot(None)).all()}
        touched |= {r for (r,) in session.query(XrasActionLog.projcode_result)
                    .filter(XrasActionLog.projcode_result.isnot(None)).all()}
        project = (session.query(Project)
                   .filter(Project.projcode.in_(touched))
                   .filter(Project.is_active)
                   .order_by(Project.projcode)
                   .first())
        if project is None:
            print('  no active XRAS-touched project to deactivate — skipping')
            return
        project.active = False
        session.commit()
        print(f'  deactivated {project.projcode} — it will now appear as pending')


def post(session, url, body, username, password, label):
    """POST one body through the real endpoint and report the status code."""
    resp = session.post(
        url,
        data=body.encode(),
        headers={'Content-Type': 'application/json',
                 'XA-REQUESTER': username,
                 'XA-API-KEY': password},
        timeout=30,
    )
    print(f'  {resp.status_code}  {label}')
    return resp.status_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--base-url', default='http://localhost:5050',
                        help='webapp base URL (default: the webdev container)')
    parser.add_argument('--dir', dest='payload_dir', default=None, metavar='PATH',
                        help='Post every *.json under PATH instead of the committed '
                             'fixtures. For the raw-payload loop: point it at a '
                             'directory of unscrubbed production bodies '
                             '(e.g. ~/xras_payloads_raw). Those files must never '
                             'enter tests/ or any commit — they are real people, '
                             'real awards, real organizations.')
    parser.add_argument('--errors', action='store_true',
                        help='also post a malformed body (400) and a rejected one (422)')
    parser.add_argument('--skip-credentials', action='store_true',
                        help='assume api_credentials is already provisioned')
    parser.add_argument('--pending-demo', action='store_true',
                        help='deactivate one XRAS-touched project so the '
                             'pending-activation card has a row (mutates the dev DB)')
    parser.add_argument('--db-host', default='127.0.0.1')
    parser.add_argument('--db-port', default='3306')
    parser.add_argument('--db-name', default='sam')
    parser.add_argument('--db-user', default='root')
    parser.add_argument('--db-password', default='root')
    args = parser.parse_args(argv)

    username = os.getenv('SAM_XRAS_USER')
    password = os.getenv('SAM_XRAS_PASS')
    if not username or not password:
        sys.exit('SAM_XRAS_USER / SAM_XRAS_PASS are not set — '
                 'run `source etc/config_env.sh` first')

    if not args.skip_credentials:
        print(f'provisioning api_credentials for {username!r} ...')
        print(f'  {ensure_credentials(args, username, password)}')

    source = pathlib.Path(args.payload_dir).expanduser() if args.payload_dir else FIXTURE_DIR
    payloads = sorted(source.glob('*.json'))
    if not payloads:
        sys.exit(f'no payloads found under {source}')
    if args.payload_dir:
        # Loud on purpose. The committed fixtures are scrubbed; an arbitrary
        # directory is not, and the difference decides whether anything derived
        # from this run may be committed.
        print(f'⚠️  posting UNSCRUBBED payloads from {source} — '
              f'nothing derived from this run may be committed')

    import requests
    http = requests.Session()
    url = args.base_url.rstrip('/') + '/api/xras/v1/actions'

    print(f'posting {len(payloads)} real payloads to {url} ...')
    codes = [post(http, url, p.read_text(), username, password, p.name)
             for p in payloads]

    if args.errors:
        print('posting error-path bodies ...')
        codes += [post(http, url, body, username, password, label)
                  for label, body in _error_bodies()]

    unexpected = [c for c in codes if c not in (200, 400, 422)]
    if unexpected:
        sys.exit(f'\nunexpected status code(s) {unexpected} — '
                 f'401 means the credential step did not take effect '
                 f'(API_KEYS_DB_TTL caches for 60s by default; retry shortly)')

    if args.pending_demo:
        print('setting up the pending-activation demo ...')
        make_pending_demo(args)

    print('\ndone. Rows are visible at Allocations > XRAS, and via:\n'
          '  sam-admin xras --summary')
    return 0


if __name__ == '__main__':
    sys.exit(main())
