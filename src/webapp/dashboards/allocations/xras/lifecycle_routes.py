"""XRAS pending-activation lifecycle — the operator write path.

Notify / activate / dismiss / restore / comment on a pending XRAS project, plus
the action-detail and re-check modals. Dashboard routes (session-cookie auth,
CSRF via the hx-headers on ``<body>``), not an API. Moved verbatim out of
``blueprint.py``; the shared constants live in ``_shared``.
"""

from flask import abort, current_app, render_template, request, url_for
from flask_login import current_user, login_required

from webapp.api.xras.recheck import recheck_action
from webapp.extensions import db
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_modal_not_found, htmx_not_found, htmx_success,
    htmx_success_message,
)
from webapp.utils.notify import get_notifier, notify_summary
from webapp.utils.project_permissions import can_edit_project_governance
from webapp.utils.rbac import Permission, has_permission, require_permission
from sam.integration.xras import XrasActivationEvent
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project
from sam.queries.xras_actions import get_recent_xras_actions
from sam.queries.xras_activation import (
    get_latest_xras_action_id,
    get_xras_activation_events,
    get_xras_pending_recipients,
)
# Full dotted path, never through `sam.queries` — that package imports its
# submodules eagerly, and this one imports `sam.notify`. See the module
# docstring; `tests/unit/test_notify_import_graph.py` is the gate.
from sam.queries.xras_notices import build_xras_messages, load_xras_action
from sam.schemas.forms import XrasActivationEventForm

from .. import bp
from ._shared import _XRAS_MODAL_TRIGGERS


# ---------------------------------------------------------------------------
# Pending-activation worklist — the operator write path.
#
# These are dashboard routes, NOT an API: session-cookie auth, CSRF via the
# hx-headers on <body>, and the card's own buttons are the only callers. No
# /api/v1/ or /api/xras/v1/ surface is added; webapp/api/xras/ stays the
# legacy-compat inbound blueprint it is.
#
# WARNING: Every one of these writes runs INSIDE management_transaction, which is the
# OPPOSITE of what webapp/api/xras/recheck.py does one screen away — see the
# docstrings below for why, because the difference is deliberate and a reader
# who has just read recheck.py will expect the other answer.
# ---------------------------------------------------------------------------


def _load_pending_project(project_id):
    """Fetch the project an activation event is about, or None."""
    return db.session.get(Project, project_id)


def _record_activation_event(project, event_type, *, comment=None,
                             notified_to=None, action_log_id=None):
    """Append one operator event, with the prompting action as provenance.

    Runs inside ``management_transaction`` — deliberately unlike
    :func:`webapp.api.xras.recheck.recheck_action`, which commits its audit row on a
    private connection precisely so it survives a handler rollback. Its value is
    "we received this even though processing it blew up".

    An activation event is the inverse: it records an operator's *decision*, and
    if the decision does not apply the record must not survive. Because the card's
    state is **derived** from these events, an ``activated`` row that outlived its
    own effect would make the card go on showing the project as pending while the
    audit says it was activated — exactly the drift the append-only design exists
    to eliminate. Two connections mean two truths; the design's premise is one.
    """
    return XrasActivationEvent.create(
        db.session,
        project_id=project.project_id,
        event_type=event_type,
        created_by=current_user.username,
        comment=comment,
        notified_to=notified_to,
        # `action_log_id` names the action the operator acted on. It defaults
        # to the newest, which is right for Activate/Dismiss/Restore — those
        # are about the project's current situation. Notify passes one
        # explicitly, because working through a backlog means reporting an
        # older outcome and the timeline has to say which.
        xras_action_log_id=(action_log_id if action_log_id is not None
                            else get_latest_xras_action_id(
                                db.session, project.project_id)),
    )


def _xras_messages(project, people, *, action=None):
    """The route's binding of :func:`~sam.queries.xras_notices.build_xras_messages`.

    Two lines, and they are the two the scheduled task cannot supply: Flask's
    request-scoped session, and the operator who clicked. Everything else —
    the payload, the subject, and above all the dedup key — lives in
    ``sam.queries.xras_notices`` so that the button and ``xras_notices`` can
    never disagree about what has already been sent.
    """
    return build_xras_messages(db.session, project, people, action=action,
                               requested_by=current_user.username)


