"""File a user's XRAS action (Extension, Supplement, Renewal, New) as audited steps.

The ``xras_submission`` row is committed before any write. Each step then runs through
``audited_write`` (its own audit row, committed before the write) and records that row's id
on the submission. A rejected, errored or unverified step stops the flow, stamps
``last_error`` and leaves the state where it was: there is **no automatic abort**. Resume
skips every step whose event is ``verified``; abort deletes the line or action SAM minted,
never a visitor's placeholder person (a refile finds it standing). Flask-free.
Design: ``docs/plans/implemented/XRAS_SUBMISSION_SPRINT1.md`` § 2.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sam.dates import format_ymd, parse_wire_date
from sam.integration.xras_submission import OPEN_STATES, documents_to_reattach
from sam.manage.xras_remediation import RemediationOutcome, admin_client, audited_write

logger = logging.getLogger(__name__)

#: Action types that mint a line of their own; their abort deletes the line.
LINE_MINTING_TYPES = ('New', 'Renewal')

#: Writes that add a row in XRAS. One that may have landed (not verified, not rejected)
#: is never re-sent by a resume: a second Extension action is the UNVR0027 duplicate.
ADDING_OPERATIONS = ('create_request', 'renew_request', 'add_action', 'add_role',
                     'set_action_dates', 'add_grant', 'upload_document', 'create_person')

#: The steps that run before a minted line exists, so never read it by id.
PRE_MINT_STEPS = ('person', 'mint')


class SubmissionRefused(ValueError):
    """The flow will not start: lever off, type not armed, a second open row, no agreement."""


@dataclass(frozen=True)
class SamRefs:
    """The SAM rows a submission resolves to; ``request_action`` fills the two user ids it can."""

    project_id: Optional[int] = None
    user_id: Optional[int] = None
    admin_user_id: Optional[int] = None
    facility_id: Optional[int] = None
    mnemonic_code_id: Optional[int] = None
    allocation_type_id: Optional[int] = None
    contract_id: Optional[int] = None
    registration_id: Optional[int] = None     # a visitor's account_request (/request)


@dataclass(frozen=True)
class ResourceAmount:
    resource_id: int
    amount: float
    comments: Optional[str] = None


@dataclass(frozen=True)
class SubmissionDocument:
    """A PDF to attach. The payload keeps its name, size and digest, never the bytes."""

    document_type: str
    filename: str
    content: bytes = field(default=b'', repr=False)
    title: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        return {'document_type': self.document_type, 'filename': self.filename,
                'title': self.title, 'bytes': len(self.content),
                'sha256': hashlib.sha256(self.content).hexdigest() if self.content else None}


@dataclass(frozen=True)
class ActionRequest:
    """One action as the form assembled it; its payload is what the row stores."""

    action_type: str
    request_number: Optional[str] = None
    request_id: Optional[int] = None
    opportunity_id: Optional[int] = None
    title: Optional[str] = None
    abstract: Optional[str] = None
    keywords: Optional[str] = None
    user_comments: Optional[str] = None
    resources: Tuple[ResourceAmount, ...] = ()
    dates: Optional[Tuple[Optional[date], Optional[date]]] = None
    grants: Tuple[Dict[str, Any], ...] = ()
    fos_type_id: Optional[int] = None
    documents: Tuple[SubmissionDocument, ...] = ()
    eua_attribute_id: Optional[int] = None
    admin_username: Optional[str] = None
    #: A New's other people: ``{first_name, last_name, email, username}``. ``username`` is set
    #: for an active SAM user XRAS knows (an XRAS User role); the rest are invited at activation.
    roster: Tuple[Dict[str, Any], ...] = ()
    sam: SamRefs = SamRefs()

    @property
    def roster_usernames(self) -> Tuple[str, ...]:
        return tuple(e['username'] for e in self.roster if e.get('username'))

    @property
    def invitees(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(e for e in self.roster if not e.get('username'))

    def to_payload(self) -> Dict[str, Any]:
        return {
            'action_type': self.action_type, 'request_number': self.request_number,
            'request_id': self.request_id, 'opportunity_id': self.opportunity_id,
            'title': self.title, 'abstract': self.abstract, 'keywords': self.keywords,
            'user_comments': self.user_comments,
            'resources': [vars(r) for r in self.resources],
            'dates': [format_ymd(d) for d in self.dates] if self.dates else None,
            'grants': [dict(g) for g in self.grants], 'fos_type_id': self.fos_type_id,
            'documents': [d.to_payload() for d in self.documents],
            'eua_attribute_id': self.eua_attribute_id,
            'admin_username': self.admin_username,
            'roster': [dict(e) for e in self.roster], 'sam': vars(self.sam),
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any],
                     documents: Tuple[SubmissionDocument, ...] = ()) -> 'ActionRequest':
        """Rebuild a stored draft; *documents* supply the bytes the payload never kept."""
        by_name = {d.filename: d for d in documents}
        dates = payload.get('dates')
        return cls(
            action_type=payload['action_type'],
            request_number=payload.get('request_number'),
            request_id=payload.get('request_id'),
            opportunity_id=payload.get('opportunity_id'),
            title=payload.get('title'), abstract=payload.get('abstract'),
            keywords=payload.get('keywords'), user_comments=payload.get('user_comments'),
            resources=tuple(ResourceAmount(**r) for r in payload.get('resources') or ()),
            dates=tuple(parse_wire_date(d) for d in dates) if dates else None,
            grants=tuple(payload.get('grants') or ()),
            fos_type_id=payload.get('fos_type_id'),
            documents=tuple(by_name.get(d['filename']) or SubmissionDocument(
                d['document_type'], d['filename'], title=d.get('title'))
                for d in payload.get('documents') or ()),
            eua_attribute_id=payload.get('eua_attribute_id'),
            admin_username=payload.get('admin_username'),
            roster=tuple(payload.get('roster') or ()),
            sam=SamRefs(**(payload.get('sam') or {})),
        )


@dataclass(frozen=True)
class SubmissionOutcome:
    """Where the flow ended: the row, its state, and the step that stopped it (if any)."""

    submission_id: Optional[int]
    state: Optional[str]
    step: Optional[str] = None
    step_outcome: Optional[RemediationOutcome] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def submit_armed(action_type: Optional[str] = None, config=None) -> bool:
    """True when SAM may file for a user here: the submit lever, and *action_type* when given."""
    from sam.integration.xras_api.config import XrasApiConfig
    config = config or XrasApiConfig.from_environment()
    return config.submit_allows(action_type) if action_type else config.submit_configured


def _door_armed(door: str, action_type: str) -> bool:
    """The admin door needs only the write lever; the PI door its type; a visitor its own lever."""
    from sam.integration.xras_api.config import XrasApiConfig
    if door == 'admin':
        return True
    if door == 'visitor':
        return (action_type == 'New'
                and XrasApiConfig.from_environment().visitor_submit_configured)
    return submit_armed(action_type)


# the row, touched on short sessions of its own

def _with_row(session_factory, submission_id: int, fn: Callable) -> Any:
    from sam.integration.xras_submission import XrasSubmission
    with session_factory() as session:
        row = session.get(XrasSubmission, submission_id)
        if row is None:
            raise LookupError(f'xras_submission {submission_id} not found')
        value = fn(row)
        session.commit()
        return value


def _row_view(row) -> Dict[str, Any]:
    return {'id': row.xras_submission_id, 'state': row.state, 'action_type': row.action_type,
            'request_id': row.request_id, 'action_id': row.action_id,
            'request_number': row.request_number, 'request_type': row.request_type,
            'xa_user': row.xa_user, 'steps': row.steps, 'payload': row.payload_dict,
            'registration_id': row.registration_id}


def _step_statuses(session_factory, steps: Dict[str, Optional[int]]) -> Dict[str, str]:
    from sam.integration.xras_submission import step_statuses
    if not steps:
        return {}
    with session_factory() as session:
        return step_statuses(session, steps)


def _error_text(outcome: RemediationOutcome) -> Dict[str, Any]:
    """``last_error``: the status, the one-line reason and XRAS's ``errors[]`` whole."""
    result = outcome.result
    errors = list(getattr(result, 'errors', ()) or ())
    reason = outcome.error or getattr(result, 'verify_detail', None) \
        or getattr(result, 'write_error', None)
    return {'status': outcome.status, 'reason': reason, 'errors': errors}


