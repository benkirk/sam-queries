"""One ``roles[]`` array, two readings, and the defect that falls out of the gap.

The two readings share an end-date rule and differ on the begin-date rule, and the
difference is not symmetric: role assignment's exclusion is the roster's conjoined with
two further conditions, so it excludes a strict subset. That asymmetry is legacy defect
3 — a person can be made project lead while being excluded from the project's own
roster — and :class:`TestDefectThree` asserts both the behavior and the one-directional
shape of it, rather than trusting the argument.

``adjustment_uwis0064_manual.json`` is the live case in the corpus. Every role on it
begins in 2025 against a 2021 action date, so the roster comes back **empty** while
both role assignments resolve.

Because the role-assignment rule reads the *current date*, every test that touches it
pins ``today`` explicitly. A test that let it float would pass or fail depending on
when it ran.

See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *The roster*.
"""


import pytest

from sam.xras.errors import ActionErrors
from sam.xras.roster import (

    ALLOCATION_MANAGER_ROLE,
    PI_ROLE,
    Roster,
    normalize_username,
    resolve_roster,
    role_assignment_disagreements,
    role_candidates,
    roster_usernames,
)

from xras_helpers import FIXTURE_DIR, load_fixture

pytestmark = pytest.mark.unit


def role(role_type='PI', username='alice', begin='2026-01-01', end=None):
    return {'roleType': role_type, 'username': username,
            'beginDate': begin, 'endDate': end}


def act(*roles, action_begin='2026-06-01'):
    return {'actionId': 1, 'actionBeginDate': action_begin, 'roles': list(roles)}


# ---------------------------------------------------------------------------
# The roster reading — roleType is never examined.
# ---------------------------------------------------------------------------


class TestRosterIgnoresRoleType:

    def test_every_role_type_becomes_a_member(self):
        """``getUsernames()`` never looks at ``roleType``. ``ActionRoleName`` has only
        two constants, so a ``Co-PI`` or a ``User`` is invisible to role *assignment* —
        but is still added to the project."""
        assert roster_usernames(act(
            role(PI_ROLE, 'alice'),
            role('User', 'bob'),
            role('Co-PI', 'carol'),
            role(ALLOCATION_MANAGER_ROLE, 'dave'),
        )) == ('alice', 'bob', 'carol', 'dave')

    def test_the_corpus_proves_it(self):
        """NCAR4232 carries a ``User`` entry alongside its PI and Allocation Manager."""
        data = load_fixture('new_ncar4232_failed.json')
        assert {r['roleType'] for r in data['roles']} == {
            'PI', 'User', 'Allocation Manager'}
        # Two distinct humans across three roles, all inside the window.
        assert roster_usernames(data) == (
            'placeholder66-user-00066', 'user_00000067')

    def test_a_role_beginning_after_the_action_is_strictly_excluded(self):
        assert roster_usernames(act(
            role(username='alice', begin='2026-06-02'),
            action_begin='2026-06-01')) == ()

    def test_a_role_beginning_on_the_action_date_is_included(self):
        """``> 0``, not ``>= 0`` — the boundary is inclusive."""
        assert roster_usernames(act(
            role(username='alice', begin='2026-06-01'),
            action_begin='2026-06-01')) == ('alice',)

    def test_a_role_ending_before_the_action_is_excluded(self):
        assert roster_usernames(act(
            role(username='alice', end='2026-05-31'),
            action_begin='2026-06-01')) == ()

    def test_a_role_ending_on_the_action_date_is_included(self):
        assert roster_usernames(act(
            role(username='alice', end='2026-06-01'),
            action_begin='2026-06-01')) == ('alice',)

    def test_duplicates_collapse_although_legacy_leaves_them(self):
        """Legacy's list carries one human twice when they hold two roles.
        ``Account.assign`` is idempotent so it is harmless there, but it would double
        every ``Username %s is missing`` and makes the count meaningless."""
        assert roster_usernames(act(
            role(PI_ROLE, 'alice'),
            role(ALLOCATION_MANAGER_ROLE, 'alice'),
        )) == ('alice',)

    def test_order_is_preserved(self):
        assert roster_usernames(act(
            role(username='zoe'), role(username='alice'))) == ('zoe', 'alice')

    def test_an_empty_roles_array_yields_nothing_rather_than_raising(self):
        assert roster_usernames(act()) == ()

    def test_a_blank_username_still_produces_an_entry(self):
        """Legacy reports ``Username  is missing`` — two spaces — for a role with no
        person attached. Swallowing the entry would hide that."""
        assert roster_usernames(act(role(username=None))) == ('',)


