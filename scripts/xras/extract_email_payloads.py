#!/usr/bin/env python3
"""Unpack ``XRAS_post_action.json`` attachments out of a legacy-SAM email forward.

Legacy SAM emails the verbatim POST body to ``hdt@ucar.edu`` on every XRAS action, so
a bulk forward from that mailbox is the only way to grow the corpus before cutover
(``docs/xras/incoming/implemented/XRAS_SPRINT_A.md`` § 3b). Travis Fair's forwards arrive as one message
carrying alternating pairs: the notification ``.eml`` legacy sent, then the JSON body
that produced it.

Two facts make this worth a script rather than a hand-unpack:

* **Every JSON attachment is named ``XRAS_post_action.json``.** Saving them naively
  overwrites 40 files into one. The name has to be derived from the payload.
* **The outcome lives only in the paired ``.eml`` subject.** The JSON says what was
  asked; the subject next to it says what legacy *did*, and that pairing is the whole
  reason a forward is better evidence than a payload alone. Split them apart and it is
  unrecoverable.

Subjects map to outcomes as follows — the first three are
``EmailingActionPostService.formatSuccessSubject`` / ``formatFailureSubject``, the
fourth is ``ManualFallbackActionPostService.formatSubject``:

===================================================  ========  ==================================
subject                                              outcome   meaning
===================================================  ========  ==================================
``New XRAS project added: ...``                      ``ok``    Add path, projcode minted
``Existing XRAS project updated: ...``               ``ok``    ``requestNumber == projcode``
``Failed to add or update XRAS project``             failed    legacy rejected it
``New XRAS post action (<type> request for <code>)`` manual    **no serviceable matched**
===================================================  ========  ==================================

WARNING: The manual-fallback subject is the only record of the action types SAM does not
service (``XRAS_REIMPLEMENTATION.md`` § 1.4). It is also the clause the original harvest
query missed, which is how the Adjustment payload nearly went unnoticed. Never filter it
out.

WARNING: **Output is raw, unscrubbed PII.** It goes to a staging directory outside the working
tree and is written mode 0600. Nothing here is committable — run
``scripts/xras/scrub_payload.py`` over the whole staged corpus afterwards, in one
invocation, and commit *that*.

Usage
-----
    python scripts/xras/extract_email_payloads.py ~/Downloads/*.eml \\
        -o ~/xras_payloads_raw/incoming_2026-08-11
"""

import argparse
import email
import email.policy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

#: ``ManualFallbackActionPostService.formatSubject``. The captured group is the
#: ``actionType`` legacy could not service — the only place that name is recorded.
MANUAL_RE = re.compile(r'^New XRAS post action \((.+?) request for (\S+?)\)\s*$')

SUCCESS_PREFIXES = ('New XRAS project added:', 'Existing XRAS project updated:')
FAILURE_PREFIX = 'Failed to add or update XRAS project'


def classify(subject):
    """Map a legacy notification subject to ``(outcome, subject_action_type)``.

    ``subject_action_type`` is non-``None`` only on the manual arm, where the subject
    is the sole carrier of the type. Everywhere else the payload's own ``actionType``
    is authoritative and this returns ``None`` rather than guessing.
    """
    subject = ' '.join((subject or '').split())
    if subject.startswith(SUCCESS_PREFIXES):
        return 'ok', None
    if subject.startswith(FAILURE_PREFIX):
        return 'failed', None
    match = MANUAL_RE.match(subject)
    if match:
        return 'manual', match.group(1)
    return 'unknown', None


def _slug(value):
    """Lowercase, collapse anything that is not a word character to ``_``."""
    return re.sub(r'\W+', '_', (value or 'unknown').strip().lower()).strip('_')


