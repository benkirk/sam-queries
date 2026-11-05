"""sam.manage.contracts: placing a contract person, and the award plan the XRAS post applies."""

from datetime import date

import pytest

from sam.core.users import EmailAddress
from sam.integration.awards.base import AwardRecord, PersonRef
from sam.manage.contracts import (
    NO_PI, NUMBER_TAKEN, PI_MISMATCH, INCOMPLETE_AWARD,
    apply_contract_plan, person_columns, plan_contract_from_award, same_surname,
)
from sam.projects.contracts import ExternalContact
from tests.factories._seq import next_seq
from tests.factories.core import make_user
from tests.factories.projects import make_contract, make_contract_source, make_external_contact


def _address(tag='CP'):
    return f"{next_seq(tag).lower()}@uni.example"


def _with_email(session, user, address):
    session.add(EmailAddress(user_id=user.user_id, email_address=address, is_primary=True))
    session.flush()
    session.expire(user)
    return user


def _number():
    """A worker-unique, award-shaped number (seven digits)."""
    return f"AGS-87{next_seq('AWD')[3:]}"


def _record(number=None, **overrides):
    defaults = dict(provenance='NSF Awards API', contract_number=number or _number(),
                    title='A study of things', start_date=date(2026, 9, 1),
                    end_date=date(2029, 8, 31), url='https://nsf.example/award',
                    program_name=None,
                    pi=PersonRef(name=f"Pat {next_seq('Doe')}", email=_address('PI')),
                    monitor=PersonRef(name='Olive Officer', email=_address('PO')),
                    unavailable_fields=frozenset())
    defaults.update(overrides)
    return AwardRecord(**defaults)


class TestPersonColumns:

    def test_a_user_goes_on_the_user_fk(self, session):
        user = make_user(session)
        assert person_columns(session, 'pi', user_id=user.user_id) == {
            'principal_investigator_user_id': user.user_id}

    def test_a_typed_contact_becomes_a_contact(self, session):
        address = _address()
        cols = person_columns(session, 'monitor', name='Olive Officer', email=address)
        contact = ExternalContact.get_by_email(session, address)
        assert cols == {'monitor_contact_id': contact.external_contact_id}

    def test_a_typed_contact_who_is_a_user_goes_on_the_user_fk(self, session):
        address = _address()
        user = _with_email(session, make_user(session), address)
        assert person_columns(session, 'pi', name='Whoever', email=address.upper()) == {
            'principal_investigator_user_id': user.user_id}
        assert ExternalContact.get_by_email(session, address) is None

    def test_nothing_typed_is_nothing(self, session):
        assert person_columns(session, 'monitor', name='  ') == {}


def test_same_surname():
    assert same_surname('C Spencer Jones', 'Spencer Jones')
    assert same_surname('José Núñez', 'Jose Nunez')
    assert not same_surname('Pat Doe', 'Sam Roe')
    assert not same_surname('', 'Sam Roe')


class TestPlan:

    def _source(self, session):
        return make_contract_source(session, name='NSF')

    def test_an_external_pi_becomes_a_contact(self, session):
        record = _record()
        plan, reason = plan_contract_from_award(session, record, source=self._source(session),
                                                wire_pi_name=record.pi.name)
        assert reason is None and not plan.pi.is_user
        contract = apply_contract_plan(session, plan)
        assert contract.principal_investigator_user_id is None
        assert contract.pi_person.name == record.pi.name
        assert contract.pi_person.email == record.pi.email
        assert contract.monitor_contact.name == 'Olive Officer'
        assert contract.end_date.hour == 23 and contract.start_date.hour == 0

    def test_a_roster_user_wins_even_with_a_different_email(self, session):
        """The identity race: a PI who arrives with the post is that user, not a contact."""
        pi = make_user(session, first_name='Ian', last_name=f"Grooms{next_seq('G')}")
        _with_email(session, pi, _address('ROSTER'))
        record = _record(pi=PersonRef(name=f'Ian G {pi.last_name}', email=_address('NSFPI')))
        plan, reason = plan_contract_from_award(session, record, source=self._source(session),
                                                roster_users=[pi])
        assert reason is None and plan.pi.user is pi
        contract = apply_contract_plan(session, plan)
        assert contract.principal_investigator_user_id == pi.user_id
        assert contract.pi_contact_id is None

    def test_an_existing_contact_is_reused(self, session):
        record = _record()
        contact = make_external_contact(session, name=record.pi.name, email=record.pi.email)
        plan, _ = plan_contract_from_award(session, record, source=self._source(session))
        assert apply_contract_plan(session, plan).pi_contact_id == contact.external_contact_id

    def test_a_different_wire_pi_is_refused(self, session):
        plan, reason = plan_contract_from_award(session, _record(), source=self._source(session),
                                                wire_pi_name='Somebody Else')
        assert plan is None and reason == PI_MISMATCH

    def test_a_taken_number_is_refused(self, session):
        number = _number()
        make_contract(session, contract_number=number.split('-')[1])
        plan, reason = plan_contract_from_award(session, _record(number),
                                                source=self._source(session))
        assert reason == NUMBER_TAKEN

    @pytest.mark.parametrize('overrides, expected', [
        ({'pi': None}, NO_PI),
        ({'title': ''}, INCOMPLETE_AWARD),
        ({'start_date': None}, INCOMPLETE_AWARD),
    ])
    def test_incomplete_awards_are_refused(self, session, overrides, expected):
        plan, reason = plan_contract_from_award(session, _record(**overrides),
                                                source=self._source(session))
        assert plan is None and reason == expected

    def test_no_award_is_refused(self, session):
        assert plan_contract_from_award(session, None, source=self._source(session)) == (
            None, INCOMPLETE_AWARD)
