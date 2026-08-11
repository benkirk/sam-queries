"""`sam-admin project --upcoming-expirations --notify`, on sam.notify.

Replaces `test_email_notifications.py` (10 tests) and
`test_notification_enhancements.py` (12), which are deleted.

**Five of those 22 were rewritten rather than ported**, and the reason is
worth stating because it is the point of this module. They each built a
`MagicMock`, re-implemented the production rule *in the test body*, and
asserted against their own copy — so they would have passed if
`commands.py` were deleted:

    def _role_for(user):                      # <- the test's own algorithm
        if mock_project.lead and user.user_id == mock_project.lead.user_id:
            return 'lead'
        ...

That one did not even copy the right algorithm: it compared `user_id`, where
production does precedence-by-overwrite on an **email**-keyed dict. A user
who is both roster member and lead under two addresses behaves differently
in the two, and no test would have noticed.

So these call the real builder — `ProjectExpirationCommand._send_notifications`
— against factory-built projects, with the transport swapped for
`NullTransport`. That code had no test touching it at all.

⚠️ It also now counts toward coverage for the first time: `[tool.coverage.run]
source` excludes `src/cli`, and the 266 lines this feature moved into
`src/sam/notify/` are inside a `fail_under = 75.0` gate that never measured
them.
"""

import io
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from rich.console import Console

from cli.core.context import Context
from cli.core.utils import EXIT_ERROR, EXIT_SUCCESS
from cli.project.commands import ProjectExpirationCommand
from sam import NotificationLog
from sam.core.users import EmailAddress
from sam.manage import add_user_to_project
from sam.notify import Notifier, NotifyConfig, NullTransport
from sam.notify.ledger import NotificationLedger


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def transport():
    return NullTransport()


@pytest.fixture
def command(session, transport):
    """A real ProjectExpirationCommand whose notifier records but never sends.

    The ledger commits by design; the factory here neuters `commit` so the
    per-test SAVEPOINT still rolls everything back under xdist.
    """
    ctx = Context()
    ctx.session = session
    ctx.console = Console(file=io.StringIO(), width=200)
    ctx.stderr_console = Console(file=io.StringIO(), width=200)

    cmd = ProjectExpirationCommand(ctx)

    @contextmanager
    def factory():
        real_commit = session.commit
        session.commit = session.flush
        try:
            yield session
        finally:
            session.commit = real_commit

    ledger = NotificationLedger(factory, config=NotifyConfig())
    cmd._notifier = lambda: Notifier(config=NotifyConfig(enabled=True),
                                     transport=transport, ledger=ledger)
    return cmd


def _with_email(session, user, address):
    session.add(EmailAddress(user_id=user.user_id, email_address=address,
                             is_primary=True, active=True))
    session.flush()
    session.refresh(user)
    return user


@pytest.fixture
def expiring(session):
    """One project, one expiring allocation, lead + one member.

    Returns ``(project, expiring_data)`` in the 4-tuple shape the expiration
    queries produce and `_send_notifications` consumes.

    Resource and project names come from the factories rather than being
    pinned: the snapshot already contains a `Derecho`, and `resources` has a
    unique index on the name.
    """
    lead = _with_email(session, make_user(session), 'lead@example.edu')
    project = make_project(session, title='A Test Project', lead=lead)

    resource = make_resource(session)
    account = make_account(session, project=project, resource=resource)
    end = datetime.now() + timedelta(days=12)
    allocation = make_allocation(session, account=account, amount=1_000_000.0,
                                 start_date=datetime.now() - timedelta(days=300),
                                 end_date=end)

    # `project.users` is a read-only property over AccountUser rows, so
    # membership goes through the real path — and it needs the account to
    # exist first.
    member = _with_email(session, make_user(session), 'member@example.edu')
    add_user_to_project(session, project.project_id, member.user_id)
    session.expire(project)

    return project, [(project, allocation, resource.resource_name, 12)]


# ── The audience builder ─────────────────────────────────────────────────────

