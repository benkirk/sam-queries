#!/usr/bin/env python3
"""Probe the live XRAS Allocations API — opt-in, read-only, never in CI.

Why a script and not a test
---------------------------
Everything this exercises needs a real API key against a real production
service. ``tests/unit/test_xras_api_client.py`` covers the client's transport
and parsing with canned payloads; this covers the thing a fixture cannot:
that the endpoints, headers and field names we built against are still what
XRAS actually serves.

**It skips with exit 0 when ``XRAS_API_KEY`` is absent**, mirroring
``utils/parity/check_legacy_apis.py::_resolve_xras_credentials`` — an
unconfigured checkout is the normal state, not a failure.

Usage
-----
::

    source etc/config_env.sh
    XRAS_OUTGOING_ENABLED=1 XRAS_API_KEY=… python scripts/xras/probe_outgoing.py

Exit codes: 0 success (or skipped), 2 the API could not be reached or
answered something we did not expect.

WARNING: Output names real people. Do not paste it into a commit, a fixture, a
docstring, or a PR description. The counts and the reconciliation verdict are
safe to report; the identities are not — which is why ``--roster`` is off by
default and why nothing here prints an email address.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sam.integration.xras_api import (  # noqa: E402
    XrasApiClient,
    XrasApiConfig,
    XrasSourceUnavailable,
)

EXIT_SUCCESS = 0
EXIT_ERROR = 2


def _rule(title: str) -> None:
    print(f'\n── {title} ' + '─' * max(0, 68 - len(title)))


def probe_resources(client: XrasApiClient) -> list:
    """The resource catalog, and the join key we depend on."""
    _rule('GET /v1/resources')
    resources = client.get_resources() or []
    print(f'{len(resources)} resources')
    keyed = [r for r in resources if r.get('resourceRepositoryKey') is not None]
    print(f'{len(keyed)} carry resourceRepositoryKey')
    for r in sorted(resources, key=lambda x: str(x.get('resourceName', ''))):
        ended = r.get('productionEndDate') or ''
        flag = '  ⚠️ production ended' if ended else ''
        print(f"  {str(r.get('resourceRepositoryKey')):>6}  "
              f"{r.get('resourceName')}{flag} {ended}")
    return resources


def reconcile_keys(resources: list) -> None:
    """Two-sided check against ``xras_resource_repository_key_resource``.

    Skipped silently when no SAM database is reachable — the point of the
    script is the API, and a laptop without a DB should still be able to run
    the other probes.
    """
    _rule('reconcile resourceRepositoryKey against SAM')
    try:
        from sqlalchemy.orm import Session

        from sam.integration.xras import XrasResourceRepositoryKeyResource
        from sam.session import create_sam_engine

        engine, _ = create_sam_engine()
        with Session(engine) as session:
            sam_keys = {row.resource_repository_key for row in
                        session.query(XrasResourceRepositoryKeyResource).all()}
    except Exception as exc:                     # noqa: BLE001 - diagnostic only
        print(f'skipped (no SAM database: {exc})')
        return

    live = {int(r['resourceRepositoryKey']) for r in resources
            if r.get('resourceRepositoryKey') is not None}
    print(f'SAM mapping rows: {len(sam_keys)}   live XRAS keys: {len(live)}')
    print(f'  XRAS keys SAM cannot resolve: {sorted(live - sam_keys) or "none"}')
    print(f'  SAM keys XRAS does not send:  {sorted(sam_keys - live) or "none"}')


def probe_reports(client: XrasApiClient, limit: int) -> None:
    """One page of the enumeration, and whether person detail is inline."""
    _rule(f'GET /v1/reports/requests?status=Approved&limit={limit}')
    page = client.get_requests_page(status='Approved', limit=limit)
    print(f'{len(page)} requests on page 1')
    if not page:
        return
    first = page[0]
    print(f"  top-level keys: {sorted(first)}")
    roles = first.get('roles') or []
    inline = sum(1 for r in roles if isinstance(r.get('person'), dict))
    print(f'  first request: {len(roles)} role entries, '
          f'{inline} with an inline person object')
    person = next((r['person'] for r in roles
                   if isinstance(r.get('person'), dict)), None)
    if person:
        # Field NAMES only — the values name a real researcher.
        print(f'  person fields: {sorted(person)}')
        print(f"  isReconciled present: {'isReconciled' in person}   "
              f"residenceCountry present: {'residenceCountry' in person}")
    numbers = [r.get('requestNumber') for r in page]
    print(f'  requestNumber non-null on {sum(1 for n in numbers if n)}/{len(page)}')


def probe_person(client: XrasApiClient, username: str) -> None:
    _rule(f'GET /v1/people/{username}')
    person = client.get_person(username)
    if person is None:
        print('404 — no such username (a clean not-found, not an error)')
        return
    print(f'  fields: {sorted(person)}')
    print(f"  isReconciled: {person.get('isReconciled')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=5,
                        help='requests to pull on the enumeration page')
    parser.add_argument('--person', help='probe one username through /v1/people')
    args = parser.parse_args()

    if not os.environ.get('XRAS_API_KEY'):
        print('XRAS_API_KEY is not set — skipping the live probe.')
        print('To run it: XRAS_OUTGOING_ENABLED=1 XRAS_API_KEY=… '
              'python scripts/xras/probe_outgoing.py')
        return EXIT_SUCCESS

    # The lever exists to keep the webapp and the task quiet; asking for this
    # script by name is opt-in enough, so force it on rather than making the
    # operator set two variables.
    config = XrasApiConfig.from_environment()
    if not config.enabled:
        config = replace(config, enabled=True)

    print(f'base_url={config.base_url}  process={config.allocations_process}  '
          f'user={config.api_user}  context=report')

    client = XrasApiClient.from_environment(config)
    try:
        resources = probe_resources(client)
        reconcile_keys(resources)
        probe_reports(client, args.limit)
        if args.person:
            probe_person(client, args.person)
    except XrasSourceUnavailable as exc:
        print(f'\nXRAS unavailable: {exc}', file=sys.stderr)
        return EXIT_ERROR

    print('\nProbe complete.')
    return EXIT_SUCCESS


if __name__ == '__main__':
    sys.exit(main())