@bp.route('/xras_notify_form/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_notify_form(project_id: int):
    """Modal body: **what these people will actually receive**, plus Send.

    A real send is irreversible, so the one-click POST became two steps — the
    same reasoning that already puts an ``hx-confirm`` on ``xras_activate``.
    A preview beats a confirm dialog because it also answers "and what does
    it say", which is the question an operator actually has.

    ``preview()`` writes **no** ledger row: a preview is not an attempt, and a
    stray row would poison the dedup query for the send that follows.

    The ledger is attached here even though a preview does not need one: it
    answers *"would this send be suppressed as a duplicate"* **before** the
    operator clicks, so the modal can offer the override up front rather than
    reporting "nothing was sent" afterwards and leaving SQL as the only
    recovery. Asking is cheap — one indexed lookup per recipient — and it is
    the same predicate ``send_many`` will apply.

    ``?action_id=`` names *which* outcome to report, which is what lets a
    Supplement be notified separately from the New before it. It is a query
    param rather than a second path segment deliberately: absent means "the
    newest action", so the bare URL keeps working and no route-map entry moved.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')

    action = load_xras_action(db.session,
                              request.args.get('action_id', type=int))
    people = get_xras_pending_recipients(db.session, [project_id]).get(project_id, [])
    messages = _xras_messages(project, people, action=action)

    notifier = get_notifier()
    preview = None
    preview_error = None
    if messages:
        try:
            preview = notifier.preview(messages[0])
        except Exception as exc:            # a template problem, not a send
            current_app.logger.warning(
                'XRAS notify preview failed for %s: %s', project.projcode, exc)
            preview_error = str(exc)

    # A notifier without a ledger cannot answer "was this already sent", and
    # that is a legitimate configuration — `get_notifier(ledger=False)` exists
    # for a pure preview. No ledger means no duplicate to override, so the
    # force toggle simply does not appear.
    already_notified = [
        m.recipient for m in messages
        if m.dedup_key and notifier.ledger is not None
        and notifier.ledger.already_sent(m.dedup_key)
    ]

    return render_template(
        'dashboards/allocations/partials/xras_notify_form.html',
        project=project,
        people=people,
        preview=preview,
        preview_error=preview_error,
        already_notified=already_notified,
        notify_enabled=notifier.config.enabled,
        redirect_to=notifier.config.redirect_to or None,
        # Every one of these notices tells a PI their allocation is usable —
        # the activation one says "is now active" in as many words. Nothing
        # orders Notify after Activate, and in the pre-deploy smoke a notice
        # went out 64 seconds before the project was activated. The operator
        # keeps the choice; it just stops being invisible.
        project_inactive=not project.is_active,
        # The action travels to the POST so the send reports the same outcome
        # the operator just previewed — not whatever is newest by then.
        post_url=url_for('allocations_dashboard.xras_notify',
                         project_id=project_id,
                         **({'action_id': action.xras_action_log_id}
                            if action is not None else {})),
    )


@bp.route('/xras_notify/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_notify(project_id: int):
    """Send the handoff mail, then record what actually happened.

    **Send first, record second.** The activation event's ``notified_to``
    names the addresses that *succeeded*, so the card never claims a handoff
    that did not leave the building. The recipients are recomputed here,
    server-side, and never taken from the request: "the current lead" and
    "who we notified" are different questions, and only the second is an
    audit answer.

    No path may 500. ``Notifier.send_many`` never raises for a delivery
    failure, and the three outcomes are:

    * **all delivered** — success fragment naming who was mailed;
    * **partial** — success fragment naming the failures; the event records
      only the successes;
    * **nothing delivered** (relay down, or ``NOTIFY_ENABLED`` off) — the
      manual-fallback dialog, which hands the operator the addresses and says
      plainly that nothing was sent. **No activation event is written**,
      because none happened.

    ``suppressed`` counts as "nothing delivered" here on purpose: if everyone
    was already told about this same XRAS action, there is no new handoff to
    record, and writing another ``notified`` event would be the double-count
    the derive rule exists to prevent.

    **The force override.** Suppression is right by default and wrong in the
    cases that actually reach an operator: a bad address since corrected, a
    template fixed after the fact, a recipient who deleted the mail. Without
    an override the only recovery is a ``DELETE`` against ``notification_log``,
    which is not a thing to ask of someone at 3am. ``force`` is offered by the
    modal **only when a duplicate would actually be suppressed**, and it
    bypasses the dedup check alone — ``NOTIFY_ENABLED`` still fails closed, so
    this cannot be used to mail from a deployment that is meant to be silent.
    A forced send is stamped on the activation event, because "we told them
    twice" is exactly the kind of thing the timeline exists to explain.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    action = load_xras_action(db.session,
                              request.args.get('action_id', type=int))
    people = get_xras_pending_recipients(db.session, [project_id]).get(project_id, [])
    messages = _xras_messages(project, people, action=action)

    # Unchecked checkboxes are omitted from the request entirely, so presence
    # is the signal — never a value comparison. See CLAUDE.md § 10.
    force = 'force' in request.form

    results = get_notifier().send_many(messages, force=force) if messages else []
    summary = notify_summary(results)

    if not summary['ok']:
        current_app.logger.info(
            'XRAS notify sent nothing: project=%s by=%s statuses=%s',
            project.projcode, current_user.username,
            sorted({r.status for r in results}) or ['no recipients'])
        return htmx_success(
            'dashboards/allocations/partials/xras_notify_manual_fallback.html',
            {'refreshXrasTab': {}},
            project=project, people=people, summary=summary)

    notified_to = '; '.join(
        f"{r.message.recipient.name or r.message.recipient.address} "
        f"<{r.message.recipient.address}>" for r in summary['delivered']) or None

    with management_transaction(db.session):
        event = _record_activation_event(
            project, 'notified', notified_to=notified_to,
            # Stamp the action actually reported, not whatever is newest by
            # now — an operator working through a backlog notifies about an
            # older outcome, and the timeline must say which one.
            action_log_id=(action.xras_action_log_id
                           if action is not None else None),
            comment=('Re-sent with the duplicate check overridden.'
                     if force else None))

    current_app.logger.info(
        'XRAS notify sent: project=%s by=%s to=%s failed=%d forced=%s',
        project.projcode, current_user.username, notified_to,
        len(summary['failed']), force)

    return htmx_success(
        'dashboards/allocations/partials/xras_notify_sent.html',
        {'refreshXrasTab': {}},
        project=project, summary=summary, recorded_at=event.creation_time)


