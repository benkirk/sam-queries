"""Unit tests for `Contract.get_by_number` / `Contract.search_by_pattern`.

Every assertion is scoped to rows this test created — `search_by_pattern`
takes no test-isolation argument and the snapshot DB holds 2,225 contracts,
so a global count would be a snapshot-refresh landmine. The pattern is a
per-test unique token woven into the contract number and title.

The wildcard cases are the load-bearing ones: `search_by_pattern` deliberately
diverges from both `_apply_filter` (which falls back to exact equality) and
`sam-search user --search` (whose advertised wildcard support is a lie). If
that behaviour regresses, the CLI's `--help` becomes wrong too.
"""

from datetime import datetime, timedelta

import pytest

from sam.projects.contracts import Contract
from tests.factories.core import make_user
from tests.factories.projects import (
    make_contract, make_contract_source, make_nsf_program,
)
from tests.factories._seq import next_seq

pytestmark = pytest.mark.unit


@pytest.fixture
def token():
    """A unique, wildcard-free token that cannot collide with snapshot rows."""
    return f"zqx{next_seq('CSEARCH')}"


@pytest.fixture
def contracts(session, token):
    """Three contracts sharing *token*: one open, one expired, one future."""
    past = datetime.now() - timedelta(days=365)
    return {
        'open': make_contract(
            session, contract_number=f'AGS-{token}-open',
            title=f'Open study of {token} dynamics',
            start_date=past),
        'expired': make_contract(
            session, contract_number=f'OCE-{token}-old',
            title=f'Expired study of {token} dynamics',
            start_date=past, end_date=datetime.now() - timedelta(days=30)),
        'future': make_contract(
            session, contract_number=f'DMS-{token}-new',
            title=f'Future study of {token} dynamics',
            start_date=datetime.now() + timedelta(days=30)),
    }


def _numbers(rows):
    return {c.contract_number for c in rows}


class TestGetByNumber:

    def test_exact_number_returns_the_contract(self, session, contracts):
        target = contracts['open']
        found = Contract.get_by_number(session, target.contract_number)
        assert found is not None
        assert found.contract_id == target.contract_id

    def test_surrounding_whitespace_is_stripped(self, session, contracts):
        number = contracts['open'].contract_number
        assert Contract.get_by_number(session, f'  {number}  ') is not None

    def test_unknown_number_is_none(self, session):
        assert Contract.get_by_number(session, 'NO-SUCH-CONTRACT-9999') is None

    def test_blank_is_none_without_querying(self, session):
        assert Contract.get_by_number(session, '') is None
        assert Contract.get_by_number(session, '   ') is None
        assert Contract.get_by_number(session, None) is None

    def test_it_is_exact_not_a_substring_match(self, session, contracts):
        """The number column is free text; a partial must not resolve."""
        partial = contracts['open'].contract_number[:6]
        assert Contract.get_by_number(session, partial) is None

    def test_expired_contracts_are_still_findable(self, session, contracts):
        """A getter has no business applying an activity filter."""
        found = Contract.get_by_number(
            session, contracts['expired'].contract_number)
        assert found is not None


class TestActiveOnly:

    def test_defaults_to_open_contracts(self, session, token, contracts):
        found = Contract.search_by_pattern(session, token, limit=100)
        assert _numbers(found) == {contracts['open'].contract_number}

    def test_active_only_false_includes_expired_and_future(
            self, session, token, contracts):
        found = Contract.search_by_pattern(
            session, token, active_only=False, limit=100)
        assert _numbers(found) == {c.contract_number
                                   for c in contracts.values()}


class TestWildcardSemantics:
    """LIKE iff the term carries % or _, else substring. Both branches."""

    def test_bare_term_is_a_substring_match(self, session, token, contracts):
        found = Contract.search_by_pattern(session, token, limit=100)
        assert found, 'a bare term must match mid-string'

    def test_bare_term_and_explicit_wildcards_agree(
            self, session, token, contracts):
        bare = Contract.search_by_pattern(session, token, limit=100)
        wrapped = Contract.search_by_pattern(session, f'%{token}%', limit=100)
        assert _numbers(bare) == _numbers(wrapped)

    def test_percent_anchors_instead_of_wrapping(
            self, session, token, contracts):
        """`AGS-<token>%` must anchor — if % were still wrapped, the OCE and
        DMS rows would come back too."""
        found = Contract.search_by_pattern(
            session, f'AGS-{token}%', active_only=False, limit=100)
        assert _numbers(found) == {contracts['open'].contract_number}

    def test_underscore_is_honoured_as_a_single_char_wildcard(
            self, session, token, contracts):
        # 'AGS-<token>-ope_' matches only '-open'.
        found = Contract.search_by_pattern(
            session, f'AGS-{token}-ope_', active_only=False, limit=100)
        assert _numbers(found) == {contracts['open'].contract_number}

    def test_a_wildcard_term_is_not_also_substring_matched(
            self, session, token, contracts):
        """The divergence that matters: an anchored pattern must not silently
        fall back to `%term%` the way `user --search` effectively does."""
        found = Contract.search_by_pattern(
            session, f'{token}-old', active_only=False, limit=100)
        assert _numbers(found) == {contracts['expired'].contract_number}

        anchored = Contract.search_by_pattern(
            session, f'{token}-old%', active_only=False, limit=100)
        assert _numbers(anchored) == set(), 'nothing starts with the token'

    def test_matching_is_case_insensitive(self, session, token, contracts):
        """The columns are utf8mb3_bin (case-sensitive), so this only works
        because the query uses ilike — a plain LIKE would return nothing."""
        found = Contract.search_by_pattern(
            session, token.upper(), active_only=False, limit=100)
        assert _numbers(found) == {c.contract_number
                                   for c in contracts.values()}

    def test_number_or_title_both_match(self, session, token):
        """A term present only in the title still hits."""
        title_token = f'zqt{next_seq("CSEARCH")}'
        made = make_contract(session, contract_number=f'AGS-{token}-t',
                             title=f'A study of {title_token}')
        found = Contract.search_by_pattern(session, title_token, limit=100)
        assert _numbers(found) == {made.contract_number}


