"""XRAS Remediations — the operator write surface on Allocations → XRAS.

A scoped **subset** of the external XRAS admin dashboard, never a replacement:
resolve an erroneously-reconciled placeholder by merge, withdraw a stale or
in-flight submission, re-submit, and fix a roster. `MANAGE_XRAS` only,
conditional on the outgoing write lever, never automated.

Its own module rather than more of ``blueprint.py`` (already 2,500 lines), on
that file's existing ``bp``, imported at its foot.

Three things a reader coming from the sibling cards must know
------------------------------------------------------------
**1. These routes write to a system SAM does not own, and the write is done
before the response is.** Nothing here is undone by an exception, a rollback,
or a browser closing. The service layer (``sam.manage.xras_remediation``)
commits its audit row on a private session *before* dispatching, precisely so
the record survives everything the request does not.

**2. ``perform()`` opens ``management_transaction`` and writes nothing to it.**
The handler base wraps every ``perform()`` that way, and these handlers do
their persistence on the service's own connections. So each POST holds an idle
SAM transaction across an HTTP call to ``api.xras.org``. That is accepted
rather than engineered around: the alternative is a second handler base whose
only difference is the missing wrapper, and the calls are single-attempt with a
10 s timeout.

**3. Every modal GET degrades with a 200, not a 4xx.** Remediation needs live
reads — a roster, a person, a preflight — and htmx will not swap a 4xx body
into an already-open modal, so an XRAS outage rendered as an error status is an
empty modal with no explanation. The degraded body says what happened instead.
"""

from __future__ import annotations

from flask import current_app, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.users import User
from sam.integration.xras_api import (
    XrasSourceUnavailable,
    XrasWriteNotConfigured,
    XrasWriteRejected,
    xras_admin_context_available,
    xras_api_configured,
    xras_write_configured,
)
from sam.manage import xras_remediation as remediation
from sam.queries.xras_requests import _as_date, request_index_entry
from sam.schemas.forms import (
    XrasActionDatesForm,
    XrasActionFieldsForm,
    XrasAddActionForm,
    XrasMergeForm,
    XrasRemediationReasonForm,
    XrasRequestAttributesForm,
    XrasResourceAmountForm,
    XrasRoleForm,
)
from sam.schemas.forms.xras_remediation import XRAS_ACTION_TYPES
from webapp.extensions import db
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import htmx_modal_not_found, htmx_success_message
from webapp.utils.rbac import Permission, has_permission, require_permission

from .blueprint import _XRAS_MODAL_TRIGGERS, _parse_activity_window, bp

#: Facet form and swap target — mirrors the sibling cards' pair. Both names are
#: also written into ``xras.html``; a mismatch renders chips that silently do
#: nothing, so they live in one place.
_REMEDIATION_FORM_ID = 'xras-remediation-filters'
_REMEDIATION_TARGET = 'alloc-xras-remediations'

_CARD = 'dashboards/allocations/partials/xras_remediations_card.html'
_MERGE_FORM = 'dashboards/allocations/partials/xras_merge_form.html'
_ACTION_FORM = 'dashboards/allocations/partials/xras_action_form.html'


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

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


def _read_client():
    from sam.integration.xras_api import XrasApiClient
    return XrasApiClient.from_environment()


def _live_request(request_number):
    """Live roster + action states for one request, via the reports family.

    ⚠️ Not ``GET /v1/requests/<id>``, which is 401 for our credential in every
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

    # ⚠️ The project lead is sometimes an unmerged placeholder — measured on 2
    # of 27 live rows the first time this card was pointed at production. That
    # is legitimate as far as XRAS is concerned (the placeholder really does
    # hold the role, so the call authorizes), but the operator is then acting
    # as a throwaway identity that a merge on this very card would delete. It
    # is surfaced rather than worked around: preferring a different role-holder
    # would change who the write is attributed to, silently.
    placeholder = any(r.get('placeholder') and r.get('username') == username
                      for r in roster)
    return username, is_pi, placeholder


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------

@bp.route('/xras_remediations')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_remediations_fragment():
    """HTMX fragment: the Remediations card.

    Read from the sweep's second cache key, never computed here — the
    enumeration behind it is minutes, not an htmx round trip.

    **Four empty states, and collapsing any two would mislead:**

    - *not configured* — the outgoing API is off, so no sweep can run;
    - *no sweep yet* — configured, but nothing has published (the task may be
      disabled, or this is the first hour after a deploy);
    - *snapshot predates the index* — a sweep has published the account
      worklist but not this key, which is precisely the window between
      deploying this code and the first sweep that carries it. Reporting it as
      "nothing to remediate" would be a lie during the one hour an operator is
      most likely to be looking;
    - *published and empty* — a real sweep found nothing, the healthy state.

    Renders with **disabled** controls when the write lever is off rather than
    hiding itself: a card that vanishes teaches nobody that a switch exists.

    Filtering is the chips plus a ``search`` box (``_search``), and the
    controls render whenever anything was swept — including when they have
    hidden every row. Rendering them only alongside rows is the trap: they
    disappear at the moment they empty the card, which is the one moment an
    operator needs to clear them.
    """
    payload = _index()
    configured = xras_api_configured()
    write_enabled = xras_write_configured()

    rows = list(payload.get('rows') or []) if payload else []
    swept_total = len(rows)

    window = _parse_activity_window(request.args)
    rows = [r for r in rows if _in_window(r, window['since'])]
    # Counted BEFORE the chips and the search box, because the header badge it
    # feeds names the date filter specifically. Measured against `swept_total`
    # it would grow every time an operator typed, and blame the window for it.
    window_total = len(rows)

    # Applied before the facets, so every chip counts within the search rather
    # than promising rows the search has already removed.
    search = (request.args.get('search') or '').strip()
    rows = _search(rows, search)

    selected_statuses = [s for s in request.args.getlist('status') if s]
    selected_opportunities = [o for o in request.args.getlist('opportunity') if o]
    selected_push = [p for p in request.args.getlist('push') if p]
    selected_requests = [n for n in request.args.getlist('request_number') if n]

    # Self-excluding facets: each dimension counts over the set filtered by the
    # *other* dimensions, so a chip never shows a zero that its own selection
    # caused and clicking one never empties the row it lives in.
    status_values = _facet(rows, 'status',
                           _apply(rows, opportunities=selected_opportunities,
                                  push=selected_push, requests=selected_requests))
    opportunity_values = _facet(rows, 'opportunity_name',
                                _apply(rows, statuses=selected_statuses,
                                       push=selected_push,
                                       requests=selected_requests))
    push_values = _push_facet(_apply(rows, statuses=selected_statuses,
                                     opportunities=selected_opportunities,
                                     requests=selected_requests))

    rows = _apply(rows, statuses=selected_statuses,
                  opportunities=selected_opportunities,
                  push=selected_push, requests=selected_requests)

    return render_template(
        _CARD,
        groups=_group_by_opportunity(rows),
        total=len(rows),
        swept_total=swept_total,
        window_total=window_total,
        search=search,
        snapshot=payload,
        configured=configured,
        write_enabled=write_enabled,
        # Distinguishes "no sweep at all" from "a sweep that predates this
        # feature" — see the docstring.
        has_worklist=_has_worklist(),
        status_values=status_values,
        opportunity_values=opportunity_values,
        push_values=push_values,
        selected_statuses=selected_statuses,
        selected_opportunities=selected_opportunities,
        selected_push=selected_push,
        selected_requests=selected_requests,
        request_values=[{'value': n, 'label': n, 'count': 1}
                        for n in selected_requests],
        form_id=_REMEDIATION_FORM_ID,
        fragment_url=url_for('allocations_dashboard.xras_remediations_fragment'),
        target_id=_REMEDIATION_TARGET,
    )


def _has_worklist():
    from sam.integration.xras_api.cache import load_pending_worklist
    return load_pending_worklist() is not None


def _in_window(row, since):
    """Keep a row with no submit date.

    Missing information is not evidence of age, and on **this** card an older
    row is the more urgent one — a 2015 approval nobody pushed is the whole
    point — so a date filter may narrow the view but must never silently drop
    the rows the card exists to surface. The header says how many it hid.
    """
    if since is None:
        return True
    submitted = row.get('submit_date')
    if submitted is None:
        return True
    start = since.date() if hasattr(since, 'date') else since
    return submitted >= start


def _search(rows, needle):
    """Free-text narrowing over the fields an operator arrives holding.

    Three of them, and they are one field fewer than they look:

    * the **request number, which is also the projcode** — the sweep resolves
      a handoff by ``Project.projcode == requestNumber``, so ``NCAR4282``
      typed from a ticket and typed from SAM are the same string. There is no
      separate projcode to search;
    * the **project lead**, name or username, because the column shows the
      display name and "Sharma" is what someone remembers;
    * **every roster member**, name or username, because the reason a request
      is on this card is usually one person on its roster — and that person is
      not visible until the row is expanded, which is exactly why searching
      for them has to work from the outside.

    Substring, case-folded, no wildcards: the corpus is ~100 rows in memory
    and anything cleverer would need explaining in the placeholder.
    """
    if not needle:
        return list(rows)
    wanted = needle.casefold()

    def haystack(row):
        yield row.get('request_number')
        pi = row.get('pi') or {}
        yield pi.get('username')
        yield pi.get('name')
        for member in row.get('roster') or ():
            yield member.get('username')
            yield member.get('name')

    return [r for r in rows
            if any(wanted in str(v).casefold() for v in haystack(r) if v)]


def _apply(rows, *, statuses=(), opportunities=(), push=(), requests=()):
    out = list(rows)
    if statuses:
        out = [r for r in out if r.get('status') in statuses]
    if opportunities:
        out = [r for r in out if r.get('opportunity_name') in opportunities]
    if push:
        out = [r for r in out
               if ('pending' if r.get('pending_push') else 'pushed') in push]
    if requests:
        out = [r for r in out if r.get('request_number') in requests]
    return out


def _facet(all_rows, key, scoped_rows):
    counts = {}
    for row in scoped_rows:
        value = row.get(key)
        if value:
            counts[value] = counts.get(value, 0) + 1
    values = {row.get(key) for row in all_rows if row.get(key)}
    return [{'value': v, 'label': v, 'count': counts.get(v, 0)}
            for v in sorted(values)]


def _push_facet(scoped_rows):
    counts = {'pending': 0, 'pushed': 0}
    for row in scoped_rows:
        counts['pending' if row.get('pending_push') else 'pushed'] += 1
    return [{'value': 'pending', 'label': 'No SAM project', 'count': counts['pending']},
            {'value': 'pushed', 'label': 'Project exists', 'count': counts['pushed']}]


def _group_by_opportunity(rows):
    """Group for the nested table, preserving the sweep's ordering."""
    groups = []
    seen = {}
    for row in rows:
        name = row.get('opportunity_name') or 'Unknown opportunity'
        if name not in seen:
            seen[name] = {'name': name, 'opportunity_id': row.get('opportunity_id'),
                          'rows': []}
            groups.append(seen[name])
        seen[name]['rows'].append(row)
    return groups