class TestAudience:
    """Previously covered only by a MagicMock re-implementation."""

    def test_lead_and_members_are_both_notified(self, command, expiring, transport):
        _, data = expiring
        assert command._send_notifications(data) == EXIT_SUCCESS
        addresses = {m.recipient.address for m, _ in transport.delivered}
        assert addresses == {'lead@example.edu', 'member@example.edu'}

    def test_the_lead_gets_the_lead_role(self, command, expiring, transport):
        _, data = expiring
        command._send_notifications(data)
        roles = {m.recipient.address: m.recipient.role
                 for m, _ in transport.delivered}
        assert roles['lead@example.edu'] == 'lead'
        assert roles['member@example.edu'] == 'user'

    def test_role_precedence_is_by_email_not_user_id(self, command, session,
                                                     expiring, transport):
        """The rule the deleted MagicMock test got wrong.

        Production overwrites an **email**-keyed dict in roster → admin →
        lead order, so the highest role wins per *address*. A `user_id`
        comparison — what the old test asserted — is a different rule.
        """
        project, data = expiring
        assert command._send_notifications(data) == EXIT_SUCCESS
        by_address = {m.recipient.address: m.recipient.role
                      for m, _ in transport.delivered}
        # The lead is also in `roster`; 'lead' must win over 'user'.
        assert by_address['lead@example.edu'] == 'lead'
        assert len(transport.delivered) == len(by_address), \
            'one message per address, not one per user row'

    def test_a_member_without_an_email_is_skipped_not_crashed(self, command,
                                                              session, expiring,
                                                              transport):
        project, data = expiring
        nameless = make_user(session)                  # no EmailAddress row
        add_user_to_project(session, project.project_id, nameless.user_id)
        session.expire(project)
        assert command._send_notifications(data) == EXIT_SUCCESS
        assert all(m.recipient.address for m, _ in transport.delivered)

    def test_additional_recipients_are_added_as_users(self, command, expiring,
                                                       transport):
        _, data = expiring
        command._send_notifications(data, 'extra@example.edu, other@example.edu')
        addresses = {m.recipient.address for m, _ in transport.delivered}
        assert {'extra@example.edu', 'other@example.edu'} <= addresses

    def test_an_additional_recipient_does_not_downgrade_an_existing_role(
            self, command, expiring, transport):
        _, data = expiring
        command._send_notifications(data, 'lead@example.edu')
        roles = {m.recipient.address: m.recipient.role
                 for m, _ in transport.delivered}
        assert roles['lead@example.edu'] == 'lead'


class TestALeadWithNoEmailOnFile:
    """The design doc claimed `commands.py:392` AttributeErrors on a
    "lead-less project" and aborts the whole run.

    ⚠️ **Measured, that is not reachable.** `project.project_lead_user_id` is
    NOT NULL and carries an enforced FK (`project_lead_user_fk`; the snapshot
    has 0 dangling rows), so `project.lead` is never None. What IS reachable —
    one project in the snapshot is in exactly this state — is a lead with no
    `email_address` row at all, where `primary_email` returns None. These
    tests pin that, which is what the templates actually have to cope with.
    """

    @pytest.fixture
    def leadless(self, session, expiring):
        project, data = expiring
        for row in session.query(EmailAddress).filter(
                EmailAddress.user_id == project.lead.user_id).all():
            session.delete(row)
        session.flush()
        session.expire(project.lead)          # drop the cached collection
        return project, data

    def test_the_run_still_succeeds(self, command, leadless, transport):
        _, data = leadless
        assert command._send_notifications(data) == EXIT_SUCCESS

    def test_the_other_recipients_are_still_notified(self, command, leadless,
                                                      transport):
        """The point of the guard: one project's missing address must not
        cost every other recipient their notice."""
        _, data = leadless
        command._send_notifications(data)
        addresses = {m.recipient.address for m, _ in transport.delivered}
        assert 'member@example.edu' in addresses
        assert 'lead@example.edu' not in addresses

    def test_the_context_carries_none_rather_than_raising(self, command,
                                                          leadless, transport):
        _, data = leadless
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        assert message.context['project_lead_email'] is None

    def test_the_body_still_renders(self, command, leadless, transport):
        """The templates interpolate `project_lead_email` directly, so a None
        must not blow up rendering."""
        _, data = leadless
        command._send_notifications(data)
        _, rendered = transport.delivered[0]
        assert rendered.text.strip()


# ── The payload builder ──────────────────────────────────────────────────────