def walk_pairs(message):
    """Yield ``(eml_subject, payload_bytes)`` in attachment order.

    Pairing is positional: legacy's forward alternates notification then body, and the
    JSON attachments are indistinguishable by name. Each JSON is attributed to the most
    recent ``message/rfc822`` seen, so a JSON with no preceding notification yields
    ``None`` and is reported rather than silently mis-attributed.
    """
    pending_subject = None
    for part in message.walk():
        if part.get_content_type() == 'message/rfc822':
            inner = part.get_payload(0)
            pending_subject = inner.get('Subject', '')
            continue
        filename = part.get_filename() or ''
        if not filename.lower().endswith('.json'):
            continue
        body = part.get_payload(decode=True)
        if body is None:
            continue
        yield pending_subject, body
        pending_subject = None


def extract(paths):
    """Parse every message, returning one record per JSON attachment found."""
    records = []
    for path in paths:
        with open(path, 'rb') as handle:
            message = email.message_from_binary_file(handle,
                                                     policy=email.policy.default)
        for subject, body in walk_pairs(message):
            try:
                doc = json.loads(body)
            except json.JSONDecodeError as exc:
                console.print(f'[red]{path.name}: unparsable JSON attachment '
                              f'({exc})[/red]')
                continue
            outcome, subject_type = classify(subject)
            records.append({
                'source': path.name,
                'subject': ' '.join((subject or '').split()),
                'outcome': outcome,
                'subject_action_type': subject_type,
                'action_type': doc.get('actionType'),
                'request_number': doc.get('requestNumber'),
                'action_id': doc.get('actionId'),
                'digest': hashlib.sha256(body).hexdigest(),
                'body': body,
                'doc': doc,
            })
    return records


def dedupe(records, seen_digests=()):
    """Drop byte-identical repeats. Keyed on the **content digest**, not ``actionId``.

    A forward split across several emails re-sends pairs — batch 3 of the 2026-08-11
    forward repeats five of batch 2's, byte for byte.

    WARNING: **``actionId`` is NOT an identity key, and keying on it loses payloads.**
    Measured in that same forward: ``actionId=388865`` arrives twice with *different
    bodies* — once as ``requestNumber=NCAR4236``, which legacy failed, and once as
    ``requestNumber=UCHI0020``, which legacy applied as an update. So one action id
    spans a failure and the re-push that followed it, and the two are different posts
    with different outcomes. Deduping on it silently discarded the failure. See
    :func:`action_id_collisions`, which reports the pattern rather than hiding it.

    (A re-push, not an automatic retry: ACCESS confirmed on 2026-08-11 that XRAS
    POSTs are human-triggered from xras_admin and never retried automatically.)

    *seen_digests* holds payloads already staged elsewhere, so re-extracting a forward
    that overlaps the existing corpus does not produce a second copy under a new name.
    """
    seen, unique, dropped, already = set(seen_digests), [], [], []
    for record in records:
        if record['digest'] in seen:
            (already if record['digest'] in seen_digests else dropped).append(record)
            continue
        seen.add(record['digest'])
        unique.append(record)
    return unique, dropped, already


def action_id_collisions(records):
    """Distinct payloads sharing one ``actionId`` — the retry signature.

    Worth reporting on its own because ``xras_action_log.action_id`` exists to answer
    "have I seen this action before?" (``XRAS_STRESS_AND_SCHEMA.md`` § *Verdicts*), and
    this is the evidence that the answer is "yes, but not necessarily the same body".
    """
    by_id = {}
    for record in records:
        if record['action_id'] is not None:
            by_id.setdefault(record['action_id'], []).append(record)
    return {aid: rows for aid, rows in sorted(by_id.items())
            if len({r['digest'] for r in rows}) > 1}


