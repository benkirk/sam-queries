"""The XRAS message builder, unit-tested away from Flask.

`sam.queries.xras_notices` is what the operator's Notify button and the hourly
`xras_notices` task both call, so it is where the dedup key, the subject and
the per-kind payload are decided *once*. Keeping it out of
`webapp/dashboards/allocations/blueprint.py` is what lets the tests below take a
plain session rather than a request context.

Route-level behavior (the preview modal, the send, the activation event) stays
in `test_xras_notify.py` — it did not move and passes unedited.
"""

import json
from types import SimpleNamespace

import pytest
from factories.core import make_user
from factories.projects import make_project
from factories.xras import make_xras_action, make_xras_key_mapping

from sam.core.users import EmailAddress
from sam.queries.xras_activation import xras_dedup_key
from sam.queries.xras_notices import (
    XRAS_KIND_SUBJECTS,
    action_increments,
    build_xras_messages,
)

pytestmark = pytest.mark.unit


class TestSignedIncrements:
    """`action_increments(signed=True)` — the Adjustment mail's only number.

    The allocation now holds the NEW TOTAL and `allocation_transaction`
    records the delta without naming the XRAS action, so this payload read is
    the only place the per-resource change survives. A dropped or flipped sign
    here tells a PI their allocation grew when it shrank, and nothing
    downstream could catch it.
    """

    def _action(self, *, amount, key):
        return SimpleNamespace(raw_payload=json.dumps({
            'resources': [{'resourceRepositoryKey': key,
                           'awardedAmount': str(amount)}]}))

    @pytest.fixture
    def resource_key(self, session):
        """Any real mapping row — the helper resolves the key through it.

        A Layer-1 "any row of this shape" pick, deliberately: the sign logic
        is what is under test, not which resource carries it.
        """
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        row = session.query(XrasResourceRepositoryKeyResource).first()
        if row is None:
            pytest.skip('no xras_resource_repository_key_resource rows')
        return row.resource_repository_key

    def test_a_positive_amount_is_shown_with_an_explicit_plus(
            self, session, resource_key):
        out = action_increments(
            session, self._action(amount=50000.0, key=resource_key),
            signed=True)
        assert out and out[0]['amount'].startswith('+')

    def test_a_negative_amount_keeps_its_minus(self, session, resource_key):
        out = action_increments(
            session, self._action(amount=-100000.0, key=resource_key),
            signed=True)
        assert out and out[0]['amount'].startswith('-')

    def test_the_supplement_path_is_unsigned(self, session, resource_key):
        """A supplement's amounts are increments by construction, and its
        wording already says "Added by this request" — a '+' there would be
        noise, and changing it would move a byte in a shipped template."""
        out = action_increments(
            session, self._action(amount=50000.0, key=resource_key))
        assert out and not out[0]['amount'].startswith('+')

    def test_units_are_computed_on_the_magnitude(self, session, resource_key):
        """`allocation_unit` picks singular/plural from the value; -1 is one
        hour in either direction, so a sign must not reach it."""
        neg = action_increments(
            session, self._action(amount=-2500.0, key=resource_key),
            signed=True)
        pos = action_increments(
            session, self._action(amount=2500.0, key=resource_key),
            signed=True)
        assert neg[0]['units'] == pos[0]['units']

    @pytest.mark.parametrize('payload', [
        None, '', 'not json at all', '{"resources": []}',
        '{"resources": [{"awardedAmount": "5"}]}',              # no key
    ])
    def test_anything_unparseable_yields_nothing_rather_than_a_guess(
            self, session, payload):
        action = SimpleNamespace(raw_payload=payload)
        assert action_increments(session, action) == []

    def test_no_action_yields_nothing(self, session):
        assert action_increments(session, None) == []


@pytest.fixture
def project(session):
    """A project with a lead who has an address."""
    lead = make_user(session)
    session.add(EmailAddress(user_id=lead.user_id, email_address='pi@example.edu',
                             is_primary=True, active=True))
    session.flush()
    session.expire(lead, ['email_addresses'])
    proj = make_project(session, lead=lead)
    session.expire(proj)
    return proj


PEOPLE = [{'name': 'A PI', 'email': 'pi@example.edu', 'role': 'lead'}]


def _built(session, project, *, service, action_type, payload=None,
           requested_by='task:xras_notices'):
    action = make_xras_action(
        session, status='processed', action_type=action_type, service=service,
        request_number=project.projcode, projcode_result=project.projcode,
        payload=payload or json.dumps({'actionType': action_type}))
    messages = build_xras_messages(session, project, PEOPLE, action=action,
                                   requested_by=requested_by)
    return action, messages