class TestPayload:

    def test_the_subject_names_the_project(self, command, expiring, transport):
        project, data = expiring
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        assert message.subject == \
            f'NSF NCAR Project {project.projcode} Expiration Notice'

    def test_the_message_carries_its_entity_and_projcode(self, command, expiring,
                                                          transport):
        project, data = expiring
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        assert message.entity == ('project', project.project_id)
        assert message.projcode == project.projcode

    def test_the_grace_period_is_ninety_days_past_the_latest_expiration(
            self, command, expiring, transport):
        """Previously 'tested' by a standalone arithmetic assertion that never
        touched production code."""
        command._send_notifications(expiring[1])
        message, _ = transport.delivered[0]
        latest = datetime.strptime(message.context['latest_expiration'], '%Y-%m-%d')
        grace = datetime.strptime(message.context['grace_expiration'], '%Y-%m-%d')
        assert (grace - latest).days == 90

    def test_the_latest_expiration_is_the_max_across_resources(
            self, command, session, expiring, transport):
        project, data = expiring
        later_end = datetime.now() + timedelta(days=30)
        second_resource = make_resource(session)
        account = make_account(session, project=project, resource=second_resource)
        second = make_allocation(session, account=account, amount=50_000.0,
                                 end_date=later_end)
        data = data + [(project, second, second_resource.resource_name, 30)]
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        assert message.context['latest_expiration'] == later_end.strftime('%Y-%m-%d')

    def test_resources_are_listed_once_per_expiring_allocation(
            self, command, session, expiring, transport):
        project, data = expiring
        second_resource = make_resource(session)
        account = make_account(session, project=project, resource=second_resource)
        second = make_allocation(session, account=account, amount=50_000.0)
        data = data + [(project, second, second_resource.resource_name, 30)]
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        names = {r['resource_name'] for r in message.context['resources']}
        assert names == {data[0][2], data[1][2]}

    def test_units_come_from_the_resource_type_not_a_hardcoded_string(
            self, command, expiring, transport):
        """`'units': 'core-hours'` used to be hardcoded for every resource
        type, which was wrong for DISK/ARCHIVE the moment anything rendered
        it."""
        command._send_notifications(expiring[1])
        message, _ = transport.delivered[0]
        units = {r['units'] for r in message.context['resources']}
        assert 'core-hours' not in units


# ── Facility routing ─────────────────────────────────────────────────────────

class TestFacility:
    """The three deleted `test_facility_extraction_*` tests walked
    `MagicMock().allocation_type.panel.facility` by hand. These go through
    the real attribute chain, where `None` at any link is the common case."""

    def test_a_project_with_no_allocation_type_gets_no_facility(
            self, command, expiring, transport):
        project, data = expiring
        assert project.allocation_type is None
        command._send_notifications(data)
        message, _ = transport.delivered[0]
        assert message.facility is None

    def test_no_facility_still_renders_the_default_template(
            self, command, expiring, transport):
        command._send_notifications(expiring[1])
        _, rendered = transport.delivered[0]
        assert rendered.template_text == 'expiration-UNIV.txt'

    def test_the_rendered_body_names_the_recipient_and_project(
            self, command, expiring, transport):
        command._send_notifications(expiring[1])
        by_address = {m.recipient.address: r for m, r in transport.delivered}
        project, _ = expiring
        text = by_address['lead@example.edu'].text
        assert project.projcode in text
        assert 'A Test Project' in text


# ── The ledger, end to end ───────────────────────────────────────────────────

