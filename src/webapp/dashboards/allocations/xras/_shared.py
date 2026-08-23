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
from sam.queries.xras_accounts import CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE


def _session_factory():
    """Private sessions for the service's audit writes.

    Deliberately **not** ``db.session``: an audit row that a request rollback
    could erase would be a record of an irreversible act that can be un-said.
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


def _live_request(request_number):
    """Live roster + action states for one request, via the reports family.

    WARNING: Not ``GET /v1/requests/<id>``, which is 401 for our credential in every
    context — so ``rules{allowedOperations}``, the API's own answer to "what may
    I do to this action", is unavailable and the offers are derived instead
    (PRIVILEGE(#1)).
    """
    return _read_client().get_request_by_number(request_number)


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


def _filter_accounts(rows, *, classifications=None, roles=None, origins=None):
    """Facet filters: ANDed across dimensions, ORed within one.

    *origins* is the ``placeholder`` dimension, expressed as the two values a
    form can round-trip. It earns a facet because it separates the two
    populations that share this card: an ARC placeholder is a researcher who
    has never had a site account, while a non-placeholder is a real identity
    whose account lapsed. Those are different pieces of work for different
    people, and until now the only way to tell them apart was to read the shape
    of the username.

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
    return out


_PENDING_FORM_ID = 'xras-pending-filters'

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


def _account_facets(rows, dimension, *, classifications=None, roles=None):
    """Self-excluding counts for one dimension.

    A dimension's rollup omits its own filter — scope it by itself and every
    unselected value reads 0 the moment one is picked, which turns the chips
    from switchers into a dead end. Same rule as :func:`_activity_facets`.
    """
    if dimension == 'classification':
        scoped = _filter_accounts(rows, roles=roles)
        return {key: sum(1 for r in scoped if r['classification'] == key)
                for key in (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE)}

    if dimension == 'role':
        scoped = _filter_accounts(rows, classifications=classifications)
        counts = {}
        for row in scoped:
            for role in row['roles']:
                counts[role] = counts.get(role, 0) + 1
        return dict(sorted(counts.items()))

    if dimension == 'origin':
        scoped = _filter_accounts(rows, classifications=classifications,
                                  roles=roles)
        return {
            ORIGIN_PLACEHOLDER: sum(1 for r in scoped if r['placeholder']),
            ORIGIN_KNOWN: sum(1 for r in scoped if not r['placeholder']),
        }

    raise ValueError(f'unknown account facet dimension {dimension!r}')


def _pending_account_total():
    """How many accounts the *other* tab is holding, or ``None``.

    WARNING: Counts only. Reading the sibling feed's rows into this card would undo
    the split the two tabs exist to draw — one is what has posted, the other is
    a lookahead at what XRAS may send. But a card that reports "8" while 18
    more sit one click away is a queue that reads as smaller than it is, and
    that is the failure this whole change is about.

    ``None`` means "could not look", which is the honest answer when the
    outbound API is off or no sweep has published — distinct from zero.
    """
    from sam.integration.xras_api import xras_api_configured

    if not xras_api_configured():
        return None
    try:
        from sam.integration.xras_api.cache import load_pending_worklist

        snapshot = load_pending_worklist()
    except Exception:                                # noqa: BLE001
        # Cache backends are infrastructure. A cross-reference is a courtesy;
        # it must never be the reason the worklist 500s.
        current_app.logger.warning(
            'xras accounts: could not read the pending worklist for the '
            'cross-reference', exc_info=True)
        return None
    if not snapshot:
        return None
    return (snapshot.get('counts') or {}).get('total')


_PENDING_TARGET = 'alloc-xras-pending-requests'
_WINDOW_TARGET = 'alloc-xras-window'