# ---------------------------------------------------------------------------
# The role-assignment reading.
# ---------------------------------------------------------------------------


class TestRoleAssignment:

    def test_the_role_type_must_match_exactly(self):
        """``String.equals`` — case- and space-exact. ``AllocationManager`` is the GET
        side's spelling for the same concept and does not match here."""
        action = act(
            role('pi', 'alice'),
            role('AllocationManager', 'bob'),
            role('Allocation Manager', 'carol'),
        )
        assert role_candidates(action, PI_ROLE, today='2026-06-15') == ()
        assert role_candidates(action, ALLOCATION_MANAGER_ROLE,
                               today='2026-06-15') == ('carol',)

    def test_all_survivors_are_returned_not_just_the_first(self):
        """Legacy returns the first and discards the rest — defect 1. Returning them
        all is what lets an ambiguous action be rejected instead of coin-flipped."""
        assert role_candidates(
            act(role(PI_ROLE, 'alice'), role(PI_ROLE, 'bob')),
            PI_ROLE, today='2026-06-15') == ('alice', 'bob')

    def test_the_end_date_rule_is_the_rosters(self):
        action = act(role(PI_ROLE, 'alice', end='2026-05-31'),
                     action_begin='2026-06-01')
        assert role_candidates(action, PI_ROLE, today='2026-06-15') == ()
        assert roster_usernames(action) == ()

    def test_a_future_role_on_a_future_action_is_excluded(self):
        """All three conjuncts hold: the role begins after the action, and today is at
        or before both."""
        assert role_candidates(
            act(role(PI_ROLE, 'alice', begin='2026-07-01'), action_begin='2026-06-01'),
            PI_ROLE, today='2026-05-01') == ()

    def test_a_future_role_on_a_past_action_is_accepted(self):
        """``currDate <= actionDate`` fails, so the conjunct fails and the role stands.
        This is the arm that produces defect 3."""
        assert role_candidates(
            act(role(PI_ROLE, 'alice', begin='2026-07-01'), action_begin='2026-06-01'),
            PI_ROLE, today='2026-06-15') == ('alice',)


class TestDefectThree:
    """A future-dated role can lead a project it has no account on."""

    def test_the_two_readings_disagree_on_a_past_action(self):
        action = act(role(PI_ROLE, 'alice', begin='2026-07-01'),
                     action_begin='2026-06-01')
        assert role_candidates(action, PI_ROLE, today='2026-06-15') == ('alice',)
        assert roster_usernames(action) == ()
        assert role_assignment_disagreements(action, today='2026-06-15') == ('alice',)

    def test_the_corpus_carries_a_live_case(self):
        """UWIS0064: every role begins 2025-08-06 against a 2021-08-15 action date. The
        roster is empty; both role assignments resolve. Any ``today`` after 2025-08-06
        gives this result, so the assertion is stable."""
        data = load_fixture('adjustment_uwis0064_manual.json')
        assert roster_usernames(data) == ()
        assert role_candidates(data, PI_ROLE, today='2026-08-07') == ('user_00000002',)
        assert role_candidates(data, ALLOCATION_MANAGER_ROLE,
                               today='2026-08-07') == ('user_00000001',)
        assert role_assignment_disagreements(data, today='2026-08-07') == (
            'user_00000001', 'user_00000002')

    def test_the_disagreement_is_one_directional(self):
        """Role assignment's begin-date exclusion is the roster's conjoined with two
        further conditions, so it excludes a strict subset — a role can be assigned but
        not rostered, never the reverse. Asserted across a grid rather than argued."""
        for begin in ('2026-05-01', '2026-06-01', '2026-07-01'):
            for action_begin in ('2026-05-15', '2026-06-15'):
                for end in (None, '2026-06-01', '2026-12-31'):
                    for today in ('2026-04-01', '2026-06-20', '2027-01-01'):
                        action = act(role(PI_ROLE, 'alice', begin=begin, end=end),
                                     action_begin=action_begin)
                        members = set(roster_usernames(action))
                        assigned = set(role_candidates(action, PI_ROLE, today=today))
                        assert members <= assigned, (begin, action_begin, end, today)

    def test_agreement_produces_no_warning(self):
        action = act(role(PI_ROLE, 'alice'), action_begin='2026-06-01')
        assert role_assignment_disagreements(action, today='2026-06-15') == ()