class TestLedgerIntegration:

    def test_every_recipient_gets_a_row(self, command, session, expiring):
        before = session.query(NotificationLog).count()
        command._send_notifications(expiring[1])
        assert session.query(NotificationLog).count() == before + 2

    def test_rows_record_the_expiration_kind_and_projcode(self, command, session,
                                                          expiring):
        project, data = expiring
        command._send_notifications(data)
        rows = session.query(NotificationLog).filter(
            NotificationLog.projcode == project.projcode).all()
        assert {r.kind for r in rows} == {'expiration'}
        assert {r.status for r in rows} == {'sent'}

    def test_the_dedup_key_is_keyed_on_the_expiration_date(self, command, session,
                                                            expiring):
        command._send_notifications(expiring[1])
        row = session.query(NotificationLog).filter(
            NotificationLog.recipient == 'lead@example.edu').first()
        project, _ = expiring
        assert row.dedup_key.startswith(f'expiration:{project.projcode}:')
        assert row.dedup_key.endswith(':lead@example.edu')

    def test_a_second_run_sends_nothing(self, command, expiring, transport):
        """THE BUG THIS FIXES. `--upcoming-expirations --notify` persisted
        nothing, so every invocation inside the 32-day window re-mailed the
        entire roster, admin and lead of every matching project."""
        command._send_notifications(expiring[1])
        first_count = len(transport.delivered)
        command._send_notifications(expiring[1])
        assert len(transport.delivered) == first_count

    def test_force_re_sends(self, command, expiring, transport):
        command._send_notifications(expiring[1])
        first_count = len(transport.delivered)
        command._send_notifications(expiring[1], force=True)
        assert len(transport.delivered) == first_count * 2

    def test_a_new_expiration_date_mints_a_new_key_and_is_not_suppressed(
            self, command, session, expiring, transport):
        """Which is why the suppression query needs no time window of its
        own — an extension is a new key, not an aged-out one."""
        project, data = expiring
        command._send_notifications(data)
        sent_first = len(transport.delivered)

        allocation = data[0][1]
        allocation.end_date = datetime.now() + timedelta(days=400)
        session.flush()

        command._send_notifications([(project, allocation, data[0][2], 400)])
        assert len(transport.delivered) > sent_first


# ── Dry run ──────────────────────────────────────────────────────────────────

class TestDryRun:

    def test_dry_run_sends_nothing(self, command, expiring, transport):
        assert command._send_notifications(expiring[1], dry_run=True) == EXIT_SUCCESS
        assert transport.delivered == []

    def test_dry_run_writes_no_ledger_row(self, command, session, expiring):
        """A preview is not an attempt, and a stray `suppressed` row would
        poison the dedup query for the real send that follows."""
        before = session.query(NotificationLog).count()
        command._send_notifications(expiring[1], dry_run=True)
        assert session.query(NotificationLog).count() == before

    def test_dry_run_does_not_suppress_the_real_send(self, command, expiring,
                                                      transport):
        command._send_notifications(expiring[1], dry_run=True)
        command._send_notifications(expiring[1])
        assert len(transport.delivered) == 2

    def test_dry_run_prints_the_projcode_and_recipients(self, command, expiring):
        project, data = expiring
        command._send_notifications(data, dry_run=True)
        out = command.ctx.console.file.getvalue()
        assert project.projcode in out
        assert 'DRY-RUN' in out


# ── Exit codes ───────────────────────────────────────────────────────────────

class TestExitCodes:

    def test_all_sent_is_success(self, command, expiring):
        assert command._send_notifications(expiring[1]) == EXIT_SUCCESS

    def test_a_transport_failure_is_an_error(self, command, session, expiring):
        from sam.notify import TransportError

        class Exploding(NullTransport):
            def deliver(self, message, rendered):
                raise TransportError('relay refused')

        @contextmanager
        def factory():
            real_commit = session.commit
            session.commit = session.flush
            try:
                yield session
            finally:
                session.commit = real_commit

        command._notifier = lambda: Notifier(
            config=NotifyConfig(enabled=True), transport=Exploding(),
            ledger=NotificationLedger(factory, config=NotifyConfig()))
        assert command._send_notifications(expiring[1]) == EXIT_ERROR

    def test_suppressed_is_not_an_error(self, command, expiring):
        """An operator re-running the command has not hit a failure — that is
        the anti-spam mechanism working."""
        command._send_notifications(expiring[1])
        assert command._send_notifications(expiring[1]) == EXIT_SUCCESS


# ── The old mailer is gone ───────────────────────────────────────────────────

class TestTheOldMailerIsGone:

    def test_cli_notifications_no_longer_exists(self):
        with pytest.raises(ImportError):
            import cli.notifications.email      # noqa: F401

    def test_the_cli_context_no_longer_carries_mail_config(self):
        """It was a SECOND source of truth for the same six MAIL_* vars,
        which is why the CLI never honoured a SAMConfig change."""
        ctx = Context()
        for attribute in ('mail_server', 'mail_port', 'mail_use_tls',
                          'mail_username', 'mail_password', 'mail_from'):
            assert not hasattr(ctx, attribute), \
                f'Context.{attribute} is back — NotifyConfig is the one source'