# the steps

def _plan(draft: ActionRequest, view: Dict[str, Any]) -> List[Tuple[str, str, Callable]]:
    """``[(step, operation, dispatch(admin, keys))]`` for the populate phase, in order."""
    xa = view['xa_user']
    steps: List[Tuple[str, str, Callable]] = []
    fields = {k: v for k, v in (('title', draft.title), ('abstract', draft.abstract),
                                ('keywords', draft.keywords)) if v is not None}
    if draft.grants:
        fields['isSupportedByGrants'] = 'true'
    if fields:
        steps.append(('attributes', 'update_attributes', lambda a, k: a.update_request_attributes(
            k['request_id'], request_number=k['request_number'], xa_user=xa, **fields)))
    if draft.fos_type_id is not None:
        steps.append(('fos', 'set_fos', lambda a, k: a.set_fos(
            k['request_id'], draft.fos_type_id, xa_user=xa)))
    for r in draft.resources:
        steps.append((f'resource:{r.resource_id}', 'update_resource_amount',
                      lambda a, k, r=r: a.update_resource_amount(
                          k['request_id'], k['action_id'], r.resource_id, r.amount,
                          request_number=k['request_number'], xa_user=xa,
                          comments=r.comments)))
    if draft.dates:
        begin, end = draft.dates
        steps.append(('dates', 'set_action_dates', lambda a, k: a.set_action_dates(
            k['request_id'], k['action_id'], begin, end,
            request_number=k['request_number'], xa_user=xa)))
    for n, grant in enumerate(draft.grants):
        steps.append((f'grant:{n}', 'add_grant', lambda a, k, g=grant: a.add_grant(
            k['request_id'], xa_user=xa, **g)))
    if draft.eua_attribute_id is not None:
        attrs = [{'opportunityAttributeId': draft.eua_attribute_id, 'attributeValue': None}]
        steps.append(('opp_attributes', 'set_opp_attributes',
                      lambda a, k: a.set_opportunity_attributes(
                          k['request_id'], k['action_id'], attrs, xa_user=xa)))
    for doc in draft.documents:
        steps.append((f'document:{doc.filename}', 'upload_document',
                      lambda a, k, d=doc: a.upload_document(
                          k['request_id'], k['action_id'], document_type=d.document_type,
                          filename=d.filename, content=d.content, xa_user=xa,
                          title=d.title)))
    return steps


