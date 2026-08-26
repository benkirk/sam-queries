"""``POST /v1/roles/{requestNumber}/{role}/{username}`` -- reassign the project lead.

Legacy endpoint #7. POST only. ``requestNumber`` IS the projcode. Success is
200 with an empty body and no ``Content-Type`` (see
:func:`webapp.api.xras.serialize.empty_ok`). The write is exactly two things:
set the project lead, stamp ``modified_time`` -- no roster insert, no
allocation touch.

The role check runs BEFORE the project or user is looked up, so a bad role
against a nonexistent project is 404-role. That ordering is deliberate.

Status codes diverge from legacy deliberately: 404 for a missing role,
project, or user (all three segments are resource identity), 409 for an
inactive project or user (the resource exists; its state refuses the write).
Legacy answers 400 for every validation failure because its four-branch error
ladder is dead code. That analysis, and why 403 was the wrong instinct, are
recorded in ``XRAS_REIMPLEMENTATION.md`` sections 3 and 7.
"""

from flask import abort, current_app, request

from sam.core.users import User
from sam.manage import management_transaction
from sam.projects.projects import Project
from webapp.extensions import csrf, db

from . import actions, bp, xras_api_required
from .serialize import empty_ok

#: The audit row's ``action_type``. Not a wire ``actionType`` — XRAS never sends one here,
#: since the whole request is in the path. It needs no vocabulary change:
#: ``XRAS_ACTION_TYPES`` is explicitly not a constraint ("callers union this with whatever
#: ``DISTINCT action_type`` holds"), so this shows up in the dashboard filter by itself.
#:
#: ``service`` is left NULL on purpose: that column is documented as one of
#: ``sam.xras.dispatch.SERVICES``, and this endpoint is not dispatched through the
#: registry at all.
_ACTION_TYPE = 'RoleChange'


@bp.route('/roles/<request_number>/<role>/<username>', methods=['POST'])
@csrf.exempt          # token path is Basic-auth (no cookies); actions.py precedent
@xras_api_required()
def set_user_role(request_number, role, username):
    """Reassign *request_number*'s project lead to *username*.

    Other verbs on this path are **not** mapped, matching legacy's ``@PostMapping``.
    They fall through to :mod:`webapp.api.xras.unmapped` and are recorded, which is
    better than legacy's silent 404 — the ACCESS spec documents a ``DELETE`` here for
    revocations that legacy never implemented.
    """
    # The row lands before any validation, for the same reason `post_action` writes one
    # before dispatch: a request that explodes mid-way must still leave a record. There
    # is no body, so the request line *is* the payload — and `raw_payload` is NOT NULL.
    log_id = actions._record(
        status='received',
        raw_payload=f'{request.method} {request.full_path}',
        action_type=_ACTION_TYPE,
        request_number=request_number,
        http_status=200,
    )

    def _reject(message, code):
        """Close the audit row out and answer. Never returns — ``abort`` raises."""
        actions._finish(log_id, status='failed', error_messages=[message],
                        outcome_reason=message, http_status=code)
        current_app.logger.warning(
            'XRAS role change rejected: id=%s request=%s user=%s — %s',
            log_id, request_number, username, message)
        abort(code, message)

    # First, and before anything is looked up — legacy's controller checks the role
    # before calling the service at all. `equalsIgnoreCase`, so PI/Pi/pi all pass, and
    # the message echoes the caller's own casing (legacy's String.format does).
    if role.lower() != 'pi':
        _reject(f'role {role} does not exist', 404)

    if actions._capture_only():
        # The same interlock `POST /actions` honors, and the reason this endpoint is not
        # simply "the write, plus auth": while legacy is still the system of record,
        # applying the change here would fight it. The role check above is this route's
        # equivalent of schema validation and still runs; the lookup and the write are its
        # equivalent of dispatch, and are what gets suppressed.
        #
        # The row stays 'received' — precisely true, and it is what makes the capture
        # window's backlog queryable.
        current_app.logger.info(
            'XRAS role change captured (not applied): id=%s request=%s user=%s',
            log_id, request_number, username)
        return empty_ok()

    project = Project.get_by_projcode(db.session, request_number)
    if project is None:
        _reject('non-existent project', 404)
    if not project.is_active:
        _reject('inactive project', 409)

    user = User.get_by_username(db.session, username)
    if user is None:
        _reject('non-existent user', 404)
    if not user.is_active:
        _reject('inactive user', 409)

    # Legacy inserts no roster row, so neither do we — but a lead who is not a member is
    # worth saying out loud, because `project.lead` is a bare FK with no membership
    # constraint behind it and nothing else would ever mention it.
    if not project.has_user(user):
        current_app.logger.warning(
            'XRAS set a project lead who is not a project member: '
            'id=%s projcode=%s user=%s', log_id, project.projcode, username)

    with management_transaction(db.session):
        project.update(project_lead_user_id=user.user_id)

    actions._finish(log_id, status='processed', projcode_result=project.projcode,
                    http_status=200)
    current_app.logger.info(
        'XRAS role change applied: id=%s projcode=%s lead=%s',
        log_id, project.projcode, username)

    return empty_ok()
