#!/usr/bin/env python3
"""Scrub PII out of real ``XRAS_post_action.json`` bodies so they can be committed.

Legacy SAM emails the verbatim POST body to ``hdt@ucar.edu`` on every XRAS action
(``EmailingActionPostService``, ``xras.actionpost.recipients``). Those attachments are
the only record of the wire contract that exists — ``actionJson`` is never logged at
any level — so they are the source of truth for ``XrasActionSchema`` and the handler
tests. They also carry real names, emails, phone numbers and grant-officer contacts.

This script takes *shape* from the real payload and removes *identity*, so the result
is committable to ``tests/fixtures/xras/actions/``.

What is removed
---------------
``roles[].person``   firstName, middleName, lastName, email, phone
``roles[].username`` and every other username-shaped identity
``grants[]``         programOfficerName, programOfficerEmail, piName
``requestAbstract``  replaced with a short placeholder (real proposal text, and its
                     length is not contract-relevant); ``--keep-abstract`` opts out

What is deliberately PRESERVED, because it *is* the contract evidence
--------------------------------------------------------------------
* **null vs "" vs absent.** XRAS sends every key and uses ``null`` for absent
  scalars; that distinction is the single most load-bearing fact about the payload,
  so a null stays null and an empty string stays an empty string.
* ``person.organization`` — free text with inconsistent case and appended role
  suffixes. It is an institution, not a person, and it is the input to the mnemonic
  extractor whose failures are 24% of production traffic.
* ``person.academicStatus``, all ids, all dates, all amounts, ``fundingAgency``,
  ``grantNumber``, ``grants[].title``, ``requestTitle``, and the undeclared
  ``opportunityQA`` / ``requestGrantType`` / ``resourceQA`` fields.
* **The ARC placeholder username shape.** ``<name>-user-<token>`` identities are 55%
  of production failures, so a placeholder username is rewritten to another
  placeholder-shaped username rather than to a plain one.

Pseudonyms are assigned **sequentially in first-seen order**, not derived by hashing
the original value. That is deliberate: a hash of a real name is a re-identification
oracle for anyone who can guess the input, and a name space is small enough to
enumerate. Sequential assignment carries no information about the original, and no
mapping file is written for the same reason.

Consequence: numbering is stable for a given set of inputs in a given order (inputs
are sorted), but scrubbing a *different* batch later will assign different numbers.
Scrub once, commit the result.

Usage
-----
    python scripts/xras/scrub_payload.py ~/xras_payloads_raw/*.json \
        -o tests/fixtures/xras/actions

    python scripts/xras/scrub_payload.py RAW.json --stdout | jq .
"""

import argparse
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

#: Person contact fields replaced with pseudonyms. The three name fields are handled
#: together in ``scrub`` because they must share one per-human number.
PERSON_EMAIL_FIELDS = ('email',)
PERSON_PHONE_FIELDS = ('phone',)

#: ``grants[]`` carries a *different* person — the funding-agency program officer —
#: plus the grant PI's display name, none of which SAM reads.
GRANT_NAME_FIELDS = ('programOfficerName', 'piName')
GRANT_EMAIL_FIELDS = ('programOfficerEmail',)

ABSTRACT_PLACEHOLDER = (
    'Scrubbed abstract. The real request abstract was removed by '
    'scripts/xras/scrub_payload.py; its content is not contract-relevant.'
)

#: RFC 2606 reserved TLD — these addresses can never route.
EMAIL_DOMAIN = 'example.invalid'


class Pseudonymizer:
    """Assigns stable pseudonyms in first-seen order, per value kind.

    One instance is shared across every file in a run so that a person appearing in
    two payloads gets the same pseudonym in both.
    """

    def __init__(self):
        self._maps = {}
        self._counts = {}

    def _next(self, kind, original):
        """Map ``original`` to its pseudonym, minting one on first sight."""
        seen = self._maps.setdefault(kind, {})
        if original not in seen:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            seen[original] = self._counts[kind]
        return seen[original]

    def person(self, person):
        """Return one stable number for a whole human, not per name field.

        Keyed on the identifying tuple so the same person appearing in several
        payloads — or twice in one payload under two ``roleType``s, which happens —
        scrubs to the same pseudo-identity. Numbering per *field* would emit
        ``Given01 M02 Surname03`` for one human, which reads like a bug and loses
        the property that makes the corpus useful.
        """
        key = (
            person.get('firstName'), person.get('middleName'),
            person.get('lastName'), person.get('email'),
        )
        return self._next('person', key)

    def phone(self, v):
        # 555-01xx is the reserved fictional-number range.
        return f'555-01{self._next("phone", v) % 100:02d}'

    def username(self, v):
        """Rewrite a username, preserving the ARC placeholder shape when present.

        ``gsaha-user-hv1bu`` is an unreconciled ARC placeholder identity — the shape
        itself is contract-relevant (55% of production failures), so it survives.
        """
        n = self._next('username', v)
        if '-user-' in v:
            return f'placeholder{n:02d}-user-{n:05d}'
        return f'user_{n:08d}'

    def grant_person(self, v):
        return f'Officer{self._next("grant_person", v):02d}'

    def grant_email(self, v):
        return f'officer{self._next("grant_email", v):02d}@{EMAIL_DOMAIN}'

    def stats(self):
        return {k: len(v) for k, v in sorted(self._maps.items())}


def _sub(obj, key, fn):
    """Replace ``obj[key]`` via ``fn``, preserving null and empty-string exactly.

    The null-vs-empty-string distinction is the contract evidence these fixtures
    exist to carry, so it must survive scrubbing untouched.
    """
    if key not in obj:
        return
    val = obj[key]
    if val is None or val == '':
        return
    obj[key] = fn(val)