def _visitor_person(session_factory, registration_id: int) -> Dict[str, Any]:
    """The ``create_person`` fields from the visitor's ``account_request`` row."""
    from sam.core.account_requests import AccountRequest
    with session_factory() as session:
        row = session.get(AccountRequest, registration_id)
        if row is None:
            raise SubmissionRefused(f'registration {registration_id} is gone')
        return {'first_name': row.first_name, 'last_name': row.last_name, 'email': row.email,
                'middle_name': row.middle_name, 'phone': row.phone,
                'organization': row.organization, 'academic_status': row.academic_status,
                'residence_country': row.residence_country}


def _placeholder_minted(admin, username: str) -> bool:
    """True when *username* already stands in XRAS unreconciled: our placeholder from an earlier
    filing (abort deletes the line, never the person). A reconciled hit is someone else's and the
    create step refuses it; an outage reads as not minted and the create step fails, resumably."""
    from sam.integration.xras_api import XrasSourceUnavailable
    try:
        found = admin.get_person(username)
    except XrasSourceUnavailable:
        return False
    return isinstance(found, dict) and found.get('isReconciled') is False


def _mint_steps(draft: ActionRequest, view: Dict[str, Any],
                person: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str, Callable]]:
    xa = view['xa_user']
    kind = draft.action_type
    if kind == 'New':
        # A visitor's PI is a person SAM mints, unreconciled, before the line.
        steps = [('person', 'create_person', lambda a, k: a.create_person(xa, **person))] \
            if person else []
        steps.append(('mint', 'create_request', lambda a, k: a.create_request(
            draft.opportunity_id, xa_user=xa, request_type='New')))
        steps.append(('role:PI', 'add_role', lambda a, k: a.add_role(
            k['request_id'], 'PI', xa, request_number=k['request_number'], xa_user=xa)))
        if draft.admin_username:
            steps.append(('role:Admin', 'add_role', lambda a, k: a.add_role(
                k['request_id'], 'Allocation Manager', draft.admin_username,
                request_number=k['request_number'], xa_user=xa)))
        for username in draft.roster_usernames:
            steps.append((f'role:User:{username}', 'add_role', lambda a, k, u=username: a.add_role(
                k['request_id'], 'User', u, request_number=k['request_number'], xa_user=xa)))
        return steps
    if kind == 'Renewal':
        return [('mint', 'renew_request', lambda a, k: a.renew_request(
            draft.request_id, request_number=draft.request_number, xa_user=xa,
            opportunity_id=draft.opportunity_id))]
    return [('mint', 'add_action', lambda a, k: a.add_action(
        draft.request_id, kind, request_number=draft.request_number, xa_user=xa,
        user_comments=draft.user_comments))]


