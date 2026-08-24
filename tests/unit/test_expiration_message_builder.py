"""`sam.queries.expiration_notices` — the ladder, and the key it mints.

`test_expiration_notices.py` covers the builder's *behavior* end-to-end
through the CLI command, and passed unmodified across the extraction — that
is the proof the move was pure. This module covers the surface the extraction
ADDED: the `Milestone` band type, the rung label in the dedup key, and the
legacy-key bridge.

The dedup key is the thing worth testing hardest. It is the only thing
standing between a weekly schedule and a PI getting the same notice 5-6 weeks
in a row.
"""

from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.core.users import EmailAddress
from sam.queries.expiration_notices import (
    MILESTONES, Milestone, build_expiration_messages, dedup_key,
    legacy_dedup_key,
)


def _with_email(session, user, address):
    session.add(EmailAddress(user_id=user.user_id, email_address=address,
                             is_primary=True, active=True))
    session.flush()
    session.refresh(user)
    return user


@pytest.fixture
def expiring(session):
    """One project, one allocation expiring in 12 days, a lead with an address.

    The 4-tuple shape the expiration queries produce and the builder consumes.
    Resource and project names come from the factories rather than being
    pinned — the snapshot already has a `Derecho`, and `resources` has a
    unique index on the name.
    """
    lead = _with_email(session, make_user(session), 'lead@example.edu')
    project = make_project(session, title='A Test Project', lead=lead)
    resource = make_resource(session)
    account = make_account(session, project=project, resource=resource)
    allocation = make_allocation(
        session, account=account, amount=1_000_000.0,
        start_date=datetime.now() - timedelta(days=300),
        end_date=datetime.now() + timedelta(days=12))
    return project, [(project, allocation, resource.resource_name, 12)]


def _build(expiring, milestone=None, **kwargs):
    kwargs.setdefault('requested_by', 'pytest')
    return build_expiration_messages(
        expiring[1], milestone=milestone or MILESTONES[0], **kwargs)


class TestNoSharedMailboxCopies:

    def test_the_xras_addressing_never_reaches_an_expiration_notice(
            self, expiring, monkeypatch):
        monkeypatch.setenv('NOTIFY_XRAS_CC', 'alloc@example.edu')
        monkeypatch.setenv('NOTIFY_XRAS_FROM', 'alloc@example.edu')
        msg = _build(expiring)[0]
        assert (msg.cc, msg.bcc, msg.sender, msg.reply_to) == ((), (), None, None)


# The band type

class TestMilestone:

    def test_a_band_needs_positive_width(self):
        """A zero- or negative-width band selects nothing and would look
        exactly like a query that stopped matching."""
        with pytest.raises(ValueError, match='must exceed'):
            Milestone('bad', 30, 30)
        with pytest.raises(ValueError, match='must exceed'):
            Milestone('backwards', 30, 7)

    def test_a_band_needs_a_label(self):
        """The label is IN the dedup key, so an empty one silently changes
        the key format rather than failing."""
        with pytest.raises(ValueError, match='label'):
            Milestone('', 0, 40)

    def test_it_is_hashable_and_frozen(self):
        rung = Milestone('30d', 28, 35)
        assert {rung, Milestone('30d', 28, 35)} == {rung}
        with pytest.raises(Exception):
            rung.label = 'mutated'


class TestTheShippedLadder:

    def test_exactly_one_rung_ships_today(self):
        """One notice per expiration, reproducing what the manual monthly run
        produced. Enabling 60/30/7 is a one-tuple edit — and must stay one,
        which is what the rest of this file is protecting."""
        assert len(MILESTONES) == 1
        assert MILESTONES[0] == Milestone('expiring', 0, 40)

    def test_the_runway_is_wider_than_the_weekly_run_gap(self):
        """A band narrower than the gap between runs drops expirations
        between rungs — the same failure mode as the old 32-day window under
        a monthly cadence."""
        for rung in MILESTONES:
            assert rung.hi_days - rung.lo_days >= 7

    def test_the_bands_do_not_overlap(self):
        ordered = sorted(MILESTONES, key=lambda m: m.lo_days)
        for lower, higher in zip(ordered, ordered[1:]):
            assert lower.hi_days <= higher.lo_days

    def test_the_labels_are_unique(self):
        """Two rungs sharing a label share a dedup key, so the first would
        suppress the second — § 12's warning, made a test."""
        labels = [m.label for m in MILESTONES]
        assert len(set(labels)) == len(labels)


# The key

class TestTheDedupKey:

    def test_the_format_carries_the_rung_label(self):
        assert dedup_key('SCSG0001', '2026-09-30', 'expiring', 'pi@x.edu') == \
            'expiration:SCSG0001:2026-09-30:expiring:pi@x.edu'

    def test_the_legacy_format_is_the_same_key_without_the_label(self):
        """The pre-rung-label form still sitting in `notification_log`. The
        task checks both, so the overlap cohort is not notified twice."""
        assert legacy_dedup_key('SCSG0001', '2026-09-30', 'pi@x.edu') == \
            'expiration:SCSG0001:2026-09-30:pi@x.edu'

    def test_the_two_formats_are_distinguishable(self):
        """If they collided, the bridge would be a no-op and nobody would
        notice until the duplicate notices went out."""
        assert dedup_key('P', '2026-09-30', 'expiring', 'pi@x.edu') != \
            legacy_dedup_key('P', '2026-09-30', 'pi@x.edu')

    def test_the_builder_mints_the_new_format(self, expiring):
        messages = _build(expiring)
        key = next(m.dedup_key for m in messages
                   if m.recipient.address == 'lead@example.edu')
        assert key.split(':')[3] == 'expiring'
        assert key.endswith(':lead@example.edu')

    def test_a_different_rung_mints_a_different_key(self, expiring):
        """THE property the ladder depends on. Without the label in the key
        the 60-day rung would suppress the 30-day one and the 7-day one, and
        a PI would get exactly one notice however many rungs were configured
        — § 12's stated hazard."""
        keys = {
            rung.label: {m.dedup_key for m in _build(expiring, rung)}
            for rung in (Milestone('60d', 56, 63), Milestone('30d', 28, 35),
                         Milestone('7d', 7, 14))
        }
        assert len(set().union(*keys.values())) == sum(len(v) for v in keys.values())

    def test_the_expiration_date_is_still_in_the_key(self, expiring, session):
        """Unchanged by the label: a renewal must still mint a new key, or an
        extended project is never told about its NEW expiration."""
        project, data = expiring
        before = {m.dedup_key for m in _build(expiring)}

        data[0][1].end_date = datetime.now() + timedelta(days=400)
        session.flush()
        after = {m.dedup_key for m in
                 _build((project, [(project, data[0][1], data[0][2], 400)]))}
        assert before.isdisjoint(after)


class TestTheMilestoneReachesTheTemplate:

    def test_the_rung_label_is_in_the_context(self, expiring):
        """So a future 7-day template can say "one week" without the sender
        having to infer it from days_remaining."""
        for message in _build(expiring, Milestone('7d', 7, 14)):
            assert message.context['milestone'] == '7d'


class TestDeterminism:

    def test_two_builds_of_the_same_input_are_identical(self, expiring):
        """A scheduled sender re-runs after a crash; a run whose message
        order or key set wandered would re-send whatever moved."""
        first = _build(expiring)
        second = _build(expiring)
        assert [m.dedup_key for m in first] == [m.dedup_key for m in second]

    def test_an_empty_selection_builds_nothing(self):
        """The quiet week. Must not raise, and must not open anything."""
        assert build_expiration_messages([], requested_by='pytest',
                                         milestone=MILESTONES[0]) == []
