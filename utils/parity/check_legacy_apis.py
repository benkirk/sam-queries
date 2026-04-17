#!/usr/bin/env python3
"""Compare deployed legacy SAM APIs against the new samuel.k8s deployment.

This is a standalone operations / development utility. It is NOT part of
the pytest suite. Both endpoints require the UCAR VPN.

Usage:
    # Required env vars (load from your .env or set explicitly):
    #   SAM_LEGACY_USER, SAM_LEGACY_PASS    — sam.ucar.edu Basic Auth
    #   SAM_NEW_API_USER, SAM_NEW_API_PASS  — samuel.k8s Basic Auth
    #     (falls back to SAM_LEGACY_* if absent)

    python utils/parity/check_legacy_apis.py
    python utils/parity/check_legacy_apis.py --api fstree --resource Derecho
    python utils/parity/check_legacy_apis.py --format json | jq .

Exit codes:
    0  — full parity (no failures)
    1  — at least one comparison failed
    2  — precondition error (missing env, unreachable host)
    130 — keyboard interrupt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Optional

# Make the project root importable so `utils.parity.*` resolves whether the
# script is invoked as `python utils/parity/check_legacy_apis.py` or via
# `python -m utils.parity.check_legacy_apis`. Mirrors the sys.path insert
# pattern used by utils/profiling/profile_user_dashboard.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', '..'))

import requests  # noqa: E402

from utils.parity.clients import LegacyClient, NewClient  # noqa: E402
from utils.parity.comparators import (  # noqa: E402
    CheckResult,
    collect_resource_names,
    compare_directory_access,
    compare_fstree_access,
    compare_project_access,
)


DEFAULT_LEGACY_BASE = 'https://sam.ucar.edu'
DEFAULT_NEW_BASE = 'https://samuel.k8s.ucar.edu'
PROJECT_BRANCHES = ('hpc', 'hpc-data', 'hpc-dev')


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------

def _resolve_credentials() -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Return (legacy_auth, new_auth) or None if env vars are missing.

    Prints a clear error to stderr listing missing variables.
    """
    missing: list[str] = []

    legacy_user = os.environ.get('SAM_LEGACY_USER', '')
    legacy_pass = os.environ.get('SAM_LEGACY_PASS', '')
    if not legacy_user:
        missing.append('SAM_LEGACY_USER')
    if not legacy_pass:
        missing.append('SAM_LEGACY_PASS')

    new_user = os.environ.get('SAM_NEW_API_USER', legacy_user)
    new_pass = os.environ.get('SAM_NEW_API_PASS', legacy_pass)
    if not new_user:
        missing.append('SAM_NEW_API_USER (or SAM_LEGACY_USER fallback)')
    if not new_pass:
        missing.append('SAM_NEW_API_PASS (or SAM_LEGACY_PASS fallback)')

    if missing:
        print('ERROR: missing required environment variables:', file=sys.stderr)
        for m in missing:
            print(f'  - {m}', file=sys.stderr)
        print('\nLoad them from your .env file, e.g.:', file=sys.stderr)
        print('  source etc/config_env.sh', file=sys.stderr)
        return None

    return (legacy_user, legacy_pass), (new_user, new_pass)