# ---------------------------------------------------------------------------
# handler base
# ---------------------------------------------------------------------------

class _XrasRemediationHandler(HtmxFormHandler):
    """Shared plumbing for every remediation POST.

    ``exception_map`` translates the three ways an XRAS write fails into copy an
    operator can act on. The distinctions are not cosmetic:

    * **not configured** — a switch is off; nothing was attempted.
    * **rejected** — XRAS understood and refused. A 401 means the impersonated
      user holds no role on that request; a 400 carries XRAS's own validation
      errors. Retrying identically cannot help.
    * **unavailable** — we could not ask, or could not tell. The write may have
      landed; the audit row says which, and it exists either way.
    """

    error_prefix = 'XRAS remediation failed'
    exception_map = (
        (XrasWriteNotConfigured,
         'XRAS writes are switched off for this deployment '
         '(XRAS_WRITE_ENABLED). Nothing was sent.'),
        # A 400 carries XRAS's own errors[] — the single most actionable thing
        # about a rejection, so it is rendered, not just audited.
        (XrasWriteRejected, lambda e: 'XRAS refused this: {}{}'.format(
            e, ' — ' + '; '.join(str(m) for m in e.errors) if e.errors else '')),
        (XrasSourceUnavailable,
         'XRAS could not be reached. If a write had already been sent it is '
         'recorded in the remediation log — check there before retrying.'),
    )

    def triggers(self, result):
        return _XRAS_MODAL_TRIGGERS

    def detail(self, result):
        """Say so when the card could not be refreshed.

        The write is done and verified by this point; only the *view* lags. An
        operator who is not told will re-click, and on this card a re-click is
        a second production write.
        """
        if result is not None and not getattr(result, 'patched', True):
            return ('The card may lag until the next hourly sweep — the write '
                    'itself is recorded.')
        return None

    def _finish(self, outcome, *, verb):
        """Turn a service outcome into a response, or raise for the error map."""
        if outcome.status == 'rejected':
            # The service parks the original exception on `result`, and its
            # errors[] must survive the re-raise or the exception_map renders
            # a refusal with none of XRAS's reasons in it.
            raise XrasWriteRejected(
                outcome.error or 'refused',
                status=getattr(outcome.result, 'status', 0) or 0,
                errors=getattr(outcome.result, 'errors', None))
        if outcome.status == 'error':
            raise XrasSourceUnavailable(outcome.error or 'unavailable')
        if outcome.status == 'unverified':
            # ⚠️ Not an error and not a success. XRAS answered, the re-read did
            # not confirm it, and an operator has to go and look — so this must
            # not render as a green tick.
            raise FormError(
                f'{verb} was sent but could NOT be verified. Check XRAS '
                'directly before retrying — the attempt is recorded in the '
                'remediation log.')
        return outcome


# ---------------------------------------------------------------------------
# merge — a person operation
# ---------------------------------------------------------------------------

def _merge_candidates(person, *, source_username):
    """Real XRAS identities this placeholder might be, best first.

    Searched by **email first, then surname**, and ranked email → organization →
    name. That order is the whole safety property, measured on real data: one
    human had two live identities differing only in email and organization (a
    university address and an NCAR-staff one), and a name match picks between
    them arbitrarily. Merge deletes the loser.

    Intersected with SAM's ``users`` so the modal can say whether the target has
    a SAM account — which decides whether the handoff can proceed after the
    merge or the person still needs an account created.
    """
    client = _read_client()
    email = (person or {}).get('email') or ''
    surname = (person or {}).get('lastName') or ''
    organization = ((person or {}).get('organization') or '').strip().casefold()

    found = {}
    for query in [q for q in (email, surname) if q]:
        for row in (client.search_people(query) or ()):
            if not isinstance(row, dict):
                continue
            username = (row.get('username') or '').strip()
            if username and username != source_username:
                found.setdefault(username, row)

    candidates = []
    for username, row in found.items():
        row_email = (row.get('email') or '').strip().casefold()
        row_org = (row.get('organization') or '').strip().casefold()
        if email and row_email == email.strip().casefold():
            rank, why = 0, 'email matches exactly'
        elif organization and row_org == organization:
            rank, why = 1, 'organization matches'
        else:
            rank, why = 2, 'name only — check the email and organization'
        sam_user = User.get_by_username(db.session, username)
        candidates.append({
            'username': username, 'rank': rank, 'why': why,
            'name': ' '.join(x for x in [row.get('firstName'),
                                         row.get('lastName')] if x),
            'email': row.get('email'),
            'organization': row.get('organization'),
            'in_sam': sam_user is not None,
            'sam_active': bool(sam_user is not None and sam_user.is_active),
        })
    candidates.sort(key=lambda c: (c['rank'], c['username']))
    return candidates


