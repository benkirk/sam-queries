"""Shared XRAS helpers for the Allocations dashboard.

Route-free home for the XRAS constants, the filter/facet helpers, and the XRAS
client / degrade infrastructure that the ``allocations/xras/`` route modules
(``card_routes``, ``lifecycle_routes``, ``modals``, ``remediation``) build on.

Import-cheap and side-effect-free on purpose: it declares no ``@bp.route`` and
imports nothing from its sibling route modules, so any of them may import it
without a circular dependency. The blueprint-origin filter/facet helpers were
lifted verbatim from ``blueprint.py`` and the client/degrade infra verbatim
from ``xras_remediation_routes.py`` — ``git blame -C`` follows both moves.
"""

from datetime import date, datetime, timedelta

from flask import current_app, render_template

from webapp.extensions import db
from webapp.utils.htmx import modal_triggers, read_page, read_sort
from sam.integration.xras_api import XrasSourceUnavailable
from sam.manage import xras_remediation as remediation
from sam.queries.xras_actions import XRAS_ACTION_SORT_COLUMNS
from sam.queries.xras_activation import ACTIVITY_TAGS
from sam.queries.xras_accounts import (CLASSIFICATION_ABSENT,
                                       CLASSIFICATION_INACTIVE,
                                       SOURCE_ACTION_LOG, SOURCE_REPORTS)


def _session_factory():
    """Private sessions for the service's audit writes.

    Deliberately **not** ``db.session``: an audit row that a request rollback
    could erase would be a record of an irreversible act that can be un-said.

    WARNING: the service takes the factory, not a session -- pass
    ``_session_factory``, never ``_session_factory()``. The first production
    merge (2026-08-25) shipped the call form and wrote no audit row; the
    service logs and proceeds by design, so nothing else fails.
    """
    from sqlalchemy.orm import Session
    return Session(db.engine)


def _index():
    """The published request index, or ``None`` if no sweep has written one."""
    from sam.integration.xras_api.cache import load_requests_index
    return load_requests_index()


def _entry(request_number):
    """One request's snapshot entry, or ``None``.

    Snapshot-derived and therefore possibly stale — which is fine for deciding
    *which buttons to draw*. Every modal re-reads live before offering to act,
    and the client verifies after acting.
    """
    payload = _index() or {}
    wanted = str(request_number).strip()
    for row in payload.get('rows') or ():
        if isinstance(row, dict) and str(row.get('request_number') or '').strip() == wanted:
            return row
    return None


def _degraded(message, *, title='XRAS unavailable'):
    """A 200 body explaining why a modal cannot proceed. See the module docstring."""
    return render_template(
        'dashboards/allocations/partials/xras_remediation_degraded.html',
        title=title, message=message)


def _render_xras_modal(*, build, template, noun, not_found, log_label):
    """Shared body for the three read-only detail modals (request/user/opportunity).

    Every one does the same four things and differs only in the four arguments:
    run ``build()``; degrade with a **200** on an XRAS outage (htmx will not swap
    a 4xx into an already-open modal — see the module docstring); show
    ``not_found()`` when XRAS holds no such thing (``build`` returned ``None``);
    else render ``template`` with the built context. ``noun`` fills the standard
    outage line and ``log_label`` names the warning.
    """
    try:
        context = build()
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('%s: %s', log_label, exc)
        return _degraded(f'Showing this {noun} needs a live read from XRAS, '
                         'and XRAS is not answering.')
    if context is None:
        return not_found()
    return render_template(template, **context)


def _read_client():
    from sam.integration.xras_api import XrasApiClient
    return XrasApiClient.from_environment()


def _primary_line(lines):
    """The request line holding the project's globally most-recent action.

    A projcode can have several request lines (a New plus Renewals, each its own
    ``requestId``); this picks the one with the highest ``actionId`` — the current
    request. The modal anchors its header/roster and every write on it, so the
    target is deterministic rather than XRAS's arbitrary ``lines[0]`` order.
    """
    def _max_action(line):
        ids = [a.get('actionId') for a in (line.get('actions') or ())
               if isinstance(a.get('actionId'), int)]
        return max(ids) if ids else -1
    return max(lines, key=_max_action) if lines else None