def _minted_keys(operation: str, result) -> Dict[str, Any]:
    """The XRAS ids a mint step learned, recorded even when its verify fell short."""
    extra = getattr(result, 'extra', None) or {}
    if operation == 'create_request':
        return {'request_id': extra.get('request_id'), 'action_id': extra.get('action_id'),
                'snapshot': extra.get('response')}
    if operation == 'renew_request':
        return {'request_id': extra.get('renewal_request_id'),
                'action_id': extra.get('action_id'), 'snapshot': extra.get('response')}
    return {'action_id': extra.get('action_id')}


def _by_id(dispatch: Callable, xa_user: str) -> Callable:
    """*dispatch* with its verify reading the line by id: a line SAM minted is a draft."""
    def scoped(admin, keys):
        with admin.reading_draft(keys['request_id'], xa_user):
            return dispatch(admin, keys)
    return scoped


def _run_step(session_factory, view, step, operation, dispatch, admin) -> RemediationOutcome:
    """One audited write, then its event id on the row; keys from a mint are kept either way."""
    keys = {k: view[k] for k in ('request_id', 'action_id', 'request_number')}
    outcome = audited_write(
        operation, session_factory, lambda a: dispatch(a, keys),
        open_fields=dict(created_by=view['actor'], xa_user=view['xa_user'],
                         request_number=view['request_number'],
                         request_id=view['request_id'], action_id=view['action_id'],
                         comment=f"xras_submission {view['id']}: {step}"),
        request_number=view['request_number'], client=admin, refresh=lambda: True)

    def record(row):
        if step == 'mint' and outcome.result is not None:
            row.set_keys(**_minted_keys(operation, outcome.result))
        row.record_step(step, outcome.event_id)
        return _row_view(row)
    view.update(_with_row(session_factory, view['id'], record))
    logger.info('xras submission %s: step=%s op=%s request=%s action=%s status=%s',
                view['id'], step, operation, view['request_id'], view['action_id'],
                outcome.status)
    return outcome


def _stop(session_factory, view, step, outcome=None, *, reason=None) -> SubmissionOutcome:
    error = _error_text(outcome) if outcome else {'status': 'stopped', 'reason': reason,
                                                  'errors': []}
    _with_row(session_factory, view['id'], lambda row: row.stop({'step': step, **error}))
    return SubmissionOutcome(view['id'], view['state'], step=step, step_outcome=outcome,
                             error=error['reason'] or error['status'])


def _advance(session_factory, view, state) -> None:
    view.update(_with_row(session_factory, view['id'],
                          lambda row: _row_view(row.advance(state))))