class TestNoCorpusPayloadHasAnAmbiguousRole:
    """Ambiguous roles **do** occur in production traffic — just never where it counts.

    ⚠️ **Corrected by the 2026-08-11 forward.** At eight payloads no payload named two
    current holders of a lead role, and this class asserted that flatly. At 41, two do
    (:data:`AMBIGUOUS`).

    It still does not bite, and the reason is structural rather than lucky: only the
    Add and Update handlers resolve a roster. Extension reads ``actionEndDate`` alone
    and ignores ``roles[]`` entirely, and ``Date Adjustment`` parks before any handler
    runs. **Zero of the sixteen payloads that route to add/update are ambiguous**, and
    that — not the corpus-wide claim — is the property worth guarding.

    Worth knowing before the cutover rather than after, because ``ambiguous_role`` is
    one of the three error strings SAM added that legacy does not have: legacy picks
    by array order and never reports it. If one of these shapes ever arrives on a New,
    SAM 422s where legacy would have silently chosen.
    """

    #: fixture → (current PIs, current Allocation Managers) where either exceeds one.
    #: Both are on services that never build a roster; see the class docstring.
    AMBIGUOUS = {
        'date_adjustment_ucub0155_manual.json': (2, 2),   # parks: no serviceable
        'extension_ucbk0034_ok.json': (1, 2),             # extend: ignores roles[]
    }

    #: Services that call ``resolve_roster``. The others never read ``roles[]``.
    ROSTER_SERVICES = ('add', 'update')

    @pytest.mark.parametrize('name', sorted(p.name for p in FIXTURE_DIR.glob('*.json')))
    def test_each_payload_names_at_most_one_current_pi_and_manager(self, name):
        data = load_fixture(name)
        counts = (len(role_candidates(data, PI_ROLE, today='2026-08-07')),
                  len(role_candidates(data, ALLOCATION_MANAGER_ROLE,
                                      today='2026-08-07')))
        if name in self.AMBIGUOUS:
            assert counts == self.AMBIGUOUS[name]
        else:
            assert counts[0] <= 1 and counts[1] <= 1, counts

    def test_no_roster_building_payload_is_ambiguous(self, session):
        """The claim that actually protects the cutover.

        Swept against the real selector rather than against the action type, because
        ``New`` routes to Add or Update depending on whether the project exists — and
        both build a roster, which is the point.
        """
        from sam.xras.dispatch import select_service
        offenders = []
        for path in sorted(FIXTURE_DIR.glob('*.json')):
            data = load_fixture(path.name)
            if select_service(session, data) not in self.ROSTER_SERVICES:
                continue
            if (len(role_candidates(data, PI_ROLE, today='2026-08-07')) > 1
                    or len(role_candidates(data, ALLOCATION_MANAGER_ROLE,
                                           today='2026-08-07')) > 1):
                offenders.append(path.name)
        assert offenders == []

    def test_uwis0071_has_two_pi_roles_but_only_one_is_current(self):
        """The payload legacy resolved by array order. Our date filter makes it
        unambiguous without needing the tie-break: the second PI's role ended
        2026-08-04, before the 2026-08-06 action date."""
        data = load_fixture('new_uwis0071_existing_ok.json')
        assert sum(1 for r in data['roles'] if r['roleType'] == PI_ROLE) == 2
        assert role_candidates(data, PI_ROLE, today='2026-08-07') == ('user_00000070',)


# ---------------------------------------------------------------------------
# StringUtil.normalize.
# ---------------------------------------------------------------------------


class TestUsernameNormalization:
    """The function that decides *which row* gets looked up. Skipping it would turn a
    resolvable user into ``Username %s is missing``."""

    def test_accents_are_stripped(self):
        assert normalize_username('José') == 'Jose'

    def test_surrounding_whitespace_is_trimmed(self):
        assert normalize_username('  alice  ') == 'alice'

    def test_plain_ascii_is_untouched(self):
        assert normalize_username('user_00000001') == 'user_00000001'

    def test_none_becomes_empty_not_none(self):
        """Jackson's field default is ``""``, and legacy's ``Username  is missing``
        depends on it."""
        assert normalize_username(None) == ''

    def test_non_latin_characters_are_dropped_entirely(self):
        """NFD leaves them undecomposed and outside the ASCII window, so they vanish —
        legacy's behavior, and the reason a non-Latin username cannot resolve."""
        assert normalize_username('用户alice') == 'alice'


# ---------------------------------------------------------------------------
# resolve_roster — the validated entry point.
# ---------------------------------------------------------------------------