def _live_request(request_number):
    """The project's **primary** request line, via the reports family.

    WARNING: Not ``lines[0]`` (XRAS's arbitrary order) and not
    ``GET /v1/requests/<id>`` (401 for our credential — so
    ``rules{allowedOperations}`` is unavailable and offers are derived,
    PRIVILEGE(#1)). Returning the primary line here is what makes every write
    handler that resolves ``request_id`` from it target the current request.
    """
    return _primary_line(_read_client().get_request_family_by_number(request_number))


def _live_family(request_number):
    """Every request line for a projcode — the whole allocation lifecycle, one call.

    All lines (a New plus any Renewals, each with its own ``requestId`` and
    ``actions[]``); :func:`_live_request` returns the primary one of these.
    """
    return _read_client().get_request_family_by_number(request_number)


def _impersonation(entry, live=None):
    """Who SAM should act as: the request's PI.

    Falls back to any role-holder, because a request whose PI record is broken
    is exactly the sort this card exists to fix and refusing to act on it would
    be unhelpful. Returns ``(username, is_pi, is_placeholder)`` so the modal can
    say what it got — probe P2 measured the PI and the Allocation Manager giving *different*
    validation verdicts on the same action, so the distinction is operational,
    not cosmetic.
    """
    from sam.queries.xras_requests import resolve_pi, roster_from_payload

    roster = (roster_from_payload(live) if live
              else (entry or {}).get('roster') or [])
    pi = resolve_pi(roster)
    username, is_pi = (pi, True) if pi else (
        next((r.get('username') for r in roster if r.get('username')), None), False)

    # WARNING: The project lead is sometimes an unmerged placeholder — measured on 2
    # of 27 live rows the first time this card was pointed at production. That
    # is legitimate as far as XRAS is concerned (the placeholder really does
    # hold the role, so the call authorizes), but the operator is then acting
    # as a throwaway identity that a merge on this very card would delete. It
    # is surfaced rather than worked around: preferring a different role-holder
    # would change who the write is attributed to, silently.
    placeholder = any(r.get('placeholder') and r.get('username') == username
                      for r in roster)
    return username, is_pi, placeholder


def _role_options():
    """(wire name, display label) pairs for the role select.

    Built here rather than in the template because Jinja has no list
    comprehension — and the pairing matters: the **name** goes on the wire and
    the **display** is XRAS's own operator vocabulary, so an operator reading
    SAM and the XRAS admin app sees one word for one thing.
    """
    return [(r['name'], r['display']) for r in remediation.role_choices()]


_XRAS_FRAGMENT_TARGET = 'alloc-xras-fragment'
_XRAS_FORM_ID = 'xras-filters'

#: The activity card's own filter form and swap target. Separate from the
#: action-log table's pair above: the two tables filter independently, and
#: sharing a form id would make one table's chips silently re-scope the other.
_XRAS_ACTIVITY_FORM_ID = 'xras-activity-filters'
_XRAS_ACTIVITY_TARGET = 'alloc-xras-pending'

#: Close the modal, then reload the tab behind it. Built by ``modal_triggers``
#: rather than written as a literal, which is what the four admin route modules
#: already do — the close half is the shared convention and only the reload event
#: is ours.
_XRAS_MODAL_TRIGGERS = modal_triggers('refreshXrasTab')


