"""XRAS_AUTO_CONTRACTS: a post creates its missing contract from the cached NSF award.

A post blocked on anything else writes no contract (the sweep may make it early instead),
and its PI resolves against the roster first. A post that applies links a contract contact
its roster names (Ben 2026-11-07). The award comes from the cache only; nothing is network.
"""

from datetime import date

import pytest

import sam.integration.awards as awards
from sam.integration.awards.base import AwardRecord, PersonRef
from sam.projects.contracts import Contract, ExternalContact
from sam.projects.projects import Project
from sam.xras.errors import ActionErrors, XrasActionRejected
from sam.xras.handlers._fields import plan_contracts
from sam.xras.handlers.new import handle_new
from factories import make_contract_source
from factories._seq import next_seq
from xras_helpers import committing, wire_resource  # noqa: F401

from test_xras_new_handler import _stub_gid_pool, action_for, creatable  # noqa: F401


def _award_number():
    return f"AGS-86{next_seq('AUTO')[4:]}"


def _record(number, pi_name='Pat Doe', pi_email=None, **overrides):
    defaults = dict(provenance='NSF Awards API', contract_number=number,
                    title='Clouds and more clouds', start_date=date(2026, 9, 1),
                    end_date=date(2029, 8, 31), url='https://nsf.example/a',
                    program_name=None,
                    pi=PersonRef(name=pi_name, email=pi_email or f"{next_seq('pi')}@uni.example"),
                    monitor=PersonRef(name='Olive Officer', email=f"{next_seq('po')}@nsf.example"),
                    unavailable_fields=frozenset())
    defaults.update(overrides)
    return AwardRecord(**defaults)


@pytest.fixture
def armed(monkeypatch, session):
    """The lever on, NSF present, and a cache the test fills."""
    monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '1')
    make_contract_source(session, name='NSF')
    cache = {}
    monkeypatch.setattr(awards, 'cached_award',
                        lambda source, number, exact=True: cache.get(number))
    return cache


def _grant(number, pi_name='Pat Doe', agency='NSF'):
    return {'grantNumber': number, 'fundingAgency': agency, 'piName': pi_name,
            'title': 'Clouds', 'beginDate': '2026-09-01', 'endDate': '2029-08-31'}


def _action(number, **grant):
    return {'requestNumber': f"NCAR{next_seq('rq')[2:]}", 'grants': [_grant(number, **grant)]}


class TestPlanContracts:

    def test_a_cached_award_is_planned_not_reported(self, session, armed):
        number = _award_number()
        armed[number] = _record(number)
        errs = ActionErrors()
        contracts, warnings, unresolved, planned = plan_contracts(session, _action(number), errs)
        assert not list(errs) and unresolved == [] and contracts == []
        assert [p.contract_number for p in planned] == [number]
        assert any('is created from the NSF award' in w for w in warnings)

    def test_lever_off_keeps_the_422(self, session, armed, monkeypatch):
        monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '0')
        number = _award_number()
        armed[number] = _record(number)
        errs = ActionErrors()
        *_, planned = plan_contracts(session, _action(number), errs)
        assert list(errs) and planned == []

    def test_a_cache_miss_keeps_the_422(self, session, armed):
        errs = ActionErrors()
        _, _, unresolved, planned = plan_contracts(session, _action(_award_number()), errs)
        assert list(errs) and unresolved and planned == []

    def test_a_non_nsf_grant_is_not_planned(self, session, armed):
        number = _award_number()
        armed[number] = _record(number)
        errs = ActionErrors()
        *_, planned = plan_contracts(session, _action(number, agency='DOE'), errs)
        assert list(errs) and planned == []

    def test_a_different_wire_pi_keeps_the_422(self, session, armed):
        number = _award_number()
        armed[number] = _record(number, pi_name='Sam Roe')
        errs = ActionErrors()
        *_, planned = plan_contracts(session, _action(number), errs)
        assert list(errs) and planned == []