def _drive(session_factory, draft: ActionRequest, view, *, submit: bool,
           admin) -> SubmissionOutcome:
    statuses = _step_statuses(session_factory, view['steps'])
    done = {step for step, status in statuses.items() if status == 'verified'}

    minted = draft.action_type in LINE_MINTING_TYPES

    def run(phase):
        for step, operation, dispatch in phase:
            if step in done:
                continue
            # The mint has no line to read yet; every later step on a minted line is a draft's.
            if minted and step not in PRE_MINT_STEPS:
                dispatch = _by_id(dispatch, view['xa_user'])
            if operation in ADDING_OPERATIONS and statuses.get(step) not in (None, 'rejected'):
                return _stop(session_factory, view, step, reason=(
                    f'{step} may have reached XRAS ({statuses[step]}); check the request '
                    'there, then abort rather than resume'))
            outcome = _run_step(session_factory, view, step, operation, dispatch, admin)
            if not outcome.succeeded:
                return _stop(session_factory, view, step, outcome)
        return None

    if view['state'] == 'draft':
        person = None
        if view.get('registration_id') and not _placeholder_minted(admin, view['xa_user']):
            person = _visitor_person(session_factory, view['registration_id'])
        stopped = run(_mint_steps(draft, view, person))
        if stopped:
            return stopped
        _advance(session_factory, view, 'created')

    populate = _plan(draft, view)
    missing = documents_to_reattach(statuses, [d.filename for d in draft.documents
                                               if not d.content])
    if view['state'] == 'created' and missing:
        return _stop(session_factory, view, f'document:{missing[0]}',
                     reason=f'attach {", ".join(missing)} again to continue')
    if view['state'] == 'created' and populate:
        stopped = run(populate)
        if stopped:
            return stopped
        _advance(session_factory, view, 'populated')

    if submit and view['state'] in ('created', 'populated'):
        stopped = run([('submit', 'submit_action', lambda a, k: a.submit_action(
            k['request_id'], k['action_id'], request_number=k['request_number'],
            xa_user=view['xa_user'], preflight=True))])
        if stopped:
            return stopped
        _advance(session_factory, view, 'submitted')
        if not view['request_number']:
            _learn_number(session_factory, view, admin)

    _patch_card(session_factory, view)
    return SubmissionOutcome(view['id'], view['state'])


def _learn_number(session_factory, view, admin) -> None:
    """A New's number, minted by the submit: one by-id read, best-effort (the sweep backstops)."""
    try:
        line = admin.get_draft(view['request_id'], xa_user=view['xa_user'])
    except Exception as exc:                            # noqa: BLE001
        logger.warning('xras submission %s: could not read the minted number (%s)',
                       view['id'], exc)
        return
    number = str(line.get('requestNumber') or '').strip() if isinstance(line, dict) else ''
    if number:
        view.update(_with_row(session_factory, view['id'],
                              lambda row: _row_view(row.set_keys(request_number=number))))


def _patch_card(session_factory, view) -> None:
    """One best-effort Remediations-card patch per run, for a numbered line.

    A run that just submitted inserts the row: the index otherwise lacks SAM's filing until the
    daily full sweep. ``pending_push`` is explicit, since an absent row defaults to pending.
    """
    if view['request_number']:
        from sam.manage.xras_remediation import refresh_index_entry
        submitted = view['state'] == 'submitted'
        refresh_index_entry(view['request_number'], session_factory=session_factory,
                            insert=submitted,
                            pending_push=(view['action_type'] == 'New') if submitted else None)


# the entry points

def _resolved_refs(session, draft: ActionRequest, xa_user: str) -> Dict[str, Optional[int]]:
    """``vars(draft.sam)`` with the PI's and the admin's user ids filled where the form left them."""
    from sam.core.users import User
    from sam.projects.projects import Project
    refs = dict(vars(draft.sam))
    if refs.get('user_id') is None:
        pi = User.get_by_username(session, xa_user)
        refs['user_id'] = pi.user_id if pi else None
    if refs.get('admin_user_id') is None:
        if draft.admin_username:
            admin = User.get_by_username(session, draft.admin_username)
            refs['admin_user_id'] = admin.user_id if admin else None
        elif refs.get('project_id'):
            project = session.get(Project, refs['project_id'])
            refs['admin_user_id'] = project.project_admin_user_id if project else None
    return refs


