#!/usr/bin/env python
"""Generate and post SYNTHETIC XRAS action payloads for a local smoke run.

This is not the corpus. ``tests/fixtures/xras/actions/`` holds eight scrubbed
**real** payloads and is the contract's evidence; these are made up, so that a
developer can drive the operator surface end to end with themselves as project
lead and watch the mail arrive.

Why it exists rather than a handful of committed JSON files: the useful
payloads name a projcode that does not exist until the ``New`` before them has
been processed. A generator can be told "supplement UHSS0007" the moment SAM
mints it; a static file cannot.

WARNING: Every one of these actions WRITES to the database it is posted at — a New
mints a projcode, allocates a Unix GID and creates real allocations. Point it
only at a local stack.

Typical run (see docs/xras/incoming/implemented/XRAS_PRE_DEPLOY_SMOKE.md for the full checklist)::

    source etc/config_env.sh
    python scripts/xras/smoke_payloads.py --new --contract AGS-2524858 --post
    python scripts/xras/smoke_payloads.py --new --post          # no contract
    python scripts/xras/smoke_payloads.py --supplement UHSS0003 --post
    python scripts/xras/smoke_payloads.py --extension UHSS0004 --post
    python scripts/xras/smoke_payloads.py --renewal UHSS0004 --post
    python scripts/xras/smoke_payloads.py --adjustment UHSS0003 --amount -100000 --post

Without ``--post`` the payload is written to stdout, which is the way to check
what you are about to send.

WARNING: **The lead's mnemonic decides whether a New can succeed at all.**
``resolve_mnemonic_code`` takes the *lab* route whenever ``opportunityName``
starts with ``'NCAR '``, and most NCAR labs have no mnemonic soft link — so the
default opportunity here is a University one, which routes through the lead's
own organization instead. For ``benkirk`` that resolves to ``HSS`` and mints
``UHSS####``. Change the opportunity and a New may start failing with
"Could not determine Mnemonic code".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

try:
    import requests
except ImportError:                                   # pragma: no cover
    sys.exit('requests is required — run `source etc/config_env.sh` first')


#: Resource repository keys, from `xras_resource_repository_key_resource`.
#: Verified present in the dev snapshot; `sam-admin xras --validate-mapping`
#: audits the table if one of these ever stops resolving.
DERECHO = 145575
DERECHO_GPU = 145576
CASPER = 144650
DATA_ACCESS = 145145

#: The (opportunity, allocation type, panel) triple copied from
#: `new_ncar4253_ok.json`, which is a real payload that resolves an exact
#: (panel, type) pair. Do not improvise one — the corpus reaches only 5 of the 11
#: allocation-type strategies and the pair a given triple lands on is not guessable.
#: (Measured again at 41 payloads: still 5. The chain agrees with production on all
#: 30 corpus projects that exist in the snapshot, so copying a real triple is the
#: reliable way to get a resolvable one.)
OPPORTUNITY = 'Small Allocation (University)'
ALLOCATION_TYPE = 'Small'
PANEL = {'type': 'Technical', 'name': 'CISL Resource Support',
         'abbr': 'CISL RSD', 'isPrimary': True}
FOS = [{'fosTypeId': 500032, 'fosNum': '30', 'fosName': 'Regional Climate',
        'fosAbbr': 'Regional Climate', 'isPrimary': True}]

#: Action ids start high so a smoke row is never mistaken for a real one in
#: `xras_action_log`, and so repeated runs do not collide with each other.
ACTION_ID_BASE = 990000


def _resources(spec):
    return [{
        'actionResourceId': ACTION_ID_BASE + 100 + i,
        'resourceRepositoryKey': key,
        'awardedAmount': f'{amount:.1f}',
        'comments': None,
        'resourceQA': [],
    } for i, (key, amount) in enumerate(spec)]


def _roles(lead, first, last, email):
    return [{
        'requestPeopleRoleId': ACTION_ID_BASE + 200,
        'roleType': 'PI',
        'username': lead,
        'beginDate': date.today().isoformat(),
        'endDate': None,
        'isAccountToBeCreated': False,
        'person': {
            'firstName': first, 'middleName': None, 'lastName': last,
            'email': email, 'phone': None,
            'organization': 'National Center for Atmospheric Research',
            'academicStatus': 'Staff', 'isReconciled': True,
        },
    }]


def _grants(contract_number):
    """A grant block naming an EXISTING contract, or none.

    WARNING: The ``New`` handler **links** a contract; it does not create one. A
    grant number SAM has never seen is rejected 422 with
    ``Cannot find contract for grant number ...`` — which is a real cutover
    expectation, not a quirk of this script. Pick a number that is already in
    `contract`::

        SELECT contract_number FROM contract ORDER BY contract_id DESC LIMIT 5;
    """
    if not contract_number:
        return []
    return [{
        'fundingAgency': 'National Science Foundation',
        'grantNumber': contract_number,
        'programOfficerName': 'Smoke Officer',
        'programOfficerEmail': 'officer@example.invalid',
        'piName': 'Smoke Lead',
        'title': f'Smoke test grant linkage ({contract_number})',
        'beginDate': (date.today() - timedelta(days=300)).isoformat(),
        'endDate': (date.today() + timedelta(days=900)).isoformat(),
        'awardedAmount': '1000000.0',
        'awardedUnits': None, 'percentageAward': None, 'subAwardNumber': None,
        'primaryFos': {'fosTypeId': None, 'fosNum': None, 'fosName': None,
                       'fosAbbr': None},
        'isPending': False,
    }]


def build(args):
    """One payload, shaped by whichever action flag was given."""
    today = date.today()
    end = today + timedelta(days=args.days)

    if args.new:
        action_type, request_number = 'New', args.request_number
        title = ('Smoke Test - New Project With Contract' if args.contract
                 else 'Smoke Test - New Project Without Contract')
        resources = _resources([(DERECHO, 1_000_000.0), (DERECHO_GPU, 2_500.0),
                                (CASPER, 10_000.0), (DATA_ACCESS, 1.0)])
        grants = _grants(args.contract)
    elif args.supplement:
        # WARNING: On a Supplement the amount is the INCREMENT, not the new total.
        action_type, request_number = 'Supplement', args.supplement
        title = f'Smoke Test - Supplement to {args.supplement}'
        resources = _resources([(DERECHO, 250_000.0), (CASPER, 2_500.0)])
        grants = []
    elif args.extension:
        # The Extension handler ignores `resources` entirely and reads only
        # `actionEndDate` — an empty list here is faithful, not lazy.
        action_type, request_number = 'Extension', args.extension
        title = f'Smoke Test - Extension of {args.extension}'
        resources = []
        grants = []
    elif args.renewal:
        # `Renewal` against an EXISTING project routes to the `update` service,
        # which is the `xras_update` notification kind.
        action_type, request_number = 'Renewal', args.renewal
        title = f'Smoke Test - Renewal of {args.renewal}'
        resources = _resources([(DERECHO, 750_000.0), (CASPER, 7_500.0)])
        grants = []
    elif args.adjustment:
        # WARNING: The ONLY action type whose amounts may be negative, and the only
        # reason `xras_adjustment` needs its own wording — see
        # `sam/xras/handlers/adjustment.py`, which exists to honor the sign
        # that legacy's copy-pasted `> 0` gate silently dropped.
        #
        # `--amount` is signed and applies to Derecho; Casper takes a tenth of
        # it, so a single run exercises two magnitudes in the same direction.
        # A reduction below zero is REJECTED by the handler (422), which is
        # itself worth smoking.
        action_type, request_number = 'Adjustment', args.adjustment
        direction = 'Reduction' if args.amount < 0 else 'Increase'
        title = f'Smoke Test - {direction} adjustment to {args.adjustment}'
        resources = _resources([(DERECHO, args.amount),
                                (CASPER, args.amount / 10.0)])
        grants = []
    else:                                             # pragma: no cover
        raise SystemExit('pick one of --new / --supplement / --extension / --renewal')

    return {
        'actionId': args.action_id,
        'actionType': action_type,
        'actionBeginDate': today.isoformat(),
        'actionEndDate': end.isoformat(),
        'requestId': args.action_id + 1_000_000,
        'requestNumber': request_number,
        'requestType': 'New',
        'requestAbstract': (
            'SYNTHETIC smoke-test payload generated by '
            'scripts/xras/smoke_payloads.py. Not a real allocation request.'),
        'requestTitle': title,
        'requestShortTitle': None,
        'requestGrantType': None,
        'opportunityId': 532220,
        'opportunityName': OPPORTUNITY,
        'opportunityType': 'Continuous',
        'allocationType': ALLOCATION_TYPE,
        'awardDate': None,
        'awardPeriod': 12,
        'resources': resources,
        'roles': _roles(args.lead, args.first_name, args.last_name, args.email),
        'fos': FOS,
        'panels': [PANEL],
        'grants': grants,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument('--new', action='store_true',
                      help='a New request (mints a projcode)')
    what.add_argument('--supplement', metavar='PROJCODE')
    what.add_argument('--extension', metavar='PROJCODE')
    what.add_argument('--renewal', metavar='PROJCODE',
                      help='a Renewal against an existing project → the '
                           '`update` service and the xras_update kind')
    what.add_argument('--adjustment', metavar='PROJCODE',
                      help='a signed Adjustment (see --amount); the only '
                           'action type that can REDUCE an allocation')

    parser.add_argument('--amount', type=float, default=-100_000.0,
                        help='signed Derecho delta for --adjustment; Casper '
                             'gets a tenth (default: -100000, a reduction)')

    parser.add_argument('--lead', default='benkirk',
                        help='PI username; must exist in SAM (default: benkirk)')
    parser.add_argument('--first-name', default='Benjamin')
    parser.add_argument('--last-name', default='Kirk')
    parser.add_argument('--email', default='benkirk@ucar.edu')
    parser.add_argument('--contract', default=None, metavar='NUMBER',
                        help='link an EXISTING contract_number (--new only)')
    parser.add_argument('--request-number', default=None, metavar='TOKEN',
                        help='request token for --new (default: NCAR<actionId>)')
    parser.add_argument('--action-id', type=int, default=None,
                        help=f'wire actionId (default: {ACTION_ID_BASE} + '
                             'seconds since midnight)')
    parser.add_argument('--days', type=int, default=387,
                        help='action end date, days from today (default: 387)')
    parser.add_argument('--post', action='store_true',
                        help='POST it; otherwise print it')
    parser.add_argument('--base-url', default='http://localhost:5050')
    args = parser.parse_args(argv)

    if args.action_id is None:
        # Seconds-since-midnight keeps ids unique across a session's runs
        # without needing to read the table back.
        from datetime import datetime
        now = datetime.now()
        args.action_id = ACTION_ID_BASE + (
            now.hour * 3600 + now.minute * 60 + now.second)
    if args.new and not args.request_number:
        args.request_number = f'NCAR{args.action_id}'

    body = json.dumps(build(args), indent=2)

    if not args.post:
        print(body)
        return 0

    username = os.environ.get('SAM_XRAS_USER')
    password = os.environ.get('SAM_XRAS_PASS')
    if not username or not password:
        sys.exit('SAM_XRAS_USER / SAM_XRAS_PASS are unset — '
                 'run `source etc/config_env.sh` first')

    url = f'{args.base_url.rstrip("/")}/api/xras/v1/actions'
    resp = requests.post(
        url, data=body.encode(),
        headers={'Content-Type': 'application/json',
                 'XA-REQUESTER': username, 'XA-API-KEY': password},
        timeout=30)
    print(f'{resp.status_code}  {resp.text}')
    # A 422 is a legitimate outcome worth seeing (an unknown contract, a lead
    # with no mnemonic), so only a transport-level problem is a failure here.
    return 0 if resp.status_code in (200, 400, 422) else 1


if __name__ == '__main__':
    raise SystemExit(main())