class TestResolveRoster:

    def test_a_clean_action_resolves_lead_admin_and_members(self, session):
        from factories import make_user
        pi = make_user(session)
        am = make_user(session)
        errs = ActionErrors()
        result = resolve_roster(session, act(
            role(PI_ROLE, pi.username),
            role(ALLOCATION_MANAGER_ROLE, am.username),
        ), errs, today='2026-06-15')

        assert not errs
        assert result == Roster(pi_username=pi.username, admin_username=am.username,
                                member_usernames=(pi.username, am.username),
                                pi=pi, admin=am, members=(pi, am))

    def test_the_roster_carries_the_rows_it_already_fetched(self, session):
        """``resolve_roster`` validates by looking each user up, so it has the rows.

        Handing them over is what lets New and Update stop re-querying every username
        they were just given — two byte-identical blocks that doubled the query count
        for a roster. ``members`` stays positionally aligned with
        ``member_usernames``.
        """
        from factories import make_user
        pi = make_user(session)
        am = make_user(session)
        errs = ActionErrors()

        result = resolve_roster(session, act(
            role(PI_ROLE, pi.username),
            role(ALLOCATION_MANAGER_ROLE, am.username),
        ), errs, today='2026-06-15')

        assert result.pi is pi
        assert result.admin is am
        assert list(result.members) == [pi, am]
        assert len(result.members) == len(result.member_usernames)

    def test_a_member_with_no_user_row_keeps_its_place_as_none(self, session):
        """The hole is preserved rather than compacted.

        A missing member is already an error, so ``raise_if_any()`` stops the action
        before anything iterates this — but keeping the slot keeps ``members`` aligned
        with ``member_usernames`` and keeps the handlers' ``if member is not None``
        guard meaningful instead of silently unreachable-by-shortening.
        """
        from factories import make_user
        pi = make_user(session)
        errs = ActionErrors()

        result = resolve_roster(session, act(
            role(PI_ROLE, pi.username),
            role('Co-PI', 'nosuchuser_zz'),
        ), errs, today='2026-06-15')

        assert 'nosuchuser_zz' in result.member_usernames
        assert len(result.members) == len(result.member_usernames)
        missing_at = result.member_usernames.index('nosuchuser_zz')
        assert result.members[missing_at] is None

    def test_no_pi_role_reports_missing_pi_role(self, session):
        errs = ActionErrors()
        result = resolve_roster(session, act(), errs, today='2026-06-15')
        assert list(errs) == ['Missing pi role']
        assert result.pi_username is None

    def test_a_missing_allocation_manager_is_not_an_error(self, session):
        """Legacy guards the whole check on ``adminUsername != null``, and real
        payloads arrive without one."""
        from factories import make_user
        pi = make_user(session)
        errs = ActionErrors()
        result = resolve_roster(session, act(role(PI_ROLE, pi.username)),
                                errs, today='2026-06-15')
        assert not errs
        assert result.admin_username is None

    def test_two_current_pis_are_rejected_rather_than_coin_flipped(self, session):
        """Defect 1. Legacy takes the first in array order, so which human leads the
        project is decided by JSON ordering."""
        from factories import make_user
        alice, bob = make_user(session), make_user(session)
        errs = ActionErrors()
        result = resolve_roster(session, act(
            role(PI_ROLE, alice.username),
            role(PI_ROLE, bob.username),
        ), errs, today='2026-06-15')

        assert result.pi_username is None
        assert list(errs) == [
            f'Multiple PI roles are in range for this action: '
            f'{alice.username}, {bob.username}']

    def test_two_current_allocation_managers_are_rejected_too(self, session):
        from factories import make_user
        alice, bob = make_user(session), make_user(session)
        errs = ActionErrors()
        result = resolve_roster(session, act(
            role(PI_ROLE, make_user(session).username),
            role(ALLOCATION_MANAGER_ROLE, alice.username),
            role(ALLOCATION_MANAGER_ROLE, bob.username),
        ), errs, today='2026-06-15')

        assert result.admin_username is None
        assert any(m.startswith('Multiple Allocation Manager roles') for m in errs)

    def test_an_unknown_pi_reports_with_no_trailing_punctuation(self, session):
        errs = ActionErrors()
        resolve_roster(session, act(role(PI_ROLE, 'no_such_user_xyz')),
                       errs, today='2026-06-15')
        assert 'PI no_such_user_xyz is not in database' in list(errs)

    def test_an_inactive_pi_reports_with_the_trailing_colon_space(self, session):
        from factories import make_user
        pi = make_user(session, active=False)
        errs = ActionErrors()
        resolve_roster(session, act(role(PI_ROLE, pi.username)),
                       errs, today='2026-06-15')
        assert f'PI {pi.username} is not an active user: ' in list(errs)

    def test_an_unknown_manager_reports_with_the_trailing_colon_space(self, session):
        from factories import make_user
        errs = ActionErrors()
        resolve_roster(session, act(
            role(PI_ROLE, make_user(session).username),
            role(ALLOCATION_MANAGER_ROLE, 'no_such_manager_xyz'),
        ), errs, today='2026-06-15')
        assert 'Allocation Manager no_such_manager_xyz is not in database: ' in list(errs)

    def test_an_inactive_manager_reports_with_the_trailing_bare_space(self, session):
        from factories import make_user
        am = make_user(session, active=False)
        errs = ActionErrors()
        resolve_roster(session, act(
            role(PI_ROLE, make_user(session).username),
            role(ALLOCATION_MANAGER_ROLE, am.username),
        ), errs, today='2026-06-15')
        assert f'Allocation Manager {am.username} is not active ' in list(errs)

    def test_a_roster_member_who_is_not_a_user_reports_the_roster_string(self, session):
        """The roster path has its **own** wording — ``Username %s is missing`` — which
        is why an unknown Co-PI reads differently from an unknown PI."""
        from factories import make_user
        errs = ActionErrors()
        resolve_roster(session, act(
            role(PI_ROLE, make_user(session).username),
            role('Co-PI', 'no_such_copi_xyz'),
        ), errs, today='2026-06-15')
        assert 'Username no_such_copi_xyz is missing' in list(errs)

    def test_an_inactive_roster_member_reports_inactive(self, session):
        from factories import make_user
        member = make_user(session, active=False)
        errs = ActionErrors()
        resolve_roster(session, act(
            role(PI_ROLE, make_user(session).username),
            role('User', member.username),
        ), errs, today='2026-06-15')
        assert f'Username {member.username} is inactive' in list(errs)

    def test_the_pi_is_reported_once_not_twice(self, session):
        """The PI is validated by the role path *and* walked by the roster path, so an
        unknown PI reports under both wordings — but each exactly once, because the
        accumulator deduplicates."""
        errs = ActionErrors()
        resolve_roster(session, act(role(PI_ROLE, 'no_such_user_xyz')),
                       errs, today='2026-06-15')
        assert list(errs) == [
            'PI no_such_user_xyz is not in database',
            'Username no_such_user_xyz is missing',
        ]

    def test_errors_arrive_in_legacys_order(self, session):
        """PI, then Allocation Manager, then the roster — the order
        ``ProjectActionCommandFactoryBase`` validates in, and the order the operator
        reads them in."""
        errs = ActionErrors()
        resolve_roster(session, act(
            role(PI_ROLE, 'unknown_pi_xyz'),
            role(ALLOCATION_MANAGER_ROLE, 'unknown_am_xyz'),
            role('User', 'unknown_member_xyz'),
        ), errs, today='2026-06-15')
        messages = list(errs)
        assert messages[0] == 'PI unknown_pi_xyz is not in database'
        assert messages[1] == 'Allocation Manager unknown_am_xyz is not in database: '
        assert messages[2:] == [
            'Username unknown_pi_xyz is missing',
            'Username unknown_am_xyz is missing',
            'Username unknown_member_xyz is missing',
        ]

    def test_the_defect_three_disagreement_is_carried_as_a_warning_not_an_error(
            self, session):
        """The action survives it — legacy processes these — but it is the only
        evidence anyone has that the situation occurred."""
        from factories import make_user
        pi = make_user(session)
        errs = ActionErrors()
        result = resolve_roster(session, act(
            role(PI_ROLE, pi.username, begin='2026-07-01'),
            action_begin='2026-06-01',
        ), errs, today='2026-06-15')

        assert not errs
        assert result.pi_username == pi.username
        assert result.member_usernames == ()
        assert result.warnings == (pi.username,)

    def test_a_locked_user_is_rejected_where_legacy_would_allow_them(self, session):
        """Declared divergence. Java's ``User.isActive()`` returns ``active`` alone;
        SAM's ``is_active`` hybrid is ``active AND NOT locked`` and the house rule
        (CLAUDE.md § 5) is to use it. Unobservable in practice — production has **zero**
        locked users out of 28,371 — and a locked account is one somebody deliberately
        stopped."""
        from factories import make_user
        pi = make_user(session)
        pi.locked = True
        session.flush()
        errs = ActionErrors()
        resolve_roster(session, act(role(PI_ROLE, pi.username)),
                       errs, today='2026-06-15')
        assert f'PI {pi.username} is not an active user: ' in list(errs)