@bp.route('/xras_merge_form/<path:username>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_merge_form(username: str):
    """Modal body: the assisted merge decision.

    Deliberately mirrors the XRAS admin Reconcile-User screen — an operator who
    knows that screen reads this one for free.
    """
    try:
        person = _read_client().get_person(username)
        if person is None:
            # Not an error: the placeholder is already gone from XRAS and this
            # row is a stale echo of a username that no longer exists.
            return _degraded(
                f'{username} no longer exists in XRAS — it has already been '
                'merged away. This row is a stale echo and will clear on the '
                'next sweep.', title='Already merged')
        candidates = _merge_candidates(person, source_username=username)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras merge form: %s', exc)
        return _degraded(
            'Resolving an identity requires a live read from XRAS, and XRAS '
            'is not answering. Nothing can proceed from cached data here.')

    return render_template(
        _MERGE_FORM, username=username, person=person, candidates=candidates,
        write_enabled=xras_write_configured(),
        post_url=url_for('allocations_dashboard.xras_merge', username=username),
    )


class _XrasMergeHandler(_XrasRemediationHandler):
    """Merge a placeholder into a real identity. One-way."""

    schema_cls = XrasMergeForm
    template = _MERGE_FORM
    success_message = 'Merged in XRAS.'

    def clean(self, data):
        target = data['target_username']
        # Casefolded: XRAS matches usernames case-insensitively (the people
        # cache casefolds its keys for exactly this reason), so a case-variant
        # of the source is the same identity and would be a self-merge.
        if target.strip().casefold() == self.username.strip().casefold():
            raise FormError('The target must be a different account.')

        # ⚠️ **Fail closed.** The API documents merge as "merge a username into
        # an existing/new username" — a typo does not fail, it CREATES an
        # identity and hands it the placeholder's roles. So the target is
        # resolved server-side before anything is sent, whichever field named
        # it. The client checks this too; both, because the cost of being wrong
        # is unrecoverable and the check is one GET.
        try:
            if _read_client().get_person(target) is None:
                raise FormError(
                    f'No XRAS account named "{target}". Merging into a name '
                    'that does not exist would CREATE it and move this '
                    "placeholder's roles onto it.")
        except XrasSourceUnavailable:
            raise FormError('Could not confirm the target account with XRAS, '
                            'so nothing was merged. Try again shortly.')
        return data

    def perform(self, data):
        outcome = remediation.merge_placeholder(
            _session_factory(), source_username=self.username,
            target_username=data['target_username'],
            operator=current_user.username, comment=data.get('comment'))
        self._target = data['target_username']
        return self._finish(outcome, verb='The merge')

    def detail(self, result):
        base = super().detail(result)
        note = (f'{self.username} was deleted in XRAS; its roles are now on '
                f'{self._target}. XRAS will send the real username from now on.')
        return f'{note} {base}' if base else note

    def context(self):
        """Rebuild the modal for an error re-render — it needs its candidates."""
        try:
            person = _read_client().get_person(self.username)
            candidates = _merge_candidates(person, source_username=self.username)
        except XrasSourceUnavailable:
            person, candidates = None, []
        return {'username': self.username, 'person': person,
                'candidates': candidates,
                'write_enabled': xras_write_configured(),
                'post_url': url_for('allocations_dashboard.xras_merge',
                                    username=self.username)}

    def render_errors(self, errors, field_errors=None):
        """Reroute the hidden picker's errors to the panel.

        ``target_username`` is a hidden value written by the FK picker and
        ``candidate`` is a radio group — neither has a visible input the
        per-field macros can attach a message to, so an error left on them
        renders nowhere and reads as a form that ignored the click.
        """
        field_errors = dict(field_errors or {})
        for hidden in ('candidate', 'target_username'):
            for message in field_errors.pop(hidden, []):
                errors = list(errors) + [message]
        return super().render_errors(errors, field_errors)


@bp.route('/xras_merge/<path:username>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_merge(username: str):
    return _XrasMergeHandler(username=username).handle()


# ---------------------------------------------------------------------------
# withdraw / re-submit — request operations
# ---------------------------------------------------------------------------

def _action_context(request_number, action_id, *, mode):
    """Live state for one action, or ``None`` when XRAS cannot be read.

    Returns the context both action modals render from. The *live* read is the
    authority — the snapshot decided which button to draw, this decides whether
    the button may still be pressed.
    """
    payload = _live_request(request_number)
    if payload is None:
        return None

    entry = _entry(request_number)
    actions = [a for a in (payload.get('actions') or ())
               if isinstance(a, dict) and a.get('actionId') == action_id]
    action = actions[0] if actions else None
    xa_user, is_pi, xa_placeholder = _impersonation(entry, live=payload)

    return {
        'request_number': request_number,
        'request_id': payload.get('requestId'),
        'request_status': payload.get('requestStatus'),
        'action': action,
        'action_id': action_id,
        'action_count': len(payload.get('actions') or ()),
        'xa_user': xa_user,
        'xa_user_is_pi': is_pi,
        'xa_user_is_placeholder': xa_placeholder,
        'mode': mode,
        # Always seeded, even for modes that never run a preflight: the
        # template branches on `validation is none`, and a missing key is
        # jinja2.Undefined — for which that test is False and the next
        # attribute access raises. The resubmit route overwrites this when it
        # actually preflights.
        'validation': None,
        'write_enabled': xras_write_configured(),
    }


@bp.route('/xras_withdraw_form/<path:request_number>/<int:action_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_withdraw_form(request_number: str, action_id: int):
    """Modal body: confirm a withdrawal, with a required reason.

    The copy has to be honest about a verb that sounds lighter than it is:
    withdrawal **de-approves** the action back to a draft and rewrites the XRAS
    record so the history no longer shows an approval. It is reversible — a PI,
    or the Re-submit button here, can send it back — but it is not archival and
    it is not a delete, and "close this request" is what an operator will think
    they are doing.
    """
    try:
        context = _action_context(request_number, action_id, mode='withdraw')
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras withdraw form: %s', exc)
        return _degraded('Withdrawing needs a live read of the request from '
                         'XRAS, and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    if context['action'] is None:
        return _degraded(
            f'XRAS no longer lists action {action_id} on {request_number}. '
            'The card will catch up on the next sweep.', title='Action not found')

    return render_template(
        _ACTION_FORM, **context,
        post_url=url_for('allocations_dashboard.xras_withdraw',
                         request_number=request_number, action_id=action_id))


class _XrasWithdrawHandler(_XrasRemediationHandler):
    """De-approve one action. Reason required."""

    schema_cls = XrasRemediationReasonForm
    template = _ACTION_FORM
    success_message = 'Withdrawn in XRAS.'

    def clean(self, data):
        # Wrapped like the get_person reads in the sibling handlers: the
        # handler base maps exceptions only around perform(), so an outage
        # escaping clean() would be a 500 htmx never swaps — an empty modal.
        try:
            context = _action_context(self.request_number, self.action_id,
                                      mode='withdraw')
        except XrasSourceUnavailable:
            raise FormError('XRAS could not be reached, so nothing was '
                            'withdrawn. Try again shortly.')
        if context is None or context['action'] is None:
            raise FormError('XRAS no longer lists this action.')
        if not context['xa_user']:
            # Every request-scoped XRAS write authorizes on XA-USER holding a
            # role on that request; with nobody to impersonate the call would
            # 401 and we would have written an audit row for nothing.
            raise FormError('This request has no role-holder for SAM to act '
                            'as, so XRAS would refuse the withdrawal.')
        self._context = context
        return data

    def perform(self, data):
        outcome = remediation.withdraw_action(
            _session_factory(), request_number=self.request_number,
            request_id=self._context['request_id'], action_id=self.action_id,
            pi_username=self._context['xa_user'],
            operator=current_user.username, comment=data['comment'])
        return self._finish(outcome, verb='The withdrawal')

    def detail(self, result):
        base = super().detail(result)
        note = (f"Sent as {self._context['xa_user']}, recorded against "
                f'{current_user.username}. The action is a draft again and can '
                'be re-submitted.')
        return f'{note} {base}' if base else note

    def context(self):
        context = _safe_action_context(self.request_number, self.action_id,
                                       mode='withdraw')
        context['post_url'] = url_for('allocations_dashboard.xras_withdraw',
                                      request_number=self.request_number,
                                      action_id=self.action_id)
        return context


@bp.route('/xras_withdraw/<path:request_number>/<int:action_id>',
          methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_withdraw(request_number: str, action_id: int):
    return _XrasWithdrawHandler(request_number=request_number,
                                action_id=action_id).handle()


def _safe_action_context(request_number, action_id, *, mode):
    """`_action_context` that degrades to a renderable shell on an outage."""
    try:
        context = _action_context(request_number, action_id, mode=mode)
    except XrasSourceUnavailable:
        context = None
    return context or {
        'request_number': request_number, 'request_id': None, 'action': None,
        'action_id': action_id, 'action_count': 0, 'request_status': None,
        'xa_user': None, 'xa_user_is_pi': False,
        'xa_user_is_placeholder': False, 'mode': mode, 'validation': None,
        'write_enabled': xras_write_configured(),
    }


@bp.route('/xras_resubmit_form/<path:request_number>/<int:action_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_resubmit_form(request_number: str, action_id: int):
    """Modal body: the inverse of withdraw, with its preflight rendered first.

    ⚠️ The preflight verdict is a function of **who** we impersonate, not only
    of the action — measured: the same action validated as the PI and failed as
    the Allocation Manager. So the verdict is always rendered next to the user
    it was evaluated as, and a failure disables the button rather than hiding
    the reason.
    """
    try:
        context = _action_context(request_number, action_id, mode='resubmit')
        if context and context['xa_user'] and context['request_id']:
            from sam.integration.xras_api.admin_client import XrasAdminClient
            try:
                admin = XrasAdminClient.from_environment()
                context['validation'] = admin.validate_action(
                    context['request_id'], action_id,
                    xa_user=context['xa_user'])
            except XrasWriteNotConfigured:
                # The lever is off. The modal still renders — with a disabled
                # button and an explanation — because a control that silently
                # vanishes teaches nobody that a switch exists.
                context['validation'] = None
            except XrasWriteRejected as exc:
                # ⚠️ Caught HERE, before the outer XrasSourceUnavailable (its
                # parent class) swallows it as an outage. A 4xx is XRAS
                # refusing deterministically — a 401 means the impersonated
                # user holds no role — and telling the operator "XRAS is not
                # answering, retry later" about a refusal a retry cannot fix
                # is the misleading copy this branch exists to prevent. It
                # renders through the template's failed-preflight branch,
                # beside the user it was evaluated as.
                context['validation'] = {'validation': 'rejected',
                                         'errors': exc.errors or [str(exc)]}
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras resubmit form: %s', exc)
        return _degraded('Re-submitting needs a live read of the request from '
                         'XRAS, and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    if context['action'] is None:
        return _degraded(
            f'XRAS no longer lists action {action_id} on {request_number}.',
            title='Action not found')

    return render_template(
        _ACTION_FORM, **context,
        post_url=url_for('allocations_dashboard.xras_resubmit',
                         request_number=request_number, action_id=action_id))


@bp.route('/xras_resubmit/<path:request_number>/<int:action_id>',
          methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_resubmit(request_number: str, action_id: int):
    """Re-submit a drafted action. Bodiless — no schema, by design.

    There is nothing to validate in the body: the target is in the URL, and a
    schema with no fields would be furniture. The precedent is the one-click
    actions on the sibling card. What *does* get validated is the action
    itself, by XRAS, before the submit — and a failure comes back as the
    rejection copy with XRAS's own reasons in it.
    """
    context = _safe_action_context(request_number, action_id, mode='resubmit')
    if context['action'] is None:
        return ('<div class="alert alert-warning mb-0">XRAS no longer lists '
                'this action.</div>', 200)
    if not context['xa_user']:
        return ('<div class="alert alert-warning mb-0">This request has no '
                'role-holder for SAM to act as, so XRAS would refuse the '
                're-submission.</div>', 200)

    try:
        outcome = remediation.resubmit_action(
            _session_factory(), request_number=request_number,
            request_id=context['request_id'], action_id=action_id,
            pi_username=context['xa_user'], operator=current_user.username)
    except XrasWriteNotConfigured:
        return ('<div class="alert alert-warning mb-0">XRAS writes are '
                'switched off for this deployment. Nothing was sent.</div>', 200)

    if outcome.status != 'verified':
        from markupsafe import escape
        reason = outcome.error or 'XRAS did not confirm it'
        # A 400's errors[] ride on the parked exception — render them, they
        # are the actionable part of a validation refusal. Escaped: this is a
        # raw-string response, so Jinja's autoescape never sees it.
        extra = getattr(outcome.result, 'errors', None)
        if extra:
            reason += ' — ' + '; '.join(str(m) for m in extra)
        return (f'<div class="alert alert-danger mb-0">Re-submission did not '
                f'complete: {escape(reason)}. The '
                f'attempt is recorded in the remediation log.</div>', 200)

    detail = (f"Sent as {context['xa_user']}. It is now under review in XRAS.")
    if not outcome.patched:
        detail += ' The card may lag until the next hourly sweep.'
    return htmx_success_message(_XRAS_MODAL_TRIGGERS,
                                f'Re-submitted action {action_id}.',
                                detail=detail)


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

def _roles_context(request_number):
    """Live roster for the roles modal, or ``None`` if the request is gone."""
    payload = _live_request(request_number)
    if payload is None:
        return None

    from sam.queries.xras_requests import roster_from_payload

    roster = roster_from_payload(payload)
    xa_user, is_pi, xa_placeholder = _impersonation(_entry(request_number),
                                                    live=payload)
    return {
        'request_number': request_number,
        'request_id': payload.get('requestId'),
        'request_status': payload.get('requestStatus'),
        'roster': roster,
        'xa_user': xa_user,
        'xa_user_is_pi': is_pi,
        'xa_user_is_placeholder': xa_placeholder,
        'roles': remediation.role_choices(),
        'role_options': _role_options(),
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_role_add',
                            request_number=request_number),
    }


def _role_options():
    """(wire name, display label) pairs for the role select.

    Built here rather than in the template because Jinja has no list
    comprehension — and the pairing matters: the **name** goes on the wire and
    the **display** is XRAS's own operator vocabulary, so an operator reading
    SAM and the XRAS admin app sees one word for one thing.
    """
    return [(r['name'], r['display']) for r in remediation.role_choices()]


class _XrasRoleAddHandler(_XrasRemediationHandler):
    """Put one username on a request's roster.

    Unlike every other handler here, success **re-renders the roster and leaves
    the modal open**: roster fixes come in batches (add the new admin, remove
    the departed one), and closing after each would make the common case four
    clicks longer for no reason.
    """

    schema_cls = XrasRoleForm
    # `on_success`/`render_errors` are both overridden below to re-render the
    # whole detail modal (the roster editor is inline in it now), so the base's
    # `template` renderer is never reached.
    success_message = 'Role added in XRAS.'

    def clean(self, data):
        # Wrapped for the same reason as the withdraw handler's context read:
        # exception_map covers perform() only, and an outage here must degrade
        # to an inline error, not a 500 htmx will not swap.
        try:
            context = _roles_context(self.request_number)
        except XrasSourceUnavailable:
            raise FormError('XRAS could not be reached, so nothing was '
                            'changed. Try again shortly.')
        if context is None:
            raise FormError('XRAS no longer lists this request.')
        if not context['xa_user']:
            raise FormError('This request has no role-holder for SAM to act '
                            'as, so XRAS would refuse the change.')

        # ⚠️ **Resolve the username first.** The add route accepts optional
        # person parameters that XRAS uses to CREATE an unknown user, with
        # `isReconciled` defaulting true — the exact mechanism that mints the
        # stuck placeholders this card exists to clean up. SAM never sends
        # those parameters, but an unknown username is still worth refusing
        # here rather than discovering later.
        try:
            if _read_client().get_person(data['username']) is None:
                raise FormError(
                    f"No XRAS account named \"{data['username']}\". Adding an "
                    'unknown username can create a new XRAS identity rather '
                    'than adding the person you meant.')
        except XrasSourceUnavailable:
            raise FormError('Could not confirm that account with XRAS, so '
                            'nothing was changed. Try again shortly.')

        already = [r for r in context['roster']
                   if (r.get('username') or '').casefold()
                   == data['username'].casefold()
                   and r.get('role_type') == data['role_type']]
        if already:
            raise FormError(f"{data['username']} already holds "
                            f"{data['role_type']} on this request.")

        self._context = context
        return data

    def perform(self, data):
        outcome = remediation.change_role(
            _session_factory(), add=True, request_number=self.request_number,
            request_id=self._context['request_id'], username=data['username'],
            operator=current_user.username, xa_user=self._context['xa_user'],
            role=data['role_type'], comment=data.get('comment'))
        return self._finish(outcome, verb='The role change')

    def on_success(self, result):
        """Re-render the detail modal in place with a flash; keep it open —
        roster fixes come in batches and closing after each would cost clicks."""
        return _render_detail(
            self.request_number,
            flash=(f'Added {result.result.extra.get("username")} as '
                   f'{result.result.extra.get("role_type")}.'))

    def render_errors(self, errors, field_errors=None):
        """The add-role form is inline in the detail modal, which carries no
        per-field macros, so collapse every error into a top-of-modal alert."""
        messages = list(errors or [])
        for msgs in (field_errors or {}).values():
            messages.extend(msgs)
        return _render_detail(
            self.request_number,
            flash_error=' '.join(str(m) for m in messages)
            or 'Could not add the role.')


def _safe_roles_context(request_number):
    try:
        context = _roles_context(request_number)
    except XrasSourceUnavailable:
        context = None
    return context or {
        'request_number': request_number, 'request_id': None, 'roster': [],
        'request_status': None, 'xa_user': None, 'xa_user_is_pi': False,
        'xa_user_is_placeholder': False,
        'roles': remediation.role_choices(),
        'role_options': _role_options(),
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_role_add',
                            request_number=request_number),
    }


@bp.route('/xras_role_add/<path:request_number>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_role_add(request_number: str):
    return _XrasRoleAddHandler(request_number=request_number).handle()


@bp.route('/xras_role_remove/<path:request_number>/<int:role_id>',
          methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_role_remove(request_number: str, role_id: int):
    """Take one role off the roster. Bodiless; confirmed with ``hx-confirm``.

    Keyed on ``role_id`` rather than username because one person can hold two
    roles on a request and only the id says which one goes.
    """
    # The roster editor is inline in the detail modal, so every outcome
    # re-renders that modal in place (with a flash), not a separate roles view.
    context = _safe_roles_context(request_number)
    if not context['xa_user'] or context['request_id'] is None:
        return _render_detail(
            request_number,
            flash_error='XRAS could not be read, so nothing was changed.')

    target = next((r for r in context['roster'] if r.get('role_id') == role_id),
                  None)
    if target is None:
        return _render_detail(
            request_number, flash_error='That role is no longer on the roster.')

    try:
        outcome = remediation.change_role(
            _session_factory(), add=False, request_number=request_number,
            request_id=context['request_id'], username=target.get('username'),
            operator=current_user.username, xa_user=context['xa_user'],
            role_id=role_id)
    except XrasWriteNotConfigured:
        return _render_detail(
            request_number,
            flash_error='XRAS writes are switched off for this deployment. '
                        'Nothing was sent.')

    if outcome.status == 'verified':
        return _render_detail(
            request_number,
            flash=f"Removed {target.get('username')} ({target.get('role_type')}).")
    reason = outcome.error or 'XRAS did not confirm it'
    return _render_detail(
        request_number,
        flash_error=f'Removal did not complete: {reason}. The attempt is '
                    'recorded in the remediation log.')


# ---------------------------------------------------------------------------
# read-only request detail (Part A) — the surface future editors hang off
# ---------------------------------------------------------------------------

_DETAIL_FORM = 'dashboards/allocations/partials/xras_request_detail.html'
_RESOURCE_FORM = 'dashboards/allocations/partials/xras_resource_form.html'
_DATES_FORM = 'dashboards/allocations/partials/xras_dates_form.html'
_ATTRIBUTES_FORM = 'dashboards/allocations/partials/xras_attributes_form.html'
# ⚠️ NOT xras_action_form.html — that is the withdraw/re-submit form.
_ACTION_FIELDS_FORM = 'dashboards/allocations/partials/xras_action_fields_form.html'
_ADD_ACTION_FORM = 'dashboards/allocations/partials/xras_add_action_form.html'

#: The XRAS "stage" model, in the order the modal shows it: what was asked for,
#: what the panel recommended, what was awarded. Every ``resources[]`` entry in
#: a ``reports/request_numbers`` payload carries one of these as its ``type``,
#: and a resource appears once per stage — so grouping by stage is what makes
#: requested-vs-awarded legible at a glance.
_RESOURCE_STAGES = ('Requested', 'Recommended', 'Approved')


def _detail_actions(payload):
    """Per-action view model for the detail modal: a resource × stage matrix.

    Resources are **pivoted** — one row per resource, one column per stage that
    appears — so a resource present at several stages (the common case:
    Requested + Approved) is a single row with a cell per stage, rather than the
    same name repeated down three stacked stage lists. ``stages_present`` is the
    ordered subset of stages actually seen (Requested / Recommended / Approved,
    then a trailing ``Other`` for any unrecognised ``type``), so the template
    renders only the columns that exist. ``units`` is carried once per row (the
    same resource keeps its units across stages), which is what lets the stage
    columns hold a bare number.

    Built in Python because the pivot needs first-seen row order and a fixed
    column order together, which Jinja's ``groupby`` cannot express.
    """
    actions = []
    for action in payload.get('actions') or ():
        if not isinstance(action, dict):
            continue
        column_order = list(_RESOURCE_STAGES) + ['Other']
        present = {stage: False for stage in column_order}
        rows_by_key, row_order = {}, []
        for res in action.get('resources') or ():
            if not isinstance(res, dict):
                continue
            raw = res.get('type')
            stage = raw if raw in _RESOURCE_STAGES else 'Other'
            rid = res.get('resourceId')
            name = (res.get('displayResourceName') or res.get('resourceName')
                    or (('resource ' + str(rid)) if rid is not None
                        else 'resource'))
            # Key on the resource-type id (unique per resource); fall back to
            # the name only when a payload omits the id.
            key = rid if rid is not None else name
            row = rows_by_key.get(key)
            if row is None:
                row = {'resource_id': rid, 'name': name,
                       'units': res.get('resourceUnits') or '', 'cells': {}}
                rows_by_key[key] = row
                row_order.append(key)
            if not row['units']:
                row['units'] = res.get('resourceUnits') or ''
            row['cells'][stage] = {'amount': res.get('amount'),
                                   'comments': res.get('comments')}
            present[stage] = True
        stages_present = [s for s in column_order if present[s]]
        resource_rows = [rows_by_key[k] for k in row_order]
        for row in resource_rows:
            # Comments are rare and per (resource × stage); collect the
            # non-empty ones so the template can surface them under the row.
            row['comments'] = [(s, row['cells'][s]['comments'])
                               for s in column_order
                               if row['cells'].get(s)
                               and row['cells'][s].get('comments')]
        actions.append({
            'action_id': action.get('actionId'),
            'action_type': action.get('actionType'),
            'action_status': action.get('actionStatus'),
            'user_comments': action.get('userComments'),
            'stages_present': stages_present,
            'resource_rows': resource_rows,
            # Dates arrive as raw ISO strings; parse to date objects here (the
            # same parser the entry builder uses) so the template can fmt_date
            # them — fmt_date raises on a str. `allocation_date_id` is carried
            # through so the edit/remove editors can target one range.
            'dates': [{'allocation_date_id': d.get('allocationDateId'),
                       'begin': _as_date(d.get('beginDate')),
                       'end': _as_date(d.get('endDate')),
                       'type': d.get('type')}
                      for d in (action.get('allocationDates') or ())
                      if isinstance(d, dict)],
            'documents': [d for d in (action.get('documents') or ())
                          if isinstance(d, dict)],
        })
    return actions


def _detail_grants(payload):
    """Grants with their raw ISO dates parsed for ``fmt_date``."""
    grants = []
    for g in payload.get('grants') or ():
        if not isinstance(g, dict):
            continue
        grants.append({**g,
                       'begin': _as_date(g.get('beginDate')),
                       'end': _as_date(g.get('endDate'))})
    return grants


def _detail_context(request_number, *, flash=None, flash_error=None):
    """Everything the detail modal renders, or ``None`` if the request is gone.

    Shared by the read-only GET and by every editor's success re-render, so the
    modal looks identical however it was reached. Raises
    :class:`XrasSourceUnavailable` on an outage — the caller degrades.
    """
    payload = _live_request(request_number)
    if payload is None:
        return None

    entry = _entry(request_number)
    # Reproduces the exact `row` shape the shared include expects (roster +
    # actions with can_withdraw/can_resubmit), so the modal's buttons are
    # identical to the card's by construction. `pending_push` only feeds the
    # card's SAM badge, which the include does not render — default it safely.
    row = request_index_entry(
        payload, pending_push=bool((entry or {}).get('pending_push', True)))
    xa_user, is_pi, placeholder = _impersonation(entry, live=payload)

    return {
        'request_number': request_number,
        'payload': payload,
        'row': row,
        'detail_actions': _detail_actions(payload),
        'grants': _detail_grants(payload),
        'xa_user': xa_user,
        'xa_user_is_pi': is_pi,
        'xa_user_is_placeholder': placeholder,
        'write_enabled': xras_write_configured(),
        # Approved/Recommended editors render disabled until the elevated XRAS
        # key lands (Phase 0.5); this is the flip-point flag.
        'admin_context_available': xras_admin_context_available(),
        # The destructive lifecycle buttons render only for ADMIN_XRAS holders
        # (Part C) — effectively SYSTEM_ADMIN. A MANAGE_XRAS operator never sees
        # them, and the routes 403 anyway.
        'is_xras_admin': has_permission(current_user, Permission.ADMIN_XRAS),
        'action_types': list(XRAS_ACTION_TYPES),
        # The roster editor is inline in the modal now (no separate Roles form):
        # `row.roster` already carries `role_id` (roster_from_payload), and these
        # two feed the add-role control below it.
        'role_options': _role_options(),
        'role_add_url': url_for('allocations_dashboard.xras_role_add',
                                request_number=request_number),
        'flash': flash,
        'flash_error': flash_error,
    }


@bp.route('/xras_request_detail/<path:request_number>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_request_detail(request_number: str):
    """Modal body: the full detail of one request, read-only plus the editors.

    Renders resources grouped by XRAS stage (Requested / Recommended /
    Approved) so requested-vs-awarded is visible, the rich request sections
    (abstract, FoS, grants, documents), and — via the shared
    ``_xras_remediation_actions`` include — the roster and write buttons. This
    modal is the single opener the Remediations card's Request cell links to
    (the old per-request expansion was folded into it). The Requested-stage
    rows carry the amount/date editors (Part B); the Approved editors render
    fail-visible until the elevated key lands.

    Degrades with a **200** on an XRAS outage, like every modal GET here: htmx
    will not swap a 4xx into an already-open modal.
    """
    try:
        context = _detail_context(request_number)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras request detail: %s', exc)
        return _degraded('Showing this request needs a live read from XRAS, '
                         'and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    return render_template(_DETAIL_FORM, **context)


def _render_detail(request_number, *, flash=None, flash_error=None):
    """Re-render the detail modal in place after a write, with a flash.

    The write is already done, verified and recorded by the time this runs, so
    an outage here only costs the operator a fresh view — the change stands.
    """
    try:
        context = _detail_context(request_number, flash=flash,
                                  flash_error=flash_error)
    except XrasSourceUnavailable:
        context = None
    if context is None:
        note = flash or flash_error or 'Done.'
        cls = 'alert-danger' if flash_error else 'alert-success'
        return (f'<div class="alert {cls} mb-0">{note} '
                'The card may lag until the next hourly sweep.</div>', 200)
    response = current_app.make_response(render_template(_DETAIL_FORM, **context))
    response.headers['HX-Trigger'] = 'refreshXrasTab'
    return response


# ── the request editor (Part B): resource amounts & allocation dates ─────

def _editor_target(request_number):
    """``(request_id, xa_user)`` for a write, or raise :class:`FormError`.

    Wrapped like the withdraw/role handlers' live reads: the handler base maps
    exceptions only around ``perform()``, so an outage in ``clean()`` must
    degrade to an inline error rather than a 500 htmx never swaps.
    """
    try:
        payload = _live_request(request_number)
    except XrasSourceUnavailable:
        raise FormError('XRAS could not be reached, so nothing was changed. '
                        'Try again shortly.')
    if payload is None:
        raise FormError('XRAS no longer lists this request.')
    xa_user, _, _ = _impersonation(_entry(request_number), live=payload)
    if not xa_user:
        raise FormError('This request has no role-holder for SAM to act as, so '
                        'XRAS would refuse the change.')
    return payload.get('requestId'), xa_user


def _find_resource(payload, action_id, resource_id, stage):
    """The resource dict for ``(action, resourceId, stage)``, or any-stage row.

    Returns ``(row_for_stage, any_row)`` — the first prefills the amount editor
    for that stage, the second supplies the label/units when the stage has no
    line yet (the add case).
    """
    stage_row = any_row = None
    for action in (payload.get('actions') or ()):
        if not isinstance(action, dict) or action.get('actionId') != action_id:
            continue
        for res in (action.get('resources') or ()):
            if not isinstance(res, dict) or res.get('resourceId') != resource_id:
                continue
            any_row = any_row or res
            if res.get('type') == stage:
                stage_row = res
    return stage_row, any_row


def _resource_form_context(request_number, action_id, resource_id, stage):
    """Context for the amount editor modal, or ``None`` if the request is gone."""
    payload = _live_request(request_number)
    if payload is None:
        return None
    stage_row, any_row = _find_resource(payload, action_id, resource_id, stage)
    label_row = stage_row or any_row or {}
    return {
        'request_number': request_number,
        'action_id': action_id,
        'resource_id': resource_id,
        'stage': stage,
        'is_award': stage != 'Requested',
        'resource_name': (label_row.get('displayResourceName')
                          or label_row.get('resourceName')
                          or f'resource {resource_id}'),
        'resource_units': label_row.get('resourceUnits') or '',
        'current_amount': (stage_row or {}).get('amount'),
        'current_comment': (stage_row or {}).get('comments'),
        'write_enabled': xras_write_configured(),
        'admin_context_available': xras_admin_context_available(),
        'post_url': url_for('allocations_dashboard.xras_resource_edit',
                            request_number=request_number, action_id=action_id,
                            resource_id=resource_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


def _safe_resource_form_context(request_number, action_id, resource_id, stage):
    try:
        context = _resource_form_context(request_number, action_id,
                                         resource_id, stage)
    except XrasSourceUnavailable:
        context = None
    return context or {
        'request_number': request_number, 'action_id': action_id,
        'resource_id': resource_id, 'stage': stage,
        'is_award': stage != 'Requested', 'resource_name': f'resource {resource_id}',
        'resource_units': '', 'current_amount': None, 'current_comment': None,
        'write_enabled': xras_write_configured(),
        'admin_context_available': xras_admin_context_available(),
        'post_url': url_for('allocations_dashboard.xras_resource_edit',
                            request_number=request_number, action_id=action_id,
                            resource_id=resource_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


@bp.route('/xras_resource_form/<path:request_number>/<int:action_id>'
          '/<int:resource_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_resource_form(request_number: str, action_id: int, resource_id: int):
    """Modal body: edit one resource's amount for a stage."""
    stage = request.args.get('stage') or 'Requested'
    try:
        context = _resource_form_context(request_number, action_id,
                                         resource_id, stage)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras resource form: %s', exc)
        return _degraded('Editing an amount needs a live read from XRAS, and '
                         'XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    return render_template(_RESOURCE_FORM, **context)


class _XrasResourceAmountHandler(_XrasRemediationHandler):
    """Set one resource's amount. Requested stage on our key; Approved gated."""

    schema_cls = XrasResourceAmountForm
    template = _RESOURCE_FORM
    success_message = 'Amount updated in XRAS.'

    def clean(self, data):
        if self.stage != 'Requested' and not xras_admin_context_available():
            raise FormError('Editing the awarded amount needs an elevated XRAS '
                            'key, which this deployment does not have yet.')
        self._request_id, self._xa_user = _editor_target(self.request_number)
        return data

    def perform(self, data):
        context = 'admin' if self.stage != 'Requested' else 'submit'
        outcome = remediation.update_resource_amount(
            _session_factory(), request_number=self.request_number,
            request_id=self._request_id, action_id=self.action_id,
            resource_id=self.resource_id, amount=data['amount'],
            pi_username=self._xa_user, operator=current_user.username,
            comment=data.get('comment'), context=context)
        return self._finish(outcome, verb='The amount change')

    def on_success(self, result):
        return _render_detail(
            self.request_number,
            flash=f'Set the {self.stage.lower()} amount for resource '
                  f'{self.resource_id}.')

    def context(self):
        return _safe_resource_form_context(self.request_number, self.action_id,
                                           self.resource_id, self.stage)


@bp.route('/xras_resource_edit/<path:request_number>/<int:action_id>'
          '/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_resource_edit(request_number: str, action_id: int, resource_id: int):
    stage = request.form.get('stage') or 'Requested'
    return _XrasResourceAmountHandler(
        request_number=request_number, action_id=action_id,
        resource_id=resource_id, stage=stage).handle()


@bp.route('/xras_resource_remove/<path:request_number>/<int:action_id>'
          '/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_resource_remove(request_number: str, action_id: int,
                         resource_id: int):
    """Delete one resource's Requested line. Bodiless; confirmed with hx-confirm.

    Requested-only on our current key — the button is offered only on Requested
    rows, and the service refuses anything else structurally (submit context).
    """
    try:
        request_id, xa_user = _editor_target(request_number)
    except FormError as exc:
        return _render_detail(request_number, flash_error=str(exc))

    try:
        outcome = remediation.remove_resource(
            _session_factory(), request_number=request_number,
            request_id=request_id, action_id=action_id,
            resource_id=resource_id, pi_username=xa_user,
            operator=current_user.username)
    except XrasWriteNotConfigured:
        return _render_detail(request_number, flash_error='XRAS writes are '
                              'switched off for this deployment. Nothing was sent.')

    if outcome.status == 'verified':
        return _render_detail(request_number,
                              flash=f'Removed the requested line for resource '
                                    f'{resource_id}.')
    return _render_detail(request_number, flash_error=(
        f'Removal did not complete: {outcome.error or "XRAS did not confirm it"}. '
        'The attempt is recorded in the remediation log.'))


def _dates_form_context(request_number, action_id, allocation_date_id=None):
    """Context for the allocation-dates editor, or ``None`` if the request is gone."""
    payload = _live_request(request_number)
    if payload is None:
        return None
    target = None
    for action in (payload.get('actions') or ()):
        if isinstance(action, dict) and action.get('actionId') == action_id:
            for d in (action.get('allocationDates') or ()):
                if isinstance(d, dict) \
                        and d.get('allocationDateId') == allocation_date_id:
                    target = d
    return {
        'request_number': request_number,
        'action_id': action_id,
        'allocation_date_id': allocation_date_id,
        'begin_date': _as_date((target or {}).get('beginDate')),
        'end_date': _as_date((target or {}).get('endDate')),
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_dates_edit',
                            request_number=request_number, action_id=action_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


def _safe_dates_form_context(request_number, action_id, allocation_date_id=None):
    try:
        context = _dates_form_context(request_number, action_id,
                                      allocation_date_id)
    except XrasSourceUnavailable:
        context = None
    return context or {
        'request_number': request_number, 'action_id': action_id,
        'allocation_date_id': allocation_date_id, 'begin_date': None,
        'end_date': None, 'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_dates_edit',
                            request_number=request_number, action_id=action_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


@bp.route('/xras_dates_form/<path:request_number>/<int:action_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dates_form(request_number: str, action_id: int):
    """Modal body: set or edit an allocation-date range."""
    date_id = request.args.get('allocation_date_id', type=int)
    try:
        context = _dates_form_context(request_number, action_id, date_id)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras dates form: %s', exc)
        return _degraded('Editing allocation dates needs a live read from '
                         'XRAS, and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    return render_template(_DATES_FORM, **context)


class _XrasActionDatesHandler(_XrasRemediationHandler):
    """Create or update one allocation-date range. Requested stage on our key."""

    schema_cls = XrasActionDatesForm
    template = _DATES_FORM
    success_message = 'Allocation dates updated in XRAS.'

    def clean(self, data):
        self._request_id, self._xa_user = _editor_target(self.request_number)
        return data

    def perform(self, data):
        if self.allocation_date_id:
            outcome = remediation.update_action_dates(
                _session_factory(), request_number=self.request_number,
                request_id=self._request_id, action_id=self.action_id,
                allocation_date_id=self.allocation_date_id,
                begin_date=data['begin_date'], end_date=data['end_date'],
                pi_username=self._xa_user, operator=current_user.username,
                comment=data.get('comment'))
        else:
            outcome = remediation.set_action_dates(
                _session_factory(), request_number=self.request_number,
                request_id=self._request_id, action_id=self.action_id,
                begin_date=data['begin_date'], end_date=data['end_date'],
                pi_username=self._xa_user, operator=current_user.username,
                comment=data.get('comment'))
        return self._finish(outcome, verb='The date change')

    def on_success(self, result):
        return _render_detail(self.request_number,
                              flash='Allocation dates updated.')

    def context(self):
        return _safe_dates_form_context(self.request_number, self.action_id,
                                        self.allocation_date_id)


@bp.route('/xras_dates_edit/<path:request_number>/<int:action_id>',
          methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dates_edit(request_number: str, action_id: int):
    date_id = request.form.get('allocation_date_id', type=int)
    return _XrasActionDatesHandler(
        request_number=request_number, action_id=action_id,
        allocation_date_id=date_id).handle()


@bp.route('/xras_dates_remove/<path:request_number>/<int:action_id>'
          '/<int:allocation_date_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dates_remove(request_number: str, action_id: int,
                      allocation_date_id: int):
    """Delete one allocation-date range. Bodiless; confirmed with hx-confirm."""
    try:
        request_id, xa_user = _editor_target(request_number)
    except FormError as exc:
        return _render_detail(request_number, flash_error=str(exc))

    try:
        outcome = remediation.remove_action_dates(
            _session_factory(), request_number=request_number,
            request_id=request_id, action_id=action_id,
            allocation_date_id=allocation_date_id, pi_username=xa_user,
            operator=current_user.username)
    except XrasWriteNotConfigured:
        return _render_detail(request_number, flash_error='XRAS writes are '
                              'switched off for this deployment. Nothing was sent.')

    if outcome.status == 'verified':
        return _render_detail(request_number,
                              flash='Removed the allocation dates.')
    return _render_detail(request_number, flash_error=(
        f'Removal did not complete: {outcome.error or "XRAS did not confirm it"}. '
        'The attempt is recorded in the remediation log.'))


# ── the request editor (Part B2a): request attributes & action fields ────

def _attributes_form_context(request_number):
    """Context for the request-attributes editor, or ``None`` if the request is gone."""
    payload = _live_request(request_number)
    if payload is None:
        return None
    return {
        'request_number': request_number,
        'title': payload.get('title'),
        'short_title': payload.get('shortTitle'),
        'abstract': payload.get('abstract'),
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_attributes_edit',
                            request_number=request_number),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


def _safe_attributes_form_context(request_number):
    try:
        context = _attributes_form_context(request_number)
    except XrasSourceUnavailable:
        context = None
    return context or {
        'request_number': request_number, 'title': None, 'short_title': None,
        'abstract': None, 'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_attributes_edit',
                            request_number=request_number),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


@bp.route('/xras_attributes_form/<path:request_number>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_attributes_form(request_number: str):
    """Modal body: edit the request's title / short title / abstract."""
    try:
        context = _attributes_form_context(request_number)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras attributes form: %s', exc)
        return _degraded('Editing the request attributes needs a live read '
                         'from XRAS, and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    return render_template(_ATTRIBUTES_FORM, **context)


class _XrasAttributesHandler(_XrasRemediationHandler):
    """Set a request's title/shortTitle/abstract — the requested text, not the
    award. Full-form: prefilled with current values, a save writes all three."""

    schema_cls = XrasRequestAttributesForm
    template = _ATTRIBUTES_FORM
    success_message = 'Request attributes updated in XRAS.'

    def clean(self, data):
        self._request_id, self._xa_user = _editor_target(self.request_number)
        return data

    def perform(self, data):
        # snake_case → wire; blanked short_title/abstract clear the field.
        fields = {
            'title': data['title'],
            'shortTitle': data.get('short_title') or '',
            'abstract': data.get('abstract') or '',
        }
        outcome = remediation.update_request_attributes(
            _session_factory(), request_number=self.request_number,
            request_id=self._request_id, fields=fields,
            pi_username=self._xa_user, operator=current_user.username)
        return self._finish(outcome, verb='The attribute change')

    def on_success(self, result):
        return _render_detail(self.request_number,
                              flash='Request attributes updated.')

    def context(self):
        return _safe_attributes_form_context(self.request_number)


@bp.route('/xras_attributes_edit/<path:request_number>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_attributes_edit(request_number: str):
    return _XrasAttributesHandler(request_number=request_number).handle()


def _action_fields_form_context(request_number, action_id):
    """Context for the action-fields editor, or ``None`` if the request is gone."""
    payload = _live_request(request_number)
    if payload is None:
        return None
    action = next((a for a in (payload.get('actions') or ())
                   if isinstance(a, dict) and a.get('actionId') == action_id),
                  None)
    if action is None:
        return {'_missing': True}
    return {
        'request_number': request_number,
        'action_id': action_id,
        'user_comments': action.get('userComments'),
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_action_fields_edit',
                            request_number=request_number, action_id=action_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


def _safe_action_fields_form_context(request_number, action_id):
    try:
        context = _action_fields_form_context(request_number, action_id)
    except XrasSourceUnavailable:
        context = None
    if context and not context.get('_missing'):
        return context
    return {
        'request_number': request_number, 'action_id': action_id,
        'user_comments': None, 'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_action_fields_edit',
                            request_number=request_number, action_id=action_id),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


@bp.route('/xras_action_fields_form/<path:request_number>/<int:action_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_action_fields_form(request_number: str, action_id: int):
    """Modal body: edit an action's user comments."""
    try:
        context = _action_fields_form_context(request_number, action_id)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('xras action fields form: %s', exc)
        return _degraded('Editing the action fields needs a live read from '
                         'XRAS, and XRAS is not answering.')
    if context is None:
        return htmx_modal_not_found('Request')
    if context.get('_missing'):
        return _degraded(
            f'XRAS no longer lists action {action_id} on {request_number}.',
            title='Action not found')
    return render_template(_ACTION_FIELDS_FORM, **context)


class _XrasActionFieldsHandler(_XrasRemediationHandler):
    """Set an action's userComments."""

    schema_cls = XrasActionFieldsForm
    template = _ACTION_FIELDS_FORM
    success_message = 'Action fields updated in XRAS.'

    def clean(self, data):
        self._request_id, self._xa_user = _editor_target(self.request_number)
        return data

    def perform(self, data):
        fields = {'userComments': data.get('user_comments') or ''}
        outcome = remediation.update_action(
            _session_factory(), request_number=self.request_number,
            request_id=self._request_id, action_id=self.action_id,
            fields=fields, pi_username=self._xa_user,
            operator=current_user.username)
        return self._finish(outcome, verb='The action field change')

    def on_success(self, result):
        return _render_detail(self.request_number,
                              flash=f'Updated the comments on action '
                                    f'{self.action_id}.')

    def context(self):
        return _safe_action_fields_form_context(self.request_number,
                                                self.action_id)


@bp.route('/xras_action_fields_edit/<path:request_number>/<int:action_id>',
          methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_action_fields_edit(request_number: str, action_id: int):
    return _XrasActionFieldsHandler(
        request_number=request_number, action_id=action_id).handle()


# ── the destructive lifecycle (Part C — ADMIN_XRAS only) ─────────────────
#
# ⚠️ Every route here is gated on ADMIN_XRAS (effectively SYSTEM_ADMIN), NOT
# MANAGE_XRAS — a full-editor operator cannot reach them. The verbs are
# irreversible in XRAS and were not live-probed; they are fail-visible.

def _inline_alert(message, *, variant='warning'):
    """A raw 200 alert for a bodiless destructive route (Jinja never sees it)."""
    from markupsafe import escape
    return (f'<div class="alert alert-{variant} mb-0">{escape(message)}</div>', 200)


@bp.route('/xras_request_delete/<path:request_number>', methods=['POST'])
@login_required
@require_permission(Permission.ADMIN_XRAS)
def xras_request_delete(request_number: str):
    """Delete a whole request in XRAS. **Irreversible.** Bodiless; hx-confirm.

    On success the request is gone, so the modal cannot re-render it — it closes
    and the card refreshes (the sweep patch drops the now-missing row).
    """
    try:
        request_id, xa_user = _editor_target(request_number)
    except FormError as exc:
        return _inline_alert(str(exc))

    try:
        outcome = remediation.delete_request(
            _session_factory(), request_number=request_number,
            request_id=request_id, pi_username=xa_user,
            operator=current_user.username)
    except XrasWriteNotConfigured:
        return _inline_alert('XRAS writes are switched off for this '
                             'deployment. Nothing was sent.')

    if outcome.status == 'verified':
        return htmx_success_message(
            _XRAS_MODAL_TRIGGERS, f'Deleted request {request_number} in XRAS.',
            detail='It no longer exists in XRAS and drops off the card.')

    reason = outcome.error or 'XRAS did not confirm it'
    extra = getattr(outcome.result, 'errors', None)
    if extra:
        reason += ' — ' + '; '.join(str(m) for m in extra)
    return _inline_alert(
        f'Deletion did not complete: {reason}. The attempt is recorded in the '
        'remediation log.', variant='danger')


@bp.route('/xras_request_renew/<path:request_number>', methods=['POST'])
@login_required
@require_permission(Permission.ADMIN_XRAS)
def xras_request_renew(request_number: str):
    """Spawn a renewal of a request. Bodiless; hx-confirm.

    The original stays, so the detail modal re-renders with a note naming the
    new renewal request.
    """
    try:
        request_id, xa_user = _editor_target(request_number)
    except FormError as exc:
        return _render_detail(request_number, flash_error=str(exc))

    try:
        outcome = remediation.renew_request(
            _session_factory(), request_number=request_number,
            request_id=request_id, pi_username=xa_user,
            operator=current_user.username)
    except XrasWriteNotConfigured:
        return _render_detail(request_number, flash_error='XRAS writes are '
                              'switched off for this deployment. Nothing was sent.')

    if outcome.status == 'verified':
        new_id = getattr(outcome.result, 'extra', {}).get('renewal_request_id')
        return _render_detail(
            request_number,
            flash=f'Renewal spawned in XRAS (requestId {new_id}).')
    return _render_detail(request_number, flash_error=(
        f'Renewal did not complete: {outcome.error or "XRAS did not confirm it"}. '
        'Check XRAS — the attempt is recorded in the remediation log.'))


def _safe_add_action_form_context(request_number):
    return {
        'request_number': request_number,
        # (value, label) pairs for select_field — Jinja has no comprehension.
        'action_type_options': [(t, t) for t in XRAS_ACTION_TYPES],
        'write_enabled': xras_write_configured(),
        'post_url': url_for('allocations_dashboard.xras_add_action',
                            request_number=request_number),
        'back_url': url_for('allocations_dashboard.xras_request_detail',
                            request_number=request_number),
    }


@bp.route('/xras_add_action_form/<path:request_number>')
@login_required
@require_permission(Permission.ADMIN_XRAS)
def xras_add_action_form(request_number: str):
    """Modal body: pick an action type to add. ADMIN_XRAS only."""
    return render_template(_ADD_ACTION_FORM,
                           **_safe_add_action_form_context(request_number))


class _XrasAddActionHandler(_XrasRemediationHandler):
    """Add an action to a request. Destructive-adjacent — ADMIN_XRAS only."""

    schema_cls = XrasAddActionForm
    template = _ADD_ACTION_FORM
    success_message = 'Action added in XRAS.'

    def clean(self, data):
        self._request_id, self._xa_user = _editor_target(self.request_number)
        return data

    def perform(self, data):
        outcome = remediation.add_action(
            _session_factory(), request_number=self.request_number,
            request_id=self._request_id, action_type=data['action_type'],
            pi_username=self._xa_user, operator=current_user.username)
        return self._finish(outcome, verb='Adding the action')

    def on_success(self, result):
        return _render_detail(
            self.request_number,
            flash=f'Added a {result.result.extra.get("action_type")} action.')

    def context(self):
        return _safe_add_action_form_context(self.request_number)


@bp.route('/xras_add_action/<path:request_number>', methods=['POST'])
@login_required
@require_permission(Permission.ADMIN_XRAS)
def xras_add_action(request_number: str):
    return _XrasAddActionHandler(request_number=request_number).handle()
