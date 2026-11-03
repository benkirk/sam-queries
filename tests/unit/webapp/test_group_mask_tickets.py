"""send_mask_ticket's two paths (the provider, then mail) and the withdraw comment, driven
directly with the fakes the account-ticket tests use. The external_ticket link commits on
its own session, so it is deleted by entity id afterwards."""
from dataclasses import dataclass, field
from typing import List

import pytest
from sqlalchemy.orm import Session

from factories import make_adhoc_group, make_adhoc_group_tag
from factories.tickets import FakeTicketProvider
from sam import ExternalTicket
from sam.core.groups import UnixGroupMask
from sam.integration.tickets import TicketSourceUnavailable
from sam.notify.base import DeliveryResult


@dataclass
class _Ledger:
    sent_keys: set = field(default_factory=set)
    rows: List[dict] = field(default_factory=list)

    def already_sent(self, key):
        return key in self.sent_keys

    def record(self, message, *, status, transport, detail=None, rendered=None):
        self.rows.append({'status': status, 'transport': transport, 'detail': detail,
                          'key': message.dedup_key,
                          'template': rendered.template_text if rendered else None})
        return len(self.rows)

    def resolve(self, log_id, *, status, detail=None):
        self.rows[log_id - 1].update(status=status, detail=detail)


class _Mailer:
    def __init__(self):
        self.ledger = _Ledger()
        self.mailed = []

    def send(self, message, **_):
        self.mailed.append(message)
        return DeliveryResult(ok=True, status='sent', message=message)


@pytest.fixture
def mailer(app, monkeypatch):
    from sam.notify import Notifier, NotifyConfig, NullTransport
    stub = _Mailer()

    def get_notifier(*, read_only=False):
        if read_only:
            return Notifier(config=NotifyConfig(enabled=False), transport=NullTransport(),
                            ledger=None)
        return stub
    monkeypatch.setattr('webapp.dashboards.admin.group_mask_tickets.get_notifier', get_notifier)
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', 'help@example.invalid')
    return stub


@pytest.fixture
def provider(monkeypatch):
    # (provider, key) is unique in external_ticket and xdist workers share the DB.
    from uuid import uuid4
    fake = FakeTicketProvider(prefix=f'FGM{uuid4().hex[:6].upper()}')
    monkeypatch.setattr('sam.integration.tickets.registry.provider_from_environment',
                        lambda **_: fake)
    return fake


@pytest.fixture
def ctx(app):
    """A request context with a flushed, uncommitted mask; links cleaned up by id."""
    from webapp.extensions import db
    made = []

    def build(op='add'):
        group = make_adhoc_group(db.session)
        make_adhoc_group_tag(db.session, group, 'hpc')
        row = UnixGroupMask.create(db.session, unix_gid=group.unix_gid, kind='adhoc',
                                   username='benkirk', op=op, requested_by='benkirk',
                                   note='RC-39483')
        made.append(row.mask_id)
        return row, group

    with app.test_request_context('/'):
        yield build
        db.session.rollback()
        with Session(db.engine) as s:
            s.query(ExternalTicket).filter(ExternalTicket.entity_type == 'unix_group_mask',
                                           ExternalTicket.entity_id.in_(made)).delete()
            s.commit()


def _links(app, mask_id):
    from webapp.extensions import db
    with Session(db.engine) as s:
        return [(t.provider, t.origin, t.requested_by) for t in s.query(ExternalTicket)
                .filter_by(entity_type='unix_group_mask', entity_id=mask_id)]


def test_files_through_the_provider_and_links_the_key(app, ctx, mailer, provider):
    from webapp.dashboards.admin.group_mask_tickets import send_mask_ticket
    mask, group = ctx()
    result = send_mask_ticket(mask, group_name=group.group_name, branches=['hpc', 'hpc-data'],
                              requested_by='oper')
    assert (result.status, result.detail) == ('sent', f'{provider.prefix}-1')
    draft, = provider.created
    assert draft.handle == f'SAM-GM-{mask.mask_id}' and draft.kind == 'hpc_group'
    assert draft.summary == f'Unix group change: add benkirk to {group.group_name} [SAM-GM-{mask.mask_id}]'
    assert f'Group:        {group.group_name} (gid {group.unix_gid}, adhoc)' in draft.body
    assert 'on hpc and hpc-data at' in draft.body and 'RC-39483' in draft.body
    assert draft.link_url.endswith(f'/admin/users/group-masks?q={group.group_name}')
    entry, = mailer.ledger.rows
    assert (entry['status'], entry['key'], entry['template']) == (
        'sent', f'hpc_group_ticket:{mask.mask_id}', 'hpc_group_ticket.txt')
    assert _links(app, mask.mask_id) == [('fake', 'created', 'oper')]
    assert mailer.mailed == []


def test_a_provider_outage_mails_the_ticket(app, ctx, mailer, provider):
    from webapp.dashboards.admin.group_mask_tickets import send_mask_ticket
    provider.raise_with = TicketSourceUnavailable('down')
    mask, group = ctx(op='remove')
    result = send_mask_ticket(mask, group_name=group.group_name, branches=['hpc'], requested_by='oper')
    assert result.status == 'sent'
    message, = mailer.mailed
    assert message.recipient.address == 'help@example.invalid'
    assert message.subject.startswith(f'Unix group change: remove benkirk from {group.group_name}')
    assert _links(app, mask.mask_id) == []


def test_no_path_configured_files_nothing(app, ctx, mailer, monkeypatch):
    from webapp.dashboards.admin.group_mask_tickets import send_mask_ticket
    monkeypatch.setitem(app.config, 'NOTIFY_ACCOUNT_TICKET_TO', '')
    mask, group = ctx()
    assert send_mask_ticket(mask, group_name=group.group_name, branches=['hpc'], requested_by='o') is None
    assert mailer.mailed == [] and mailer.ledger.rows == []


def test_withdraw_comments_on_the_linked_ticket(app, ctx, mailer, provider):
    from webapp.dashboards.admin.group_mask_tickets import comment_withdrawn, send_mask_ticket
    mask, group = ctx()
    send_mask_ticket(mask, group_name=group.group_name, branches=['hpc'], requested_by='oper')
    from webapp.extensions import db
    # The link committed on its own session; db.session's snapshot predates it.
    with Session(db.engine) as fresh:
        assert comment_withdrawn(mask, by='benkirk', session=fresh) is True
    key, text, internal = provider.comments[-1]
    assert key == f'{provider.prefix}-1' and internal and 'Withdrawn in SAM by benkirk' in text


def test_the_tab_row_reads_the_linked_ticket(app, ctx, mailer, provider):
    from webapp.dashboards.admin.group_mask_tickets import send_mask_ticket
    from webapp.extensions import db
    from sam.queries.group_masks import mask_tickets
    mask, group = ctx()
    result = send_mask_ticket(mask, group_name=group.group_name, branches=['hpc'], requested_by='oper')
    assert result.status == 'sent', result
    with Session(db.engine) as fresh:
        found = mask_tickets(fresh, [mask.mask_id])[mask.mask_id]
    # The registry does not know the fake, so no URL; a stored provider's browse_url fills it.
    assert (found['key'], found['url'], found['status']) == (f'{provider.prefix}-1', '', 'Waiting for support')