class TestFilters:

    def test_source_filters_by_name(self, session, token):
        nsf = make_contract(session, contract_number=f'AGS-{token}-n',
                            title=f'{token} nsf', source=make_contract_source(
                                session, name='NSF'))
        make_contract(session, contract_number=f'DE-{token}-d',
                      title=f'{token} doe',
                      source=make_contract_source(session, name='DOE'))

        found = Contract.search_by_pattern(session, token, source='NSF',
                                           limit=100)
        assert _numbers(found) == {nsf.contract_number}

    def test_pi_filters_by_username(self, session, token):
        pi = make_user(session)
        mine = make_contract(session, contract_number=f'AGS-{token}-p',
                             title=f'{token} mine', pi=pi)
        make_contract(session, contract_number=f'AGS-{token}-o',
                      title=f'{token} other')

        found = Contract.search_by_pattern(session, token, pi=pi.username,
                                           limit=100)
        assert _numbers(found) == {mine.contract_number}

    def test_monitor_filters_independently_of_pi(self, session, token):
        """Two user FKs on one table — each filter needs its own alias, or
        they collide and the query returns nothing."""
        monitor = make_user(session)
        watched = make_contract(session, contract_number=f'AGS-{token}-m',
                                title=f'{token} watched', monitor=monitor)
        make_contract(session, contract_number=f'AGS-{token}-u',
                      title=f'{token} unwatched')

        found = Contract.search_by_pattern(session, token,
                                           monitor=monitor.username, limit=100)
        assert _numbers(found) == {watched.contract_number}

    def test_pi_and_monitor_combine(self, session, token):
        pi, monitor = make_user(session), make_user(session)
        both = make_contract(session, contract_number=f'AGS-{token}-b',
                             title=f'{token} both', pi=pi, monitor=monitor)
        make_contract(session, contract_number=f'AGS-{token}-x',
                      title=f'{token} pi only', pi=pi)

        found = Contract.search_by_pattern(
            session, token, pi=pi.username, monitor=monitor.username,
            limit=100)
        assert _numbers(found) == {both.contract_number}

    def test_program_is_pattern_matched(self, session, token):
        program = make_nsf_program(session,
                                   name=f'Physical Meteorology {token}')
        made = make_contract(session, contract_number=f'AGS-{token}-g',
                             title=f'{token} programmed', nsf_program=program)
        make_contract(session, contract_number=f'AGS-{token}-h',
                      title=f'{token} unprogrammed')

        found = Contract.search_by_pattern(session, token,
                                           program='Physical Meteorology',
                                           limit=100)
        assert _numbers(found) == {made.contract_number}

    def test_filters_work_without_a_pattern(self, session, token):
        """`pattern=None` must not become a filter on the empty string."""
        pi = make_user(session)
        made = make_contract(session, contract_number=f'AGS-{token}-np',
                             title=f'{token} no pattern', pi=pi)
        found = Contract.search_by_pattern(session, None, pi=pi.username,
                                           limit=100)
        assert _numbers(found) == {made.contract_number}

    def test_blank_pattern_is_treated_as_absent(self, session, token):
        pi = make_user(session)
        made = make_contract(session, contract_number=f'AGS-{token}-bp',
                             title=f'{token} blank', pi=pi)
        found = Contract.search_by_pattern(session, '   ', pi=pi.username,
                                           limit=100)
        assert _numbers(found) == {made.contract_number}


class TestShape:

    def test_limit_caps_the_result(self, session, token):
        for i in range(4):
            make_contract(session, contract_number=f'AGS-{token}-{i}',
                          title=f'{token} number {i}')
        assert len(Contract.search_by_pattern(session, token, limit=2)) == 2

    def test_ordered_by_contract_number(self, session, token):
        for suffix in ('c', 'a', 'b'):
            make_contract(session, contract_number=f'AGS-{token}-{suffix}',
                          title=f'{token} {suffix}')
        found = Contract.search_by_pattern(session, token, limit=100)
        numbers = [c.contract_number for c in found]
        assert numbers == sorted(numbers)

    def test_with_details_returns_usable_relationships(self, session, token):
        make_contract(session, contract_number=f'AGS-{token}-d',
                      title=f'{token} detailed')
        found = Contract.search_by_pattern(session, token, limit=100,
                                           with_details=True)
        assert found[0].contract_source is not None
        assert found[0].principal_investigator is not None

    def test_no_match_returns_empty_not_none(self, session):
        assert Contract.search_by_pattern(
            session, 'zzz-no-such-contract-zzz', limit=10) == []