def _parse_xras_filters(request_args):
    """Parse filter + sort + pagination params for the XRAS fragment.

    Deliberately a sibling of ``_parse_audit_filters`` rather than a
    generalization of it. The sort/page halves are identical by convention (that
    is what makes the shared ``sort_link`` / ``pagination`` macros work) and now
    come from the shared ``read_sort`` / ``read_page``; the filter halves have
    nothing in common — projcode/resource/username/facility versus
    status/action-type/request-number. Merging *those* would mean a parameter
    for every field either page has.

    Returns ``(filters, sort, page)`` with the same shapes ``_parse_audit_filters``
    returns, because the table fragment renders through the same macros.

    Default 30-day window is applied iff **neither** ``start_date`` nor
    ``end_date`` appears in the query string — explicitly empty bounds mean
    "all time", which is a different intent from "I have not chosen".
    """
    statuses = request_args.getlist('status') or None
    action_types = request_args.getlist('action_type') or None
    request_number = (request_args.get('request_number') or '').strip() or None

    start_date_str = (request_args.get('start_date') or '').strip()
    end_date_str = (request_args.get('end_date') or '').strip()

    if 'start_date' not in request_args and 'end_date' not in request_args:
        start_date = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                      - timedelta(days=30))
        # Deliberately UNBOUNDED above, where the sibling audit pages use
        # datetime.now(). There are no future rows, so an upper bound buys
        # nothing — and a sub-second one actively loses the newest row.
        # `received_time` is a MySQL DATETIME with second resolution and MySQL
        # ROUNDS rather than truncates, so a row written at 10:10:24.894 is
        # stored as 10:10:25 and lands *after* an end_date captured microseconds
        # earlier in the same request. On an audit surface whose whole job is
        # answering "did my action get recorded?", the row most worth seeing is
        # the one that just arrived. (_parse_audit_filters above still has the
        # sub-second bound; same latent bug, left alone as pre-existing.)
        end_date = None
    else:
        try:
            start_date = (datetime.strptime(start_date_str, '%Y-%m-%d')
                          if start_date_str else None)
        except ValueError:
            start_date = None
        try:
            end_date = (datetime.strptime(end_date_str, '%Y-%m-%d')
                        .replace(hour=23, minute=59, second=59)
                        if end_date_str else None)
        except ValueError:
            end_date = None

    filters = {
        'status': statuses,
        'action_type': action_types,
        'request_number': request_number,
        'start_date': start_date,
        'end_date': end_date,
    }

    return (filters,
            read_sort(request_args, XRAS_ACTION_SORT_COLUMNS),
            read_page(request_args))


#: Window pills for the activity card, and the default. `days` is free on this
#: blueprint — it means lookback days in the jobs family and legacy days->hours
#: on the status-history routes, and neither is reachable from here.
_ACTIVITY_WINDOW_PILLS = ((7, '7D'), (30, '30D'), (90, '90D'))
_ACTIVITY_DEFAULT_DAYS = 30
_ACTIVITY_MAX_DAYS = 365

#: Chip text for each tag. The tag itself is a slug that round-trips through
#: the form; an operator should never see it, so the two are kept apart rather
#: than the vocabulary being renamed to read nicely in both places.
_ACTIVITY_TAG_LABELS = {
    'needs_activation': 'Activation',
    'not_notified': 'Not notified',
    'notified': 'Notified',
    'failed': 'Delivery failed',
    'dismissed': 'Dismissed',
}


def _parse_activity_window(args) -> dict:
    """``days`` pill, or an explicit custom range. Never a 400.

    An explicit ``start_date``/``end_date`` **outranks** ``days`` — the Custom
    pill sets the dates and leaves ``days`` behind in the form, so reading
    ``days`` first would silently ignore the range the operator just typed.

    Returns the parsed bounds *and* the raw strings, because the same dict has
    to re-render the form controls.
    """
    start_raw = (args.get('start_date') or '').strip()
    end_raw = (args.get('end_date') or '').strip()

    def _date(raw, end_of_day=False):
        try:
            parsed = datetime.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return None
        return (parsed.replace(hour=23, minute=59, second=59)
                if end_of_day else parsed)

    since = _date(start_raw) if start_raw else None
    until = _date(end_raw, end_of_day=True) if end_raw else None
    if since is not None or until is not None:
        return {'days': None, 'since': since, 'until': until,
                'start_date': start_raw, 'end_date': end_raw, 'custom': True}

    days = args.get('days', type=int) or _ACTIVITY_DEFAULT_DAYS
    days = max(1, min(days, _ACTIVITY_MAX_DAYS))
    return {'days': days, 'since': datetime.now() - timedelta(days=days),
            'until': None, 'start_date': '', 'end_date': '', 'custom': False}


def sort_rows(rows, sort, keymap):
    """In-Python sort of snapshot rows by a whitelisted column, None-last in both
    directions. ``keymap`` maps a ``sort_by`` value to a row-key function; an
    unknown/absent column leaves the order untouched."""
    keyfn = keymap.get((sort or {}).get('sort_by'))
    if not keyfn:
        return list(rows)
    reverse = (sort or {}).get('sort_dir') == 'desc'
    present = [r for r in rows if keyfn(r) is not None]
    absent = [r for r in rows if keyfn(r) is None]
    present.sort(key=keyfn, reverse=reverse)
    return present + absent


