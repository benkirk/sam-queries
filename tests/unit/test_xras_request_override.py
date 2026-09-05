"""Per-request operator overrides: the model, and the two ingest consults.

Covers ``XrasRequestOverride`` (set/lookup/clear + validation) and the escape
hatches it drives — a mnemonic override short-circuiting ``resolve_mnemonic_code``
and an ``ignore_contract`` override waiving the "Cannot find contract" 422 in
``plan_contracts``. Keyed on ``request_number`` (the stable token), not the
volatile XRAS request_id.
"""
import pytest

from sam.integration.xras import XrasRequestOverride, lookup_request_override
from sam.xras.errors import ActionErrors
from sam.xras.extractors import resolve_mnemonic_code
from sam.xras.handlers._fields import plan_contracts

from factories import (make_contract, make_mnemonic_code, make_organization,
                       make_user, make_user_organization)


def action(**overrides):
    """A minimal wire action carrying the fields the consults read."""
    base = {'opportunityName': None, 'requestNumber': None, 'grants': []}
    base.update(overrides)
    return base


class TestTheModel:

    def test_set_lookup_and_clear_round_trip(self, session):
        mc = make_mnemonic_code(session)
        XrasRequestOverride.set(session, request_number='NCAR8001', kind='mnemonic',
                                created_by='op', mnemonic_code_id=mc.mnemonic_code_id,
                                request_id=8001, comment='why')
        row = lookup_request_override(session, 'NCAR8001', 'mnemonic')
        assert row is not None and row.mnemonic_code.mnemonic_code_id == mc.mnemonic_code_id
        row.clear()
        assert lookup_request_override(session, 'NCAR8001', 'mnemonic') is None

    def test_re_set_upserts_the_one_row(self, session):
        mc = make_mnemonic_code(session)
        XrasRequestOverride.set(session, request_number='NCAR8002', kind='mnemonic',
                                created_by='a', mnemonic_code_id=mc.mnemonic_code_id)
        XrasRequestOverride.set(session, request_number='NCAR8002', kind='mnemonic',
                                created_by='b', mnemonic_code_id=mc.mnemonic_code_id)
        n = session.query(XrasRequestOverride).filter_by(
            request_number='NCAR8002', kind='mnemonic').count()
        assert n == 1

    def test_a_re_issue_with_a_new_request_id_still_matches(self, session):
        """The whole reason the key is request_number: the informational
        request_id can change under a stable request_number and the override
        must still resolve."""
        mc = make_mnemonic_code(session)
        XrasRequestOverride.set(session, request_number='NCAR8009', kind='mnemonic',
                                created_by='op', mnemonic_code_id=mc.mnemonic_code_id,
                                request_id=111)
        XrasRequestOverride.set(session, request_number='NCAR8009', kind='mnemonic',
                                created_by='op', mnemonic_code_id=mc.mnemonic_code_id,
                                request_id=222)
        row = lookup_request_override(session, 'NCAR8009', 'mnemonic')
        assert row is not None and row.request_id == 222
        assert session.query(XrasRequestOverride).filter_by(
            request_number='NCAR8009', kind='mnemonic').count() == 1

    def test_ignore_contract_carries_no_code(self, session):
        XrasRequestOverride.set(session, request_number='NCAR8003',
                                kind='ignore_contract', created_by='op')
        row = lookup_request_override(session, 'NCAR8003', 'ignore_contract')
        assert row is not None and row.mnemonic_code_id is None

    def test_lookup_is_keyed_on_request_number(self, session):
        mc = make_mnemonic_code(session)
        XrasRequestOverride.set(session, request_number='NCAR8004', kind='mnemonic',
                                created_by='op', mnemonic_code_id=mc.mnemonic_code_id)
        assert lookup_request_override(session, 'NCAR9999', 'mnemonic') is None

    @pytest.mark.parametrize("kwargs", [
        dict(request_number='NCAR1', kind='bogus', created_by='x'),
        dict(request_number='NCAR1', kind='mnemonic', created_by='x'),     # no code
    ])
    def test_bad_input_rejected(self, session, kwargs):
        with pytest.raises(ValueError):
            XrasRequestOverride.set(session, **kwargs)

    def test_ignore_contract_with_a_stray_code_rejected(self, session):
        mc = make_mnemonic_code(session)
        with pytest.raises(ValueError):
            XrasRequestOverride.set(session, request_number='NCAR1',
                                    kind='ignore_contract', created_by='x',
                                    mnemonic_code_id=mc.mnemonic_code_id)