def request_action(session_factory, draft: Optional[ActionRequest], *, actor: str,
                   xa_user: str, eula: Tuple[str, datetime], submit: bool = True,
                   resume_id: Optional[int] = None, door: str = 'pi',
                   client=None) -> SubmissionOutcome:
    """Open (or resume) a submission and run its steps; ``submit=False`` stops before submit.

    The PI door (``xa_user == actor``) needs the submit lever and the type armed; the
    admin door (``door='admin'``, xa_user the request's PI) needs only the write lever; the
    visitor door (a New whose ``draft.sam.registration_id`` names its PI) needs its own.
    Raises :class:`SubmissionRefused` before any write.
    """
    from sam.integration.xras_submission import XrasSubmission, open_submission_for

    if resume_id is not None:
        return resume_submission(session_factory, resume_id, actor=actor, submit=submit,
                                 documents=draft.documents if draft else (), door=door,
                                 client=client)
    if not _door_armed(door, draft.action_type):
        raise SubmissionRefused(f'filing a {draft.action_type} through SAM is not enabled here')
    if (door == 'visitor') != bool(draft.sam.registration_id):
        raise SubmissionRefused('a visitor files under a registration, and only a visitor does')
    sha, accepted_at = eula or (None, None)
    if not sha or accepted_at is None:
        raise SubmissionRefused('the agreement must be accepted first')
    admin = admin_client(client)

    with session_factory() as session:
        if open_submission_for(session, request_id=draft.request_id,
                               action_type=draft.action_type, created_by=actor):
            raise SubmissionRefused(
                f'a {draft.action_type} on this request is already in progress')
        # The row remembers whether it was opened to submit: an operator's unsubmitted
        # Add-action must not be submitted by the resume that completes it.
        row = XrasSubmission.create(
            session, action_type=draft.action_type, created_by=actor, xa_user=xa_user,
            eula_sha=sha, eula_accepted_at=accepted_at,
            payload={**draft.to_payload(), 'submit': submit},
            request_id=draft.request_id, request_number=draft.request_number,
            opportunity_id=draft.opportunity_id,
            request_type=draft.action_type if draft.action_type in LINE_MINTING_TYPES else None,
            **_resolved_refs(session, draft, xa_user))
        session.commit()
        view = {**_row_view(row), 'actor': actor}
    logger.info('xras submission %s: opened %s for %s by %s', view['id'],
                draft.action_type, xa_user, actor)
    return _drive(session_factory, draft, view, submit=submit, admin=admin)


def resume_submission(session_factory, submission_id: int, *, actor: str,
                      submit: Optional[bool] = None,
                      documents: Tuple[SubmissionDocument, ...] = (),
                      door: str = 'pi', client=None) -> SubmissionOutcome:
    """Continue a stopped submission from its payload, skipping every verified step.

    ``submit=None`` honors what the row was opened with.
    """
    view = {**_with_row(session_factory, submission_id, _row_view), 'actor': actor}
    if view['state'] not in OPEN_STATES:
        raise SubmissionRefused(f'submission {submission_id} is {view["state"]}')
    if not _door_armed(door, view['action_type']):
        raise SubmissionRefused(f'filing a {view["action_type"]} through SAM is not enabled here')
    if submit is None:
        submit = (view['payload'] or {}).get('submit', True)
    draft = ActionRequest.from_payload(view['payload'], documents)
    logger.info('xras submission %s: resumed by %s at %s', submission_id, actor,
                view['state'])
    return _drive(session_factory, draft, view, submit=submit, admin=admin_client(client))


def abort_submission(session_factory, submission_id: int, *, actor: str,
                     door: str = 'pi', client=None) -> SubmissionOutcome:
    """End an in-flight submission: delete the line SAM minted, else the action; then ``aborted``.

    The PI door needs the submit lever with the type armed, as filing does; the admin door
    (an operator ending a row they opened, or any row) needs only the write lever.
    """
    view = {**_with_row(session_factory, submission_id, _row_view), 'actor': actor}
    if view['state'] not in OPEN_STATES:
        raise SubmissionRefused(f'submission {submission_id} is {view["state"]}')
    if not _door_armed(door, view['action_type']):
        raise SubmissionRefused(
            f'discarding a {view["action_type"]} through SAM is not enabled here')
    xa = view['xa_user']
    # A row opens on the line it acts on (a Renewal: the line it renews), so only an id
    # that differs is a line SAM minted. A refused renew leaves the original's id behind,
    # and that line must never be deleted.
    minted_line = (view['request_type'] in LINE_MINTING_TYPES and view['request_id']
                   and view['request_id'] != (view['payload'] or {}).get('request_id'))

    if minted_line:
        step = ('abort', 'delete_request', lambda a, k: a.delete_request(
            k['request_id'], request_number=None, xa_user=xa))
    elif view['action_id']:
        step = ('abort', 'delete_action', lambda a, k: a.delete_action(
            k['request_id'], k['action_id'], xa_user=xa))
    else:
        step = None
    if step is not None:
        outcome = _run_step(session_factory, view, *step, admin_client(client))
        if not outcome.succeeded:
            return _stop(session_factory, view, 'abort', outcome)
    _advance(session_factory, view, 'aborted')
    logger.info('xras submission %s: aborted by %s', submission_id, actor)
    _patch_card(session_factory, view)
    return SubmissionOutcome(view['id'], view['state'])