def _row_activity_type(row) -> str:
    """The chip value for the action-type dimension.

    ``action_type`` rather than ``service``, because it is the word the wire
    used and the one the action-log table below already shows. The two differ
    on exactly one case — a ``New`` against an existing project routes to the
    ``update`` service — and an operator scanning for "the New that came in"
    should find it under New.
    """
    return row.get('action_type') or '—'


def _filter_activity(rows, *, tags=None, types=None):
    """Apply the chip selections. Tags are ANDed with types, ORed within."""
    if tags:
        wanted = set(tags)
        rows = [r for r in rows if wanted & set(r['tags'])]
    if types:
        wanted_types = set(types)
        rows = [r for r in rows if _row_activity_type(r) in wanted_types]
    return rows


def _activity_facets(rows, dimension, *, tags=None, types=None) -> dict:
    """Counts for one chip dimension, **excluding that dimension's own filter**.

    Computed in Python rather than SQL because the rows are already assembled
    here — the notification rollup that produces the tags has no SQL form. The
    set is one window of processed actions, so this is a pass over a list, not
    a scan.
    """
    if dimension == 'tag':
        scoped = _filter_activity(rows, types=types)
        counts = {tag: 0 for tag in ACTIVITY_TAGS}
        for row in scoped:
            for tag in row['tags']:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    if dimension == 'activity_type':
        scoped = _filter_activity(rows, tags=tags)
        counts: dict = {}
        for row in scoped:
            key = _row_activity_type(row)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    raise ValueError(f'unknown activity facet dimension {dimension!r}')


#: Display labels for the classification facet. The slug is what round-trips
#: through the form; an operator should never see it.
#: WARNING: These name the ARTIFACT, not an action SAM performs — and that is the
#: whole point of the wording. `users` is mirrored into SAM from the enterprise
#: directory by a process outside this codebase: there is no INSERT into
#: `users` anywhere in the tree, `User` alone among the models has no
#: `create()`, and nothing here ever writes `active` or `locked`. So both
#: remedies are somebody else's work, and the earlier labels — "Create
#: account" / "Reactivate account" — read as instructions to a SAM operator who
#: has no way to carry them out.
#:
#: Naming the artifact rather than the team is deliberate too: the owning group
#: can change without touching 26 rows and the CLI's JSON envelope, and the
#: banner names it once where it can be kept current.
_ACCOUNT_CLASSIFICATION_LABELS = {
    CLASSIFICATION_ABSENT: 'New account',
    CLASSIFICATION_INACTIVE: 'Reactivation',
}

_ACCOUNTS_FORM_ID = 'xras-accounts-filters'
_ACCOUNTS_TARGET = 'alloc-xras-accounts'

#: Bounds a cold-cache render. Each miss is one round trip to XRAS inside an
#: htmx request, so this is a latency bound, not a correctness one — the rows
#: past it still render, just without person detail.
_ACCOUNTS_ENRICH_BUDGET = 25

#: The ``placeholder`` facet's two values. Strings rather than a bool because a
#: form round-trips strings, and ``'false'`` is truthy on the way back in.
ORIGIN_PLACEHOLDER = 'placeholder'
ORIGIN_KNOWN = 'known'

_ORIGIN_LABELS = {
    ORIGIN_PLACEHOLDER: 'ARC placeholder',
    ORIGIN_KNOWN: 'Known identity',
}

#: The ``source`` facet, keyed on the provenance tags a worklist row carries.
#: A received-push row is the more urgent flavor (a push already arrived and is
#: blocked); a pending-request row is the lookahead. A row may carry both.
_SOURCE_LABELS = {
    SOURCE_ACTION_LOG: 'Received push',
    SOURCE_REPORTS: 'Pending request',
}