class TestThePost:

    def _payload(self, creatable, mapped_resource, number, pi_name):
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['grants'] = [_grant(number, pi_name=pi_name)]
        return payload

    def test_a_clean_post_creates_and_links_the_contract(self, committing, session, creatable,
                                                         mapped_resource, armed):
        number = _award_number()
        armed[number] = _record(number, pi_name='Pat Doe')
        result = handle_new(committing, self._payload(creatable, mapped_resource, number,
                                                      'Pat Doe'))
        contract = Contract.get_by_number(session, number)
        assert contract is not None and contract.principal_investigator_user_id is None
        assert contract.pi_contact.name == 'Pat Doe'
        project = Project.get_by_projcode(session, result.projcode)
        assert [pc.contract_id for pc in project.contracts] == [contract.contract_id]
        assert any('is created from the NSF award' in w for w in result.warnings)

    def test_validate_only_writes_nothing(self, committing, session, creatable, mapped_resource,
                                          armed):
        number = _award_number()
        armed[number] = _record(number, pi_name='Pat Doe')
        result = handle_new(committing, self._payload(creatable, mapped_resource, number,
                                                      'Pat Doe'), validate_only=True)
        assert result.status == 'rechecked'
        assert [p['number'] for p in result.resolved['planned_contracts']] == [number]
        assert Contract.get_by_number(session, number) is None

    def test_the_identity_race(self, committing, session, creatable, mapped_resource, armed):
        """A post blocked on a missing roster user who is also the award PI writes no
        contract; once the user exists, the re-post makes that user the PI, even though
        NSF holds another email for them."""
        from factories import make_user
        pi = creatable['pi']
        pi.first_name, pi.last_name = 'Ian', f"Grooms{next_seq('gr')}"
        session.flush()
        number = _award_number()
        armed[number] = _record(number, pi_name=f'{pi.first_name} {pi.last_name}',
                                pi_email=f"{next_seq('elsewhere')}@nsf-side.example")
        payload = self._payload(creatable, mapped_resource, number,
                                f'{pi.first_name} {pi.last_name}')
        absent = f"absent{next_seq('u')}"
        payload['roles'].append({'roleType': 'User', 'username': absent,
                                 'beginDate': '2025-01-01', 'endDate': None})
        with pytest.raises(XrasActionRejected):
            handle_new(committing, payload)
        assert Contract.get_by_number(session, number) is None
        assert ExternalContact.get_by_email(session, armed[number].pi.email) is None

        make_user(session, username=absent)
        handle_new(committing, payload)
        contract = Contract.get_by_number(session, number)
        assert contract.principal_investigator_user_id == pi.user_id
        assert contract.pi_contact_id is None


class TestTheEarlyContract:
    """The sweep made the contract with a contact PI; the post that applies links the user."""

    def _payload(self, creatable, mapped_resource, contract, pi_name):
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['grants'] = [_grant(contract.contract_number, pi_name=pi_name)]
        return payload

    def test_the_roster_pi_is_linked(self, committing, session, creatable, mapped_resource):
        from factories import make_contract, make_external_contact
        pi = creatable['pi']
        pi.first_name, pi.last_name = 'Ian', f"Grooms{next_seq('gr')}"
        session.flush()
        contact = make_external_contact(session, name=f'Ian {pi.last_name}')
        contract = make_contract(session, contract_number=_award_number(), pi_contact=contact)
        result = handle_new(committing, self._payload(creatable, mapped_resource, contract,
                                                      f'Ian {pi.last_name}'))
        session.expire_all()
        assert contact.user_id == pi.user_id
        assert contract.principal_investigator_user_id == pi.user_id
        assert any(f'is SAM user {pi.username}' in w for w in result.warnings)

    def test_another_grant_pi_stays_a_contact(self, committing, session, creatable,
                                              mapped_resource):
        from factories import make_contract, make_external_contact
        contact = make_external_contact(session, name=f"Grant Holder{next_seq('gh')}")
        contract = make_contract(session, contract_number=_award_number(), pi_contact=contact)
        handle_new(committing, self._payload(creatable, mapped_resource, contract,
                                             contact.name))
        session.expire_all()
        assert contact.user_id is None and contract.principal_investigator_user_id is None

    def test_a_refused_post_links_nobody(self, committing, session, creatable, mapped_resource):
        from factories import make_contract, make_external_contact
        pi = creatable['pi']
        pi.first_name, pi.last_name = 'Ian', f"Grooms{next_seq('gr')}"
        session.flush()
        contact = make_external_contact(session, name=f'Ian {pi.last_name}')
        contract = make_contract(session, contract_number=_award_number(), pi_contact=contact)
        payload = self._payload(creatable, mapped_resource, contract, contact.name)
        payload['roles'].append({'roleType': 'User', 'username': f"absent{next_seq('u')}",
                                 'beginDate': '2025-01-01', 'endDate': None})
        with pytest.raises(XrasActionRejected):
            handle_new(committing, payload)
        session.expire_all()
        assert contact.user_id is None