def name_for(record, taken):
    """``{actionType}_{requestNumber}_{outcome}.json``, matching the committed corpus.

    The existing fixtures are named this way (``extension_ucub0166_ok.json``), and the
    name is how a failing test tells you which real action broke. ``actionId`` is
    appended only on a genuine collision, so stable names stay stable.
    """
    action_type = record['action_type'] or record['subject_action_type']
    stem = f"{_slug(action_type)}_{_slug(record['request_number'])}_{record['outcome']}"
    candidate = f'{stem}.json'
    if candidate in taken:
        candidate = f"{stem}_{record['action_id']}.json"
    taken.add(candidate)
    return candidate


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', nargs='+', type=Path,
                    help='raw .eml forwards from hdt@ucar.edu')
    ap.add_argument('-o', '--outdir', type=Path, required=True,
                    help='staging directory OUTSIDE the working tree')
    ap.add_argument('--seen', type=Path, action='append', default=[],
                    help='directory of already-staged payloads to skip (repeatable)')
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would be written without writing it')
    args = ap.parse_args(argv)

    records = extract(sorted(args.inputs))
    if not records:
        console.print('[red]no JSON attachments found[/red]')
        return 1

    seen_digests = {}
    for directory in args.seen:
        for path in sorted(directory.glob('*.json')):
            seen_digests[hashlib.sha256(path.read_bytes()).hexdigest()] = path.name

    collisions = action_id_collisions(records)
    unique, dropped, already = dedupe(records, seen_digests)
    unique.sort(key=lambda r: (r['outcome'], r['action_type'] or '',
                               r['request_number'] or ''))

    taken = set()
    table = Table(title=f'XRAS payloads extracted ({len(unique)} unique '
                        f'of {len(records)})')
    for column in ('written', 'actionType', 'requestNumber', 'actionId', 'outcome'):
        table.add_column(column)

    if not args.dry_run:
        args.outdir.mkdir(parents=True, exist_ok=True)
        os.chmod(args.outdir, 0o700)

    unknown = []
    for record in unique:
        name = name_for(record, taken)
        if record['outcome'] == 'unknown':
            unknown.append((name, record['subject']))
        if not args.dry_run:
            dest = args.outdir / name
            dest.write_bytes(record['body'])
            os.chmod(dest, 0o600)
        table.add_row(name, str(record['action_type']),
                      str(record['request_number']), str(record['action_id']),
                      record['outcome'])

    console.print(table)

    if dropped:
        console.print(f'[yellow]{len(dropped)} byte-identical repeat(s) dropped '
                      f'within this forward[/yellow]')
    if already:
        console.print(f'[yellow]{len(already)} payload(s) already staged, '
                      f'skipped:[/yellow]')
        for record in already:
            console.print(f"  actionId={record['action_id']} "
                          f"({record['request_number']}) → held as "
                          f"{seen_digests[record['digest']]}")

    # Not an error — a fact about the wire, and one that bears on the open cutover
    # question of how the broker behaves after a 4xx.
    if collisions:
        console.print(f'[cyan]{len(collisions)} actionId(s) carry MORE THAN ONE '
                      f'distinct body — retry evidence:[/cyan]')
        for aid, rows in collisions.items():
            console.print(f'  actionId={aid}:')
            for record in rows:
                console.print(f"    {record['request_number']:<12} "
                              f"{record['action_type']:<16} → {record['outcome']}")

    # A subject that matches none of the four known formats means legacy has a fifth
    # mailer, which would be a new fact about the wire — worth stopping for.
    if unknown:
        console.print('[red]Unrecognised notification subject(s) — legacy may have a '
                      'mailer this script does not know:[/red]')
        for name, subject in unknown:
            console.print(f'  {name}: {subject!r}')

    if args.dry_run:
        console.print('[yellow]--dry-run: nothing written[/yellow]')
    else:
        console.print(f'[green]wrote {len(unique)} payload(s) to {args.outdir}[/green]')
        console.print('⚠️  These are RAW and carry PII. Scrub the whole staged corpus '
                      'in ONE run before committing:\n'
                      '    python scripts/xras/scrub_payload.py '
                      '~/xras_payloads_raw/*.json ~/xras_payloads_raw/*/*.json '
                      '-o tests/fixtures/xras/actions')
    return 0


if __name__ == '__main__':
    sys.exit(main())