class TestMnemonicConsult:

    def test_override_short_circuits_an_unresolvable_pi(self, session):
        """The akeesee case: a PI with no current affiliation normally reports
        ``no_current_affiliation_for_pi``. The override returns the picked code."""
        picked = make_mnemonic_code(session, description='Override Picked Section')
        user = make_user(session)                       # no org, no institution
        XrasRequestOverride.set(session, request_number='NCAR7100', kind='mnemonic',
                                created_by='op', mnemonic_code_id=picked.mnemonic_code_id)
        errs = ActionErrors()
        row = resolve_mnemonic_code(
            session, action(opportunityName='Small Allocation', requestNumber='NCAR7100'),
            errs, pi_username=user.username)
        assert not errs
        assert row.mnemonic_code_id == picked.mnemonic_code_id

    def test_override_wins_over_a_normally_resolvable_org(self, session):
        """It is a hard escape hatch — it bypasses org/institution resolution."""
        org = make_organization(session, name='Would Resolve Section')
        make_mnemonic_code(session, description='Would Resolve Section', code='QW1')
        picked = make_mnemonic_code(session, description='Chosen Instead Section')
        user = make_user(session)
        make_user_organization(session, user=user, organization=org)
        XrasRequestOverride.set(session, request_number='NCAR7101', kind='mnemonic',
                                created_by='op', mnemonic_code_id=picked.mnemonic_code_id)
        errs = ActionErrors()
        row = resolve_mnemonic_code(
            session, action(opportunityName='Small Allocation', requestNumber='NCAR7101'),
            errs, pi_username=user.username)
        assert row.mnemonic_code_id == picked.mnemonic_code_id

    def test_a_retired_override_code_falls_through(self, session):
        """A code retired after the override was set is not used — normal
        resolution (here, a miss) resumes."""
        picked = make_mnemonic_code(session, description='Retired Later Section')
        user = make_user(session)                       # no affiliation
        XrasRequestOverride.set(session, request_number='NCAR7102', kind='mnemonic',
                                created_by='op', mnemonic_code_id=picked.mnemonic_code_id)
        picked.active = False
        session.flush()
        errs = ActionErrors()
        assert resolve_mnemonic_code(
            session, action(opportunityName='Small Allocation', requestNumber='NCAR7102'),
            errs, pi_username=user.username) is None
        assert list(errs)                               # the miss was reported

    def test_no_override_leaves_resolution_unchanged(self, session):
        org = make_organization(session, name='Untouched Section')
        mc = make_mnemonic_code(session, description='Untouched Section')
        user = make_user(session)
        make_user_organization(session, user=user, organization=org)
        errs = ActionErrors()
        row = resolve_mnemonic_code(
            session, action(opportunityName='Small Allocation', requestNumber='NCAR7103'),
            errs, pi_username=user.username)
        assert row.mnemonic_code_id == mc.mnemonic_code_id


class TestContractConsult:

    def test_a_missing_contract_reports_without_an_override(self, session):
        """Baseline: the blocker fires."""
        errs = ActionErrors()
        contracts, warnings, unresolved = plan_contracts(
            session, action(requestNumber='NCAR7200',
                            grants=[{'grantNumber': 'NSF-7770001'}]), errs)
        assert contracts == []
        assert list(errs)                               # 422 reported
        assert unresolved                               # feeds the create form

    def test_ignore_override_waives_the_blocker(self, session):
        XrasRequestOverride.set(session, request_number='NCAR7201',
                                kind='ignore_contract', created_by='op')
        errs = ActionErrors()
        contracts, warnings, unresolved = plan_contracts(
            session, action(requestNumber='NCAR7201',
                            grants=[{'grantNumber': 'NSF-7770002'}]), errs)
        assert contracts == []
        assert not list(errs)                           # NOT reported
        assert unresolved == []                         # no create-form prompt
        assert any('ignored' in w for w in warnings)    # a warning instead

    def test_ignore_override_still_links_a_resolvable_contract(self, session):
        """Waiving misses does not drop a grant that DOES resolve."""
        c = make_contract(session, contract_number='NSF-7770003')
        XrasRequestOverride.set(session, request_number='NCAR7202',
                                kind='ignore_contract', created_by='op')
        errs = ActionErrors()
        contracts, warnings, unresolved = plan_contracts(
            session, action(requestNumber='NCAR7202',
                            grants=[{'grantNumber': 'NSF-7770003'}]), errs)
        assert [x.contract_id for x in contracts] == [c.contract_id]
        assert not list(errs)

    def test_override_is_keyed_on_request_number(self, session):
        XrasRequestOverride.set(session, request_number='NCAR7203',
                                kind='ignore_contract', created_by='op')
        errs = ActionErrors()
        contracts, warnings, unresolved = plan_contracts(
            session, action(requestNumber='NCAR7299',   # a different request
                            grants=[{'grantNumber': 'NSF-7770004'}]), errs)
        assert list(errs)                               # override did NOT apply