class TestSharedMailboxCopies:
    """The XRAS-only addressing rides on the Message, from NOTIFY_XRAS_*."""

    def test_the_copies_and_sender_come_from_config(self, session, project,
                                                    monkeypatch):
        monkeypatch.setenv('NOTIFY_XRAS_CC', 'alloc@example.edu')
        monkeypatch.setenv('NOTIFY_XRAS_BCC', 'a@example.edu, b@example.edu')
        monkeypatch.setenv('NOTIFY_XRAS_FROM', 'alloc@example.edu')
        monkeypatch.setenv('NOTIFY_XRAS_REPLY_TO', 'alloc@example.edu')
        _, messages = _built(session, project, service='extend',
                             action_type='Extension')
        msg = messages[0]
        assert msg.cc == ('alloc@example.edu',)
        assert msg.bcc == ('a@example.edu', 'b@example.edu')
        assert (msg.sender, msg.reply_to) == ('alloc@example.edu',
                                              'alloc@example.edu')

    def test_unset_means_no_copies_and_the_site_sender(self, session, project,
                                                       monkeypatch):
        for name in ('NOTIFY_XRAS_CC', 'NOTIFY_XRAS_BCC', 'NOTIFY_XRAS_FROM',
                     'NOTIFY_XRAS_REPLY_TO'):
            monkeypatch.delenv(name, raising=False)
        _, messages = _built(session, project, service='extend',
                             action_type='Extension')
        msg = messages[0]
        assert (msg.cc, msg.bcc, msg.sender, msg.reply_to) == ((), (), None, None)

    def test_the_dedup_key_ignores_the_copies(self, session, project, monkeypatch):
        monkeypatch.setenv('NOTIFY_XRAS_CC', 'alloc@example.edu')
        action, messages = _built(session, project, service='extend',
                                  action_type='Extension')
        assert messages[0].dedup_key == xras_dedup_key(
            'xras_extension', project.projcode, action.xras_action_log_id,
            'pi@example.edu')


class TestOneMessagePerRecipient:

    def test_the_dedup_key_is_the_one_the_card_reads_back(self, session, project):
        """Built through `xras_dedup_key`, never an f-string: the activity
        table finds these rows by *parsing* that key, and the manual Notify
        button mints the identical one — which is the entire reason the button
        and the task cannot double-mail."""
        action, (message,) = _built(session, project, service='extend',
                                    action_type='Extension')
        assert message.dedup_key == xras_dedup_key(
            'xras_extension', project.projcode, action.xras_action_log_id,
            'pi@example.edu')

    def test_requested_by_is_carried_through_verbatim(self, session, project):
        """It is what the admin card renders as "who asked". The route passes a
        username; the task passes a `task:` sentinel."""
        _action, (message,) = _built(session, project, service='update',
                                     action_type='Renewal',
                                     requested_by='benkirk')
        assert message.requested_by == 'benkirk'

    def test_it_carries_the_project_and_entity(self, session, project):
        _action, (message,) = _built(session, project, service='extend',
                                     action_type='Extension')
        assert message.projcode == project.projcode
        assert message.entity == ('project', project.project_id)


class TestTheSubjectPerKind:
    """The subject is also `notification_log.subject`, which an operator reads
    back in the admin log — a subject assembled inside a template could not be
    searched from SQL."""

    @pytest.mark.parametrize('service,action_type,kind', [
        ('update', 'Renewal', 'xras_update'),
        ('extend', 'Extension', 'xras_extension'),
        ('supplement', 'Supplement', 'xras_supplement'),
        ('adjust', 'Adjustment', 'xras_adjustment'),
        ('add', 'New', 'xras_activation'),
    ])
    def test_each_service_gets_its_own_wording(self, session, project,
                                               service, action_type, kind):
        _action, (message,) = _built(session, project, service=service,
                                     action_type=action_type)
        assert message.kind == kind
        assert message.subject == XRAS_KIND_SUBJECTS[kind].format(
            projcode=project.projcode)

    def test_the_adjustment_subject_promises_no_direction(self):
        """An Adjustment can subtract, and a subject line promising good news
        is read long before the body corrects it."""
        subject = XRAS_KIND_SUBJECTS['xras_adjustment']
        for word in ('additional', 'extended', 'increase', 'more'):
            assert word not in subject.lower()

    def test_an_unmapped_service_falls_back_rather_than_raising(self, session,
                                                                project):
        _action, (message,) = _built(session, project, service='transfer',
                                     action_type='Transfer')
        assert message.kind == 'xras_activation'


class TestTheIncrementsGoToTheRightSlot:
    """Every kind carries both keys, because a template that renders an
    undefined name renders nothing, silently."""

    def _payload(self, key, amount):
        return json.dumps({'resources': [
            {'resourceRepositoryKey': key, 'awardedAmount': str(amount)}]})

    @pytest.fixture
    def key(self, session):
        return make_xras_key_mapping(session).xras_key

    def test_a_supplement_fills_added_and_leaves_changes_empty(self, session,
                                                               project, key):
        _action, (message,) = _built(session, project, service='supplement',
                                     action_type='Supplement',
                                     payload=self._payload(key, 50_000))
        assert message.context['added'], 'a supplement must say how much'
        assert message.context['changes'] == []
        assert not message.context['added'][0]['amount'].startswith('+')

    def test_an_adjustment_fills_changes_signed_and_leaves_added_empty(
            self, session, project, key):
        """`added` is a promise that every number in it is an increase, which
        the supplement wording leans on. An adjustment makes no such promise."""
        _action, (message,) = _built(session, project, service='adjust',
                                     action_type='Adjustment',
                                     payload=self._payload(key, 50_000))
        assert message.context['added'] == []
        assert message.context['changes'][0]['amount'].startswith('+')

    def test_an_extension_carries_both_keys_empty(self, session, project, key):
        _action, (message,) = _built(session, project, service='extend',
                                     action_type='Extension',
                                     payload=self._payload(key, 50_000))
        assert message.context['added'] == []
        assert message.context['changes'] == []
