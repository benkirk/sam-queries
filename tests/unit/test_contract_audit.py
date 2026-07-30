"""Unit tests for the contract data-hygiene checks.

Query-level only — no CliRunner, no network. `tests/unit/test_cli_contracts.py`
covers the CLI wiring.

Unlike `test_tree_audit.py`, `audit_contracts()` has no scope argument to
isolate a test's rows behind, so it always sees the whole snapshot alongside
whatever the test built. Every assertion here therefore filters the findings
down to the contract the test created (`_checks_for`) rather than counting
globally — which also keeps these tests independent of snapshot refreshes.
"""

from datetime import datetime, timedelta

import pytest

from sam.queries.contract_audit import (
    CHECKS,
    FUNDING_ACCOUNT_PROGRAM,
    MISSING_MONITOR,
    MISSING_PROGRAM,
    MONITOR_IS_PI,
    UNPARSEABLE_AWARD_ID,
    URL_MISSING,
    audit_contracts,
    audit_nsf_programs,
    is_funding_account_program,
)
from tests.factories.core import make_user
from tests.factories.projects import (
    make_contract, make_contract_source, make_nsf_program,
)

pytestmark = pytest.mark.unit


def _checks_for(session, contract, active_only=True):
    """The set of check keys that fired for exactly this contract."""
    findings = audit_contracts(session, active_only=active_only)
    return {f['check'] for f in findings
            if f['contract'].contract_id == contract.contract_id}


def _detail_for(session, contract, check, active_only=True):
    """The `detail` dict of one contract's finding for one check."""
    findings = audit_contracts(session, active_only=active_only)
    for f in findings:
        if f['contract'].contract_id == contract.contract_id and f['check'] == check:
            return f['detail']
    return None


@pytest.fixture
def clean_contract(session):
    """An NSF contract that should trip no check at all.

    Everything the audit looks at is set: a real research-program name, a
    monitor who is not the PI, an award-parseable number, and a URL.
    """
    return make_contract(
        session,
        contract_number="AGS-1852977",
        source=make_contract_source(session, name="NSF"),
        monitor=make_user(session),
        nsf_program=make_nsf_program(session, name="Physical & Dynamic Meteorology"),
        url="https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977",
    )


class TestCleanContract:

    def test_fully_populated_contract_trips_nothing(self, session, clean_contract):
        assert _checks_for(session, clean_contract) == set()

    def test_expired_contract_is_invisible_by_default(self, session):
        """active_only=True is the default scope — 368 of 2,225 rows today."""
        expired = make_contract(
            session,
            start_date=datetime.now() - timedelta(days=800),
            end_date=datetime.now() - timedelta(days=400),
        )
        assert _checks_for(session, expired, active_only=True) == set()
        assert URL_MISSING in _checks_for(session, expired, active_only=False)


class TestFundingAccountProgram:
    """The headline check: NSF's `primaryProgram` pasted in as a program."""

    def test_funding_account_name_is_flagged(self, session, clean_contract):
        clean_contract.nsf_program = make_nsf_program(
            session, name="01002526DB NSF RESEARCH & RELATED ACTIVIT")
        session.flush()
        assert FUNDING_ACCOUNT_PROGRAM in _checks_for(session, clean_contract)

    def test_detail_carries_the_offending_name(self, session, clean_contract):
        clean_contract.nsf_program = make_nsf_program(
            session, name="01002627DB NSF RESEARCH & RELATED ACTIVIT")
        session.flush()
        detail = _detail_for(session, clean_contract, FUNDING_ACCOUNT_PROGRAM)
        assert detail['nsf_program'] == "01002627DB NSF RESEARCH & RELATED ACTIVIT"

    def test_flagged_regardless_of_source(self, session):
        """The rule is about the program row, not about who funded it."""
        contract = make_contract(
            session,
            source=make_contract_source(session, name="DOE"),
            monitor=make_user(session),
            url="https://example.invalid/award",
            nsf_program=make_nsf_program(
                session, name="01002324RB NSF RESEARCH & RELATED ACTIVIT"),
        )
        assert FUNDING_ACCOUNT_PROGRAM in _checks_for(session, contract)

    @pytest.mark.parametrize("name,expected", [
        ("01002526DB NSF RESEARCH & RELATED ACTIVIT", True),
        ("01002324RB", True),
        ("Physical & Dynamic Meteorology", False),
        ("ATMOSPHERIC AND GEOSPACE SCIENCES", False),
        ("0100252 DB TOO SHORT", False),      # 7 digits, not 8
        ("01002526db lowercase", False),      # letters must be upper
        (None, False),
        ("", False),
    ])
    def test_rule(self, name, expected):
        assert is_funding_account_program(name) is expected


class TestMonitorIsPi:

    def test_same_person_is_flagged(self, session, clean_contract):
        clean_contract.contract_monitor_user_id = \
            clean_contract.principal_investigator_user_id
        session.flush()
        assert MONITOR_IS_PI in _checks_for(session, clean_contract)

    def test_detail_carries_the_username(self, session, clean_contract):
        clean_contract.contract_monitor_user_id = \
            clean_contract.principal_investigator_user_id
        session.flush()
        detail = _detail_for(session, clean_contract, MONITOR_IS_PI)
        assert detail['username'] == clean_contract.principal_investigator.username

    def test_null_monitor_is_not_a_self_match(self, session, clean_contract):
        """Both NULL must not read as "the same person"."""
        clean_contract.contract_monitor_user_id = None
        session.flush()
        assert MONITOR_IS_PI not in _checks_for(session, clean_contract)


