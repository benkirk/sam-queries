"""``POST /v1/roles/{requestNumber}/{role}/{username}`` — reassign the project lead.

Legacy endpoint #7, and the only one XRAS maps that this port originally missed. It is a
**write**, it saw zero traffic in the 58 days of access logs that were audited, and after
the cutover repoints XRAS's single base URL it would have 404'd where legacy 200s — with
nothing recording the attempt. Hence :mod:`webapp.api.xras.unmapped`, which is the general
form of that problem, and hence this module, which is the specific one.

What legacy actually does
-------------------------

Read from source (``~/codes/sam``), not from the decompiled WAR, and the two disagree in
ways that matter:

``RoleServiceController.java:20`` is ``@PostMapping`` — **POST only**. A ``javap`` of the
deployed class shows ``value=[...]`` with no ``method=``, which reads like "all verbs",
but ``@PostMapping`` is a Spring meta-annotation carrying ``method = RequestMethod.POST``
on *itself*; every ``@GetMapping`` on the other five controllers decompiles identically.

``BaseController.createOkResponse()`` is ``new ResponseEntity(HttpStatus.OK)`` — 200 with
no body and, because no message converter runs, **no** ``Content-Type``. See
:func:`webapp.api.xras.serialize.empty_ok`.

``DefaultUpdateProjectLeadCommand.transact()`` does exactly two things:
``project.setProjectLead(user)`` and ``project.setModifiedTime(now)``. No roster insert,
no allocation touch. Reproduced here — ``TimestampMixin`` covers the second. Note
``requestNumber`` **is** the projcode: it goes straight into ``projectRepository.get()``,
consistent with all 130 success lines in the production action log.

The role check runs **before** the project or the user is looked at, so a bad role against
a nonexistent project is 404-*role*. That ordering is preserved.

Why the status codes diverge
----------------------------

Legacy's error ladder does not work. ``RoleServiceImpl`` matches the validation message
against four regexes (``"Project  *[^ ]* * does not exist."`` and friends), but the message
it receives is built by ``ValidationException.errorsToString()`` and always looks like::

    ValidationException:
     projcode: Project ABC123 does not exist.(ABC123)

``String.matches`` is a full-string match, so the ``ValidationException:`` prefix and the
``(invalidValue)`` suffix defeat every pattern — **every** validation failure falls to the
``else`` and answers **400** carrying that raw string. ``RoleServiceImplTest`` is green
only because it ``@Mock``s ``getMessage()`` to return the bare sentence, bypassing the real
assembly; it documents intent, not behavior.

So there is no deployed contract worth reproducing, and no client to break — which leaves
picking codes that are actually right:

===========================  ======  =========================================
condition                     code    reasoning
===========================  ======  =========================================
role is not ``pi``             404    all three segments are resource identity,
project not found              404    so a missing one is Not Found
user not found                 404
project inactive               409    the resource exists; its *state* refuses
user inactive                  409    the write — that is Conflict
success                        200    empty body, no ``Content-Type``
===========================  ======  =========================================

Legacy's *intended* ladder (per its unit test) used 403 for the last three. That is wrong
twice over: 403 is an authorization verdict about the caller, and on an endpoint sitting
behind Basic auth it is indistinguishable from a bad API key — the wrong first instinct
during triage week. It is not a design decision in legacy either, but an artifact of
``XrasController`` mapping ``BadStateException`` to ``FORBIDDEN``, that being the only
non-404 exception class ``RoleServiceImpl`` had available.

Recorded as a deliberate divergence in ``XRAS_REIMPLEMENTATION.md`` § 7.
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

    if current_app.config.get('XRAS_ACTIONS_CAPTURE_ONLY', True):
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