class TestTheCorpusResolvesWithoutStructuralErrors:
    """Every payload's roster shape, computed. Usernames were scrubbed independently of
    the obfuscated test database, so none of them resolve to rows — what is asserted
    here is the *arithmetic*, not the lookups."""

    EXPECTED_MEMBER_COUNTS = {
        'adjustment_ucsu0146_manual.json': 2,
        'adjustment_ucub0160_manual.json': 2,
        'adjustment_uwis0064_manual.json': 0,   # defect 3 — all roles post-date the action
        'date_adjustment_uazn0052_manual.json': 2,
        'date_adjustment_ucor0097_manual.json': 2,
        'date_adjustment_ucub0155_manual.json': 1,
        'date_adjustment_uwas0141_manual.json': 2,
        'extension_ucbk0034_ok.json': 1,
        'extension_ucsd0048_ok.json': 2,
        'extension_ucsd0073_ok.json': 2,
        'extension_ucub0166_ok.json': 2,
        'extension_ufsu0023_failed.json': 2,
        'extension_ugmu0052_ok.json': 2,
        'extension_uiuc0073_ok.json': 2,
        'extension_unid0003_ok.json': 1,
        'extension_uwho0019_ok.json': 2,
        'new_ncar4214_ok.json': 2,
        'new_ncar4218_ok.json': 1,
        'new_ncar4223_ok.json': 1,
        'new_ncar4227_failed.json': 1,
        'new_ncar4228_failed.json': 1,
        'new_ncar4229_ok.json': 1,
        'new_ncar4232_failed.json': 2,          # 3 roles, 2 distinct humans
        'new_ncar4236_failed.json': 1,
        'new_ncar4246_ok.json': 2,
        'new_ncar4250_ok.json': 2,
        'new_ncar4253_ok.json': 2,
        'new_uchi0020_ok.json': 1,
        'new_uida0008_ok.json': 2,
        'new_ummm0016_failed.json': 6,          # 7 roles — the largest roster in the corpus
        'new_umsb0003_ok.json': 1,
        'new_uwis0071_existing_ok.json': 1,     # 3 roles; one expired, two are one human
        'supplement_uahv0010_ok.json': 2,
        'supplement_ubrn0027_ok.json': 1,       # PI and manager are the same person
        'supplement_ucit0011_ok.json': 1,
        'supplement_ucla0076_ok.json': 2,
        'supplement_ucla0080_ok.json': 2,
        'supplement_ucsu0114_ok.json': 2,
        'supplement_ucub0182_ok.json': 2,
        'supplement_ugit0044_ok.json': 1,
        'supplement_uwku0002_ok.json': 2,
    }

    def test_the_corpus_is_complete(self):
        on_disk = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))
        assert on_disk == sorted(self.EXPECTED_MEMBER_COUNTS)

    @pytest.mark.parametrize('name', sorted(EXPECTED_MEMBER_COUNTS))
    def test_member_counts(self, name):
        assert len(roster_usernames(load_fixture(name))) == \
            self.EXPECTED_MEMBER_COUNTS[name]

    @pytest.mark.parametrize('name', sorted(EXPECTED_MEMBER_COUNTS))
    def test_every_payload_names_a_pi(self, name):
        """Not one of the 41 would hit ``Missing pi role``, which is worth knowing
        before the cutover: that error is reserved for a genuinely malformed request.

        Held at eight payloads and still holds at 41, across every action type
        including the four that park.
        """
        assert role_candidates(load_fixture(name), PI_ROLE, today='2026-08-07')

    def test_both_extensions_carry_a_roster_that_nothing_consumes(self):
        """``AddUserToProjectActionCommandsFactory`` fans the roster out per
        ``resources[]`` entry, and both Extensions send ``resources: []`` — so legacy
        computes a two-person roster and produces zero add-user commands from it. The
        roster is still validated, so an unknown member still 422s the action."""
        for name in ('extension_ucub0166_ok.json', 'extension_ufsu0023_failed.json'):
            data = load_fixture(name)
            assert data['resources'] == []
            assert len(roster_usernames(data)) == 2