def scrub(payload, pseudo, *, keep_abstract=False):
    """Return a scrubbed deep copy of one parsed action payload."""
    doc = json.loads(json.dumps(payload))  # cheap deep copy

    if not keep_abstract:
        _sub(doc, 'requestAbstract', lambda _v: ABSTRACT_PLACEHOLDER)

    for role in doc.get('roles') or []:
        _sub(role, 'username', pseudo.username)
        person = role.get('person')
        if not isinstance(person, dict):
            continue
        # One number for the whole human; every field below derives from it.
        n = pseudo.person(person)
        _sub(person, 'firstName', lambda _v, n=n: f'Given{n:02d}')
        _sub(person, 'middleName', lambda _v, n=n: f'M{n:02d}')
        _sub(person, 'lastName', lambda _v, n=n: f'Surname{n:02d}')
        for f in PERSON_EMAIL_FIELDS:
            _sub(person, f, lambda _v, n=n: f'user{n:02d}@{EMAIL_DOMAIN}')
        for f in PERSON_PHONE_FIELDS:
            _sub(person, f, pseudo.phone)

    for grant in doc.get('grants') or []:
        for f in GRANT_NAME_FIELDS:
            _sub(grant, f, pseudo.grant_person)
        for f in GRANT_EMAIL_FIELDS:
            _sub(grant, f, pseudo.grant_email)

    return doc


#: A phone-shaped run of digits: 7+ digits with optional separators, or an E.164
#: ``+``-prefixed number. Anchored loosely on purpose — this is a tripwire, and a
#: false positive costs one look while a miss costs a PII commit.
PHONE_RE = re.compile(r'(?:\+\d[\d ().-]{7,})|(?:\b\d{3}[ .-]\d{3}[ .-]\d{4}\b)')

#: What a scrubbed person field must look like. Anything else in a name slot means
#: the pseudonymiser did not reach it.
PSEUDONYM_RE = re.compile(r'^(Given|M|Surname)\d{2}$')

#: Person fields whose value must always be a pseudonym after scrubbing.
NAME_PATHS = ('firstName', 'middleName', 'lastName')


def audit(doc):
    """Return any string that still looks like a leaked contact detail.

    A guard against a field being added to the payload upstream and silently
    bypassing the scrubber — the failure mode that matters here, and the reason this
    checks the *output* rather than trusting the field list above.

    Three tripwires, because the field list is a whitelist and the wire is not ours:

    1. **An address** — any ``@`` outside ``EMAIL_DOMAIN``.
    2. **A phone number** — a digit run in any field, not just ``person.phone``.
       ``555-01xx`` is the reserved fictional range the scrubber emits and is exempt.
    3. **An unpseudonymised name** — a ``firstName`` / ``middleName`` / ``lastName``
       that is not ``Given01`` / ``M01`` / ``Surname01`` shaped. This is what catches
       a *second* roster array appearing under a new key, which the whitelist would
       walk straight past.

    Dates are exempt from the phone check: ``'2026-07-28'`` is not a phone number, and
    every payload is full of them.
    """
    leaks = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f'{path}.{k}' if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')
        elif isinstance(node, str):
            leaf = path.rsplit('.', 1)[-1]
            if '@' in node and EMAIL_DOMAIN not in node:
                leaks.append((path, node))
            elif (PHONE_RE.search(node) and not node.startswith('555-01')
                    and 'Date' not in leaf and 'date' not in leaf):
                leaks.append((path, node))
            elif leaf in NAME_PATHS and not PSEUDONYM_RE.match(node):
                leaks.append((path, node))

    walk(doc, '')
    return leaks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', nargs='+', type=Path,
                    help='raw XRAS_post_action.json files (sorted before scrubbing)')
    ap.add_argument('-o', '--outdir', type=Path,
                    help='directory to write scrubbed files into')
    ap.add_argument('--stdout', action='store_true',
                    help='write the single scrubbed payload to stdout instead')
    ap.add_argument('--keep-abstract', action='store_true',
                    help='retain requestAbstract verbatim (local use only)')
    args = ap.parse_args(argv)

    if not args.stdout and not args.outdir:
        ap.error('one of -o/--outdir or --stdout is required')
    if args.stdout and len(args.inputs) != 1:
        ap.error('--stdout takes exactly one input file')

    # Sorted so pseudonym numbering is reproducible for a given input set.
    inputs = sorted(args.inputs)
    pseudo = Pseudonymizer()
    results = []

    for src in inputs:
        doc = scrub(json.loads(src.read_text()), pseudo,
                    keep_abstract=args.keep_abstract)
        leaks = audit(doc)
        results.append((src, doc, leaks))

    if args.stdout:
        json.dump(results[0][1], sys.stdout, indent=2)
        sys.stdout.write('\n')
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    table = Table(title='Scrubbed XRAS action payloads')
    table.add_column('source')
    table.add_column('written')
    table.add_column('bytes', justify='right')
    table.add_column('leaks', justify='right')

    failed = False
    for src, doc, leaks in results:
        dest = args.outdir / src.name
        body = json.dumps(doc, indent=2) + '\n'
        dest.write_text(body)
        if leaks:
            failed = True
        table.add_row(src.name, str(dest), str(len(body)),
                      '[red]%d[/red]' % len(leaks) if leaks else '[green]0[/green]')

    console.print(table)
    console.print(f'pseudonyms assigned: {pseudo.stats()}')

    if failed:
        console.print('[red]Possible contact detail survived scrubbing:[/red]')
        for src, _doc, leaks in results:
            for path, val in leaks:
                console.print(f'  {src.name}: {path} = {val!r}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