def _filter_accounts(rows, *, classifications=None, roles=None, origins=None,
                     sources=None):
    """Facet filters: ANDed across dimensions, ORed within one.

    *origins* is the ``placeholder`` dimension, expressed as the two values a
    form can round-trip. It earns a facet because it separates the two
    populations that share this card: an ARC placeholder is a researcher who
    has never had a site account, while a non-placeholder is a real identity
    whose account lapsed. Those are different pieces of work for different
    people, and until now the only way to tell them apart was to read the shape
    of the username.

    *sources* is the provenance dimension — which feed put the row here. A row
    is kept when ANY selected source is one it carries, so a both-feeds row
    survives either filter.

    WARNING: Deliberately **not** defaulted. The rule on this card is *no selection =
    no filter*, and defaulting one dimension on would make an empty facet row
    mean something different here than on every other card.
    """
    out = rows
    if classifications:
        out = [r for r in out if r['classification'] in classifications]
    if roles:
        out = [r for r in out if any(role in roles for role in r['roles'])]
    if origins:
        wanted = {o == ORIGIN_PLACEHOLDER for o in origins}
        out = [r for r in out if bool(r['placeholder']) in wanted]
    if sources:
        out = [r for r in out
               if any(s in (r.get('sources') or ()) for s in sources)]
    return out


#: Requests to offer as chips. A worklist spanning dozens of projects would
#: otherwise render a chip wall; the cap is on the CHIPS, not the rows, and
#: the rows all stay visible whether or not their request earned one.
_MAX_REQUEST_CHIPS = 12


def _submitted_since(row, since):
    """Did any request naming this person appear in XRAS within the window?

    The Feed-B analogue of Feed A's ``received_time`` filter — the same
    question ("when did this show up?") asked of a feed that has no arrival of
    its own, which is what lets one control span both tabs.

    A row with no usable submit date is kept: that is missing information, not
    evidence of age, and dropping it would silently shrink the queue.
    """
    if since is None:
        return True
    start = since.date() if hasattr(since, 'date') else since
    dates = [a.get('submit_date') for a in row.get('actions') or []]
    if not any(dates):
        return True
    for raw in dates:
        if not raw:
            return True
        try:
            if date.fromisoformat(str(raw)[:10]) >= start:
                return True
        except ValueError:
            return True
    return False


def _request_facets(rows, *, classifications=None):
    """Counts per XRAS request number, most-affected first.

    Self-excluding on its own dimension, like every other facet here. Rows
    naming no request number contribute nothing: a NULL cannot round-trip
    through the form, so it must not become a chip.
    """
    scoped = _filter_accounts(rows, classifications=classifications)
    counts = {}
    for row in scoped:
        for number in {a['request_number'] for a in row['actions'] if a['request_number']}:
            counts[number] = counts.get(number, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{'value': k, 'count': v} for k, v in ordered[:_MAX_REQUEST_CHIPS]]


def _account_facets(rows, dimension, *, classifications=None, roles=None,
                    sources=None):
    """Self-excluding counts for one dimension.

    A dimension's rollup omits its own filter — scope it by itself and every
    unselected value reads 0 the moment one is picked, which turns the chips
    from switchers into a dead end. Same rule as :func:`_activity_facets`.
    """
    if dimension == 'classification':
        scoped = _filter_accounts(rows, roles=roles, sources=sources)
        return {key: sum(1 for r in scoped if r['classification'] == key)
                for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)}

    if dimension == 'role':
        scoped = _filter_accounts(rows, classifications=classifications,
                                  sources=sources)
        counts = {}
        for row in scoped:
            for role in row['roles']:
                counts[role] = counts.get(role, 0) + 1
        return dict(sorted(counts.items()))

    if dimension == 'origin':
        scoped = _filter_accounts(rows, classifications=classifications,
                                  roles=roles, sources=sources)
        return {
            ORIGIN_PLACEHOLDER: sum(1 for r in scoped if r['placeholder']),
            ORIGIN_KNOWN: sum(1 for r in scoped if not r['placeholder']),
        }

    if dimension == 'source':
        scoped = _filter_accounts(rows, classifications=classifications,
                                  roles=roles)
        # A both-feeds row counts in both, so this is not a partition.
        return {key: sum(1 for r in scoped if key in (r.get('sources') or ()))
                for key in (SOURCE_ACTION_LOG, SOURCE_REPORTS)}

    raise ValueError(f'unknown account facet dimension {dimension!r}')


_WINDOW_TARGET = 'alloc-xras-window'