def _probe(label: str, url: str, timeout: int = 5) -> bool:
    """Confirm the host is reachable. Prints to stderr on failure."""
    try:
        # GET / with a short timeout — a non-200 response is still "reachable".
        requests.get(url, timeout=timeout)
        return True
    except requests.exceptions.RequestException as exc:
        print(f'ERROR: {label} unreachable ({url}): {exc}', file=sys.stderr)
        print('  Are you on the UCAR VPN?', file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_directory(legacy: LegacyClient, new: NewClient, verbose: int) -> tuple[dict, dict]:
    if verbose:
        print('  fetching legacy directoryaccess ...', file=sys.stderr)
    legacy_data = legacy.directory_access()
    if verbose:
        print('  fetching new directory_access ...', file=sys.stderr)
    new_data = new.directory_access()
    return legacy_data, new_data


def _fetch_project(
    legacy: LegacyClient, new: NewClient, branches: tuple[str, ...], verbose: int
) -> tuple[dict, dict]:
    legacy_by_branch: dict = {}
    for b in branches:
        if verbose:
            print(f'  fetching legacy groupstatus/{b} ...', file=sys.stderr)
        legacy_by_branch[b] = legacy.group_status(b)
    if verbose:
        print('  fetching new project_access ...', file=sys.stderr)
    new_data = new.project_access()
    return legacy_by_branch, new_data


def _fetch_fstree(
    legacy: LegacyClient, new: NewClient, resources: Optional[list[str]], verbose: int
) -> tuple[dict, dict]:
    if verbose:
        print('  fetching new fstree_access ...', file=sys.stderr)
    new_data = new.fstree_access()

    if resources is None:
        resources = collect_resource_names(new_data)

    legacy_by_resource: dict = {}
    for r in resources:
        if verbose:
            print(f'  fetching legacy fairShareTree/v3/{r} ...', file=sys.stderr)
        data = legacy.fstree(r)
        # Skip 404s and 500s (retired resources). LegacyClient returns None.
        if data is None or data.get('name') != 'fairShareTree':
            if verbose:
                print(f'    skipped ({r} returned no usable data)', file=sys.stderr)
            continue
        legacy_by_resource[r] = data

    if not legacy_by_resource:
        raise RuntimeError(
            'No legacy fstree data fetched — every requested resource returned 404/500. '
            'Check legacy URL and credentials.'
        )

    return legacy_by_resource, new_data


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _render_text_section(
    api: str, results: list[CheckResult], elapsed: float, max_mismatches: int
) -> str:
    lines: list[str] = []
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    header = f'== {api} == {passed}/{total} checks passed ({elapsed:.1f}s)'
    lines.append(header)
    lines.append('-' * len(header))
    for r in results:
        marker = '✓' if r.passed else '✗'
        n_mis = len(r.mismatches)
        suffix = '' if r.passed else f' — {n_mis} mismatches'
        lines.append(f'  {marker} {r.name}: {r.summary}{suffix}')
        if not r.passed:
            for m in r.mismatches[:max_mismatches]:
                lines.append(f'      {m}')
            if n_mis > max_mismatches:
                lines.append(f'      ... and {n_mis - max_mismatches} more')
    return '\n'.join(lines) + '\n'


def _render_json(report: dict) -> str:
    return json.dumps(report, indent=2, default=str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        '--api', choices=('directory', 'project', 'fstree', 'all'),
        default='all', help='Which API to compare (default: all)',
    )
    p.add_argument(
        '--branch', default=None,
        help='Comma-separated branch list for project/directory '
             '(default: hpc,hpc-data,hpc-dev)',
    )
    p.add_argument(
        '--resource', default=None,
        help='Comma-separated resource list for fstree '
             '(default: every resource present in new fstree response)',
    )
    p.add_argument(
        '--format', choices=('text', 'json'), default='text',
        help='Output format (default: text)',
    )
    p.add_argument(
        '--max-mismatches', type=int, default=20,
        help='Cap printed mismatches per check in text mode (default: 20)',
    )
    p.add_argument(
        '--legacy-base-url', default=DEFAULT_LEGACY_BASE,
        help=f'Legacy API base URL (default: {DEFAULT_LEGACY_BASE})',
    )
    p.add_argument(
        '--new-base-url', default=DEFAULT_NEW_BASE,
        help=f'New API base URL (default: {DEFAULT_NEW_BASE})',
    )
    p.add_argument(
        '--timeout', type=int, default=120,
        help='HTTP request timeout in seconds (default: 120)',
    )
    p.add_argument('-v', '--verbose', action='count', default=0,
                   help='Print progress to stderr')
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    creds = _resolve_credentials()
    if creds is None:
        return 2
    legacy_auth, new_auth = creds

    if not _probe('legacy', args.legacy_base_url) or not _probe('new', args.new_base_url):
        return 2

    legacy = LegacyClient(args.legacy_base_url, legacy_auth, timeout=args.timeout)
    new = NewClient(args.new_base_url, new_auth, timeout=args.timeout)

    branches = (
        tuple(b.strip() for b in args.branch.split(',') if b.strip())
        if args.branch else PROJECT_BRANCHES
    )
    resources = (
        [r.strip() for r in args.resource.split(',') if r.strip()]
        if args.resource else None
    )

    selected = ('directory', 'project', 'fstree') if args.api == 'all' else (args.api,)

    sections: list[tuple[str, list[CheckResult], float]] = []
    try:
        for api in selected:
            t0 = time.monotonic()
            if api == 'directory':
                ld, nd = _fetch_directory(legacy, new, args.verbose)
                results = compare_directory_access(ld, nd)
            elif api == 'project':
                ld, nd = _fetch_project(legacy, new, branches, args.verbose)
                results = compare_project_access(ld, nd)
            elif api == 'fstree':
                ld, nd = _fetch_fstree(legacy, new, resources, args.verbose)
                results = compare_fstree_access(ld, nd)
            else:  # pragma: no cover — argparse choices guarantee membership
                raise AssertionError(api)
            sections.append((api, results, time.monotonic() - t0))
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        print(f'ERROR: fetch failed: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Interrupted.', file=sys.stderr)
        return 130

    any_failed = any(not r.passed for _, results, _ in sections for r in results)

    if args.format == 'json':
        report = {
            'legacy_base_url': args.legacy_base_url,
            'new_base_url': args.new_base_url,
            'sections': [
                {
                    'api': api,
                    'elapsed_seconds': round(elapsed, 2),
                    'results': [asdict(r) for r in results],
                }
                for api, results, elapsed in sections
            ],
            'overall_passed': not any_failed,
        }
        print(_render_json(report))
    else:
        for api, results, elapsed in sections:
            print(_render_text_section(api, results, elapsed, args.max_mismatches))
        total_checks = sum(len(r) for _, r, _ in sections)
        total_passed = sum(1 for _, r, _ in sections for x in r if x.passed)
        print(f'OVERALL: {total_passed}/{total_checks} checks passed')

    return 1 if any_failed else 0


if __name__ == '__main__':
    sys.exit(main())