@bp.route('/xras_activate/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_activate(project_id: int):
    """Activate a pending XRAS project in one click.

    WARNING: **Double-gated.** ``project.active`` is a GOVERNANCE_FIELD, and
    ``MANAGE_XRAS`` alone must not be enough to flip it.
    ``can_edit_project_governance`` is the single definition of who may — flat
    ``EDIT_PROJECTS`` with **no** steward override, so a project lead cannot.

    Deliberately not a §8 decorator: ``require_project_permission(EDIT_PROJECTS)``
    resolves a *projcode* and means "X **OR** project lead/admin", which is
    strictly too permissive here. Swapping this URL to a projcode to reach that
    decorator would introduce the very bug the gate exists to prevent.
    ``_ProjectUpdateHandler.form_input()`` calls the same helper in-body for the
    same reason.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')
    if not can_edit_project_governance(current_user, project):
        abort(403)

    # Idempotent: a double-click must not write two 'activated' events.
    if project.is_active:
        return htmx_success_message(
            {'refreshXrasTab': {}},
            f'{project.projcode} is already active.',
            detail='Nothing to do.')

    with management_transaction(db.session):
        # reactivate(), not update(active=True): the latter deliberately leaves
        # inactivate_time alone (see the method docstring for why widening it
        # would corrupt unrelated admin saves).
        project.reactivate()
        _record_activation_event(project, 'activated')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'Activated {project.projcode}.',
        detail=project.title or None)


@bp.route('/xras_dismiss_form/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dismiss_form(project_id: int):
    """Modal body: ask for the reason a project should not be activated."""
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')
    return render_template(
        'dashboards/allocations/partials/xras_pending_event_form.html',
        project=project,
        post_url=url_for('allocations_dashboard.xras_dismiss',
                         project_id=project_id),
    )


@bp.route('/xras_dismiss/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_dismiss(project_id: int):
    """Take a project out of the attention queue, with a required reason.

    Not permanent and not a delete: a dismissal is superseded by whichever comes
    later, a new XRAS action or an explicit Restore. See
    :func:`sam.queries.xras_activation.get_xras_activity` for the rule; the row
    stays, grayed, under Everything in the window.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    return handle_htmx_form_post(
        schema_cls=XrasActivationEventForm,
        template='dashboards/allocations/partials/xras_pending_event_form.html',
        do_action=lambda data: _record_activation_event(
            project, 'dismissed', comment=data['comment']),
        success_triggers=_XRAS_MODAL_TRIGGERS,
        success_message=f'Dismissed {project.projcode}.',
        success_detail='Out of the attention queue; Restore it under Everything '
                       'in the window, or a new XRAS action brings it back.',
        error_prefix='Error dismissing project',
        extra_context={
            'project': project,
            'post_url': url_for('allocations_dashboard.xras_dismiss',
                                project_id=project_id),
        },
    )


@bp.route('/xras_restore/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_restore(project_id: int):
    """Undo a dismissal.

    An append-only log has no DELETE, so this is a **superseding** event rather
    than the removal of the dismissal — the mistake and its correction both stay
    on the record, each with its own author and timestamp.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    with management_transaction(db.session):
        _record_activation_event(project, 'restored')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'Restored {project.projcode} to the attention queue.')


@bp.route('/xras_history/<int:project_id>')
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_history(project_id: int):
    """Modal body: the append-only operator timeline, plus an add-comment form.

    ``MANAGE_XRAS`` rather than ``VIEW_XRAS``, deliberately: the timeline surfaces
    ``notified_to``, which is project lead/admin contact detail — the same
    category of data the raw-payload gate was created for.
    """
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_modal_not_found('Project')
    return render_template(
        'dashboards/allocations/partials/xras_pending_history_modal.html',
        project=project,
        events=get_xras_activation_events(db.session, project_id),
        post_url=url_for('allocations_dashboard.xras_comment',
                         project_id=project_id),
    )


@bp.route('/xras_comment/<int:project_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_comment(project_id: int):
    """Append a note to a pending project's timeline."""
    project = _load_pending_project(project_id)
    if project is None:
        return htmx_not_found('Project')

    return handle_htmx_form_post(
        schema_cls=XrasActivationEventForm,
        template='dashboards/allocations/partials/xras_pending_history_modal.html',
        do_action=lambda data: _record_activation_event(
            project, 'comment', comment=data['comment']),
        success_triggers=_XRAS_MODAL_TRIGGERS,
        success_message=f'Comment added to {project.projcode}.',
        error_prefix='Error adding comment',
        context_fn=lambda: {
            'project': project,
            'events': get_xras_activation_events(db.session, project_id),
            'post_url': url_for('allocations_dashboard.xras_comment',
                                project_id=project_id),
        },
    )


@bp.route('/xras_action_details/<int:action_id>')
@login_required
@require_permission(Permission.VIEW_XRAS)
def xras_action_details(action_id: int):
    """HTMX fragment: full detail for a single XRAS action.

    ``include_payload`` is gated on MANAGE_XRAS at the *query* level, not just in
    the template: an operator without it never has the PII in their response body
    at all, so a view-source cannot leak what the page chose not to draw.
    """
    may_see_payload = has_permission(current_user, Permission.MANAGE_XRAS)
    rows = get_recent_xras_actions(
        db.session, action_log_id=action_id, include_payload=may_see_payload,
    )
    if not rows:
        # A bare string, not abort(404): this lands in a modal body, where a 404
        # error page would be worse than useless. text-danger-emphasis rather
        # than text-danger — the saturated brand red fails WCAG AA on the dark
        # card (3.35:1 measured); the -emphasis token is theme-aware.
        return htmx_modal_not_found('Action')
    return render_template(
        'dashboards/allocations/partials/xras_action_details_modal.html',
        r=rows[0], may_see_payload=may_see_payload,
    )


@bp.route('/xras_recheck/<int:action_id>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_XRAS)
def xras_recheck(action_id: int):
    """Re-validate a stored payload against today's code and data. Applies nothing.

    Reports the **verdict**, not the mechanism: an operator clicks this to learn
    whether a data fix took, so "Recorded as action #N" would answer a question
    nobody asked. The three outcomes map onto the ingest vocabulary — see
    ``webapp/api/xras/recheck.py``.
    """
    try:
        new_id, status = recheck_action(action_id, actor=current_user.username)
    except LookupError:
        return htmx_not_found('Action')
    except Exception:                              # pragma: no cover - defensive
        current_app.logger.exception('XRAS re-check failed for id=%s', action_id)
        # Deliberately does not interpolate the exception: this renders into the
        # operator's page, and an exception string is neither actionable nor
        # guaranteed to be free of internals. The traceback is in the log.
        return ('<div class="alert alert-danger mb-0">Re-check failed — '
                'see the application log.</div>', 500)

    headline = {
        'rechecked': 'Would succeed now.',
        'failed':    'Would still fail.',
        'manual':    'Nothing would run for this action.',
    }.get(status, 'Re-check complete.')

    return htmx_success_message(
        {'refreshXrasTab': {}},
        f'{headline} (action #{action_id})',
        detail=f'Nothing was applied. Recorded as #{new_id}; open it for details.',
    )

