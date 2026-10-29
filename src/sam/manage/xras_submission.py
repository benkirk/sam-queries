"""File a user's XRAS action (Extension, Supplement, Renewal, New) as audited steps.

The ``xras_submission`` row is committed before any write. Each step then runs through
``_editor_op`` (its own audit row, committed before the write) and records that row's id
on the submission. A rejected, errored or unverified step stops the flow, stamps
``last_error`` and leaves the state where it was: there is **no automatic abort**. Resume
skips every step whose event is ``verified``; abort deletes what SAM minted. Flask-free.
Design: ``docs/plans/XRAS_SUBMISSION_SPRINT1.md`` § 2.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sam.manage.xras_remediation import RemediationOutcome, _client, _editor_op

logger = logging.getLogger(__name__)

#: Action types that mint a line of their own; their abort deletes the line.
LINE_MINTING_TYPES = ('New', 'Renewal')

#: Writes that add a row in XRAS. One that may have landed (not verified, not rejected)
#: is never re-sent by a resume: a second Extension action is the UNVR0027 duplicate.
ADDING_OPERATIONS = ('create_request', 'renew_request', 'add_action', 'add_role',
                     'set_action_dates', 'add_grant', 'upload_document')


class SubmissionRefused(ValueError):
    """The flow will not start: lever off, type not armed, a second open row, no agreement."""


@dataclass(frozen=True)
class SamRefs:
    project_id: Optional[int] = None
    facility_id: Optional[int] = None
    mnemonic_code_id: Optional[int] = None
    allocation_type_id: Optional[int] = None
    contract_id: Optional[int] = None


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
    sam: SamRefs = SamRefs()

    def to_payload(self) -> Dict[str, Any]:
        return {
            'action_type': self.action_type, 'request_number': self.request_number,
            'request_id': self.request_id, 'opportunity_id': self.opportunity_id,
            'title': self.title, 'abstract': self.abstract, 'keywords': self.keywords,
            'user_comments': self.user_comments,
            'resources': [vars(r) for r in self.resources],
            'dates': [_iso(d) for d in self.dates] if self.dates else None,
            'grants': [dict(g) for g in self.grants], 'fos_type_id': self.fos_type_id,
            'documents': [d.to_payload() for d in self.documents],
            'eua_attribute_id': self.eua_attribute_id,
            'admin_username': self.admin_username, 'sam': vars(self.sam),
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
            dates=tuple(_from_iso(d) for d in dates) if dates else None,
            grants=tuple(payload.get('grants') or ()),
            fos_type_id=payload.get('fos_type_id'),
            documents=tuple(by_name.get(d['filename']) or SubmissionDocument(
                d['document_type'], d['filename'], title=d.get('title'))
                for d in payload.get('documents') or ()),
            eua_attribute_id=payload.get('eua_attribute_id'),
            admin_username=payload.get('admin_username'),
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


def _iso(value) -> Optional[str]:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _from_iso(value) -> Optional[date]:
    return date.fromisoformat(value[:10]) if isinstance(value, str) else value


def submit_armed(action_type: Optional[str] = None, config=None) -> bool:
    """True when SAM may file for a user here: the submit lever, and *action_type* when given."""
    from sam.integration.xras_api.config import XrasApiConfig
    config = config or XrasApiConfig.from_environment()
    return config.submit_allows(action_type) if action_type else config.submit_configured


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
            'xa_user': row.xa_user, 'steps': row.steps, 'payload': row.payload_dict}


def _step_statuses(session_factory, steps: Dict[str, Optional[int]]) -> Dict[str, str]:
    """``{step: audit status}`` for every step that opened an event."""
    from sam.integration.xras import XrasRemediationEvent
    ids = [i for i in steps.values() if i]
    if not ids:
        return {}
    with session_factory() as session:
        status = dict(session.query(XrasRemediationEvent.xras_remediation_event_id,
                                    XrasRemediationEvent.status)
                      .filter(XrasRemediationEvent.xras_remediation_event_id.in_(ids)))
    return {step: status[i] for step, i in steps.items() if i in status}


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


def _mint_steps(draft: ActionRequest, view: Dict[str, Any]) -> List[Tuple[str, str, Callable]]:
    xa = view['xa_user']
    kind = draft.action_type
    if kind == 'New':
        steps = [('mint', 'create_request', lambda a, k: a.create_request(
            draft.opportunity_id, xa_user=xa, request_type='New'))]
        steps.append(('role:PI', 'add_role', lambda a, k: a.add_role(
            k['request_id'], 'PI', xa, request_number=k['request_number'], xa_user=xa)))
        if draft.admin_username:
            steps.append(('role:Admin', 'add_role', lambda a, k: a.add_role(
                k['request_id'], 'Allocation Manager', draft.admin_username,
                request_number=k['request_number'], xa_user=xa)))
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


def _run_step(session_factory, view, step, operation, dispatch, admin) -> RemediationOutcome:
    """One audited write, then its event id on the row; keys from a mint are kept either way."""
    keys = {k: view[k] for k in ('request_id', 'action_id', 'request_number')}
    outcome = _editor_op(
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

    def run(phase):
        for step, operation, dispatch in phase:
            if step in done:
                continue
            if operation in ADDING_OPERATIONS and statuses.get(step) not in (None, 'rejected'):
                return _stop(session_factory, view, step, reason=(
                    f'{step} may have reached XRAS ({statuses[step]}); check the request '
                    'there, then abort rather than resume'))
            outcome = _run_step(session_factory, view, step, operation, dispatch, admin)
            if not outcome.succeeded:
                return _stop(session_factory, view, step, outcome)
        return None

    if view['state'] == 'draft':
        stopped = run(_mint_steps(draft, view))
        if stopped:
            return stopped
        _advance(session_factory, view, 'created')

    populate = _plan(draft, view)
    missing = [d.filename for d in draft.documents
               if not d.content
               and statuses.get(f'document:{d.filename}') in (None, 'rejected')]
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

    _patch_card(session_factory, view)
    return SubmissionOutcome(view['id'], view['state'])


def _patch_card(session_factory, view) -> None:
    """One best-effort Remediations-card patch per run, for a numbered line."""
    if view['request_number']:
        from sam.manage.xras_remediation import _refresh_index_entry
        _refresh_index_entry(view['request_number'], session_factory=session_factory)


# the entry points

def request_action(session_factory, draft: Optional[ActionRequest], *, actor: str,
                   xa_user: str, eula: Tuple[str, datetime], submit: bool = True,
                   resume_id: Optional[int] = None, door: str = 'pi',
                   client=None) -> SubmissionOutcome:
    """Open (or resume) a submission and run its steps; ``submit=False`` stops before submit.

    The PI door (``xa_user == actor``) needs the submit lever and the type armed; the
    admin door (``door='admin'``, xa_user the request's PI) needs only the write lever.
    Raises :class:`SubmissionRefused` before any write.
    """
    from sam.integration.xras_submission import XrasSubmission, open_submission_for

    if resume_id is not None:
        return resume_submission(session_factory, resume_id, actor=actor, submit=submit,
                                 documents=draft.documents if draft else (), door=door,
                                 client=client)
    if door == 'pi' and not submit_armed(draft.action_type):
        raise SubmissionRefused(f'filing a {draft.action_type} through SAM is not enabled here')
    sha, accepted_at = eula or (None, None)
    if not sha or accepted_at is None:
        raise SubmissionRefused('the agreement must be accepted first')
    admin = _client(client)

    with session_factory() as session:
        if open_submission_for(session, request_id=draft.request_id,
                               action_type=draft.action_type, created_by=actor):
            raise SubmissionRefused(
                f'a {draft.action_type} on this request is already in progress')
        row = XrasSubmission.create(
            session, action_type=draft.action_type, created_by=actor, xa_user=xa_user,
            eula_sha=sha, eula_accepted_at=accepted_at, payload=draft.to_payload(),
            request_id=draft.request_id, request_number=draft.request_number,
            opportunity_id=draft.opportunity_id,
            request_type=draft.action_type if draft.action_type in LINE_MINTING_TYPES else None,
            **vars(draft.sam))
        session.commit()
        view = {**_row_view(row), 'actor': actor}
    logger.info('xras submission %s: opened %s for %s by %s', view['id'],
                draft.action_type, xa_user, actor)
    return _drive(session_factory, draft, view, submit=submit, admin=admin)


def resume_submission(session_factory, submission_id: int, *, actor: str,
                      submit: bool = True, documents: Tuple[SubmissionDocument, ...] = (),
                      door: str = 'pi', client=None) -> SubmissionOutcome:
    """Continue a stopped submission from its payload, skipping every verified step."""
    view = {**_with_row(session_factory, submission_id, _row_view), 'actor': actor}
    if view['state'] not in ('draft', 'created', 'populated'):
        raise SubmissionRefused(f'submission {submission_id} is {view["state"]}')
    if door == 'pi' and not submit_armed(view['action_type']):
        raise SubmissionRefused(f'filing a {view["action_type"]} through SAM is not enabled here')
    draft = ActionRequest.from_payload(view['payload'], documents)
    logger.info('xras submission %s: resumed by %s at %s', submission_id, actor,
                view['state'])
    return _drive(session_factory, draft, view, submit=submit, admin=_client(client))


def abort_submission(session_factory, submission_id: int, *, actor: str,
                     client=None) -> SubmissionOutcome:
    """End an in-flight submission: delete the line SAM minted, else the action; then ``aborted``."""
    view = {**_with_row(session_factory, submission_id, _row_view), 'actor': actor}
    if view['state'] not in ('draft', 'created', 'populated'):
        raise SubmissionRefused(f'submission {submission_id} is {view["state"]}')
    xa = view['xa_user']

    if view['request_type'] in LINE_MINTING_TYPES and view['request_id']:
        step = ('abort', 'delete_request', lambda a, k: a.delete_request(
            k['request_id'], request_number=None, xa_user=xa))
    elif view['action_id']:
        step = ('abort', 'delete_action', lambda a, k: a.delete_action(
            k['request_id'], k['action_id'], xa_user=xa))
    else:
        step = None
    if step is not None:
        outcome = _run_step(session_factory, view, *step, _client(client))
        if not outcome.succeeded:
            return _stop(session_factory, view, 'abort', outcome)
    _advance(session_factory, view, 'aborted')
    logger.info('xras submission %s: aborted by %s', submission_id, actor)
    _patch_card(session_factory, view)
    return SubmissionOutcome(view['id'], view['state'])