class TestNsfScoping:
    """Three checks are NSF-only; unscoped they would be mostly noise.

    18 of the 20 contracts with no program are non-NSF sources, where
    `Contract.create`'s docstring says a program is not expected — and NSF is
    the only source that carries a program officer at all.
    """

    def test_nsf_contract_missing_monitor_is_flagged(self, session, clean_contract):
        clean_contract.contract_monitor_user_id = None
        session.flush()
        assert MISSING_MONITOR in _checks_for(session, clean_contract)

    def test_nsf_contract_missing_program_is_flagged(self, session, clean_contract):
        clean_contract.nsf_program_id = None
        session.flush()
        assert MISSING_PROGRAM in _checks_for(session, clean_contract)

    @pytest.mark.parametrize("source_name", ["DOE", "NASA", "AFOSR"])
    def test_non_nsf_contract_needs_neither(self, session, source_name):
        contract = make_contract(
            session,
            source=make_contract_source(session, name=source_name),
            url="https://example.invalid/award",
        )
        fired = _checks_for(session, contract)
        assert MISSING_MONITOR not in fired
        assert MISSING_PROGRAM not in fired

    def test_placeholder_program_counts_as_missing(self, session, clean_contract):
        """`nsf_program_id=107` is literally named NONE — a NULL in disguise."""
        clean_contract.nsf_program = make_nsf_program(session, name="NONE")
        session.flush()
        assert MISSING_PROGRAM in _checks_for(session, clean_contract)


class TestUnparseableAwardId:

    @pytest.mark.parametrize("number", [
        "OCE-UCSC0001",
        "NCAR0880 SOMETHING",
    ])
    def test_nsf_number_that_does_not_parse(self, session, number):
        contract = make_contract(
            session, contract_number=number,
            source=make_contract_source(session, name="NSF"),
            monitor=make_user(session),
            nsf_program=make_nsf_program(session, name="Physical & Dynamic Meteorology"),
            url="https://example.invalid/award",
        )
        assert UNPARSEABLE_AWARD_ID in _checks_for(session, contract)

    @pytest.mark.parametrize("number", [
        "2317820",          # bare
        "AGS-1852977",      # division-prefixed
        "OCE- 1419584",     # stray space after the hyphen
    ])
    def test_nsf_numbers_that_do_parse(self, session, number):
        contract = make_contract(
            session, contract_number=number,
            source=make_contract_source(session, name="NSF"),
        )
        assert UNPARSEABLE_AWARD_ID not in _checks_for(session, contract)

    def test_non_nsf_free_text_is_not_flagged(self, session):
        """`PRJ014003 BAHAMAS S-TIMBA` is a real value and is not an NSF award."""
        contract = make_contract(
            session, contract_number="PRJ014003 BAHAMAS S-TIMBA",
            source=make_contract_source(session, name="Other"),
            url="https://example.invalid/award",
        )
        assert UNPARSEABLE_AWARD_ID not in _checks_for(session, contract)


class TestUrlMissing:

    @pytest.mark.parametrize("url", [None, "", "   "])
    def test_blank_urls_are_flagged(self, session, clean_contract, url):
        clean_contract.url = url
        session.flush()
        assert URL_MISSING in _checks_for(session, clean_contract)


class TestFindingShape:

    def test_findings_are_grouped_in_check_order(self, session, clean_contract):
        """The CLI renders sections straight off this ordering."""
        findings = audit_contracts(session)
        order = {key: i for i, (key, _label, _sev) in enumerate(CHECKS)}
        positions = [order[f['check']] for f in findings]
        assert positions == sorted(positions)

    def test_finding_carries_the_orm_object(self, session, clean_contract):
        clean_contract.url = None
        session.flush()
        findings = [f for f in audit_contracts(session)
                    if f['contract'].contract_id == clean_contract.contract_id]
        assert findings
        assert findings[0]['contract'] is not None
        assert findings[0]['contract'].contract_number == clean_contract.contract_number

    def test_every_check_key_is_declared_in_CHECKS(self, session):
        declared = {key for key, _label, _sev in CHECKS}
        emitted = {f['check'] for f in audit_contracts(session, active_only=False)}
        assert emitted <= declared


class TestNsfProgramAudit:

    def test_funding_account_row_is_reported(self, session):
        program = make_nsf_program(
            session, name="01009900DB NSF RESEARCH & RELATED ACTIVIT")
        make_contract(session, nsf_program=program)
        session.flush()

        rows = {f['nsf_program_id']: f for f in audit_nsf_programs(session)}
        assert program.nsf_program_id in rows
        assert rows[program.nsf_program_id]['contract_count'] >= 1
        assert rows[program.nsf_program_id]['open_contract_count'] >= 1

    def test_research_program_is_not_reported(self, session):
        program = make_nsf_program(session, name="Physical & Dynamic Meteorology")
        make_contract(session, nsf_program=program)
        session.flush()

        ids = {f['nsf_program_id'] for f in audit_nsf_programs(session)}
        assert program.nsf_program_id not in ids

    def test_orphan_programs_are_not_reported(self, session):
        """53 rows reference no contract; no contract is wrong because of them."""
        orphan = make_nsf_program(session, name="Orphaned Research Program")
        session.flush()
        ids = {f['nsf_program_id'] for f in audit_nsf_programs(session)}
        assert orphan.nsf_program_id not in ids

    def test_sorted_by_open_contract_count(self, session):
        counts = [f['open_contract_count'] for f in audit_nsf_programs(session)]
        assert counts == sorted(counts, reverse=True)
