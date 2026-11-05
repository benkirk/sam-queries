"""ExternalContact: find-or-create, the PI invariant, and linking a user who arrives later."""

from datetime import datetime, timedelta

import pytest

from sam.core.users import EmailAddress
from sam.projects.contracts import Contract, ExternalContact, normalize_email
from tests.factories._seq import next_seq
from tests.factories.core import make_user
from tests.factories.projects import (
    make_contract, make_contract_source, make_external_contact,
)


def _address():
    return f"{next_seq('XCT').lower()}@Example.org"


def test_normalize_email():
    assert normalize_email('  A.B@Example.ORG ') == 'a.b@example.org'
    assert normalize_email('not an address') is None
    assert normalize_email(None) is None


def test_create_lowercases_email_and_requires_name(session):
    address = _address()
    contact = ExternalContact.create(session, name=' Pat Doe ', email=address)
    assert contact.name == 'Pat Doe' and contact.email == address.lower()
    assert contact.creation_time is not None and contact.is_active
    with pytest.raises(ValueError):
        ExternalContact.create(session, name='  ')


def test_find_or_create_matches_email_case_insensitively(session):
    address = _address()
    first = ExternalContact.find_or_create(session, name='Pat Doe', email=address.upper())
    again = ExternalContact.find_or_create(session, name='Someone Else', email=address)
    assert again is first


def test_find_or_create_name_matches_only_an_emailless_contact(session):
    name = f"Name {next_seq('XCN')}"
    with_email = ExternalContact.create(session, name=name, email=_address())
    bare = ExternalContact.find_or_create(session, name=name)
    assert bare is not with_email and bare.email is None
    assert ExternalContact.find_or_create(session, name=name.upper()) is bare


def test_contract_requires_a_pi(session):
    source = make_contract_source(session)
    with pytest.raises(ValueError, match='principal investigator'):
        Contract.create(session, contract_number=next_seq('CTR'), title='t',
                        start_date=datetime.now(), contract_source_id=source.contract_source_id)


def test_contract_with_contact_pi(session):
    contact = make_external_contact(session, name='Program Officer')
    source = make_contract_source(session)
    contract = Contract.create(session, contract_number=next_seq('CTR'), title='t',
                               start_date=datetime.now() - timedelta(days=1),
                               contract_source_id=source.contract_source_id,
                               pi_contact_id=contact.external_contact_id)
    session.refresh(contract)
    assert contract.principal_investigator is None
    assert contract.pi_person.name == 'Program Officer'
    assert contract.pi_person.user is None and contract.pi_person.contact is contact
    assert contract in contact.pi_contracts


def test_pi_person_prefers_the_user(session):
    user = make_user(session)
    contact = make_external_contact(session, user=user)
    contract = make_contract(session, pi=user, pi_contact=contact)
    assert contract.pi_person.user is user
    assert contract.monitor_person is None


def test_link_user_fills_only_empty_user_fks(session):
    contact = make_external_contact(session)
    other = make_user(session)
    as_pi = make_contract(session, pi_contact=contact)
    as_monitor = make_contract(session, monitor_contact=contact)
    kept = make_contract(session, pi=other, pi_contact=contact)
    user = make_user(session)
    assert contact.link_user(user) == 2
    assert as_pi.principal_investigator_user_id == user.user_id
    assert as_monitor.contract_monitor_user_id == user.user_id
    assert kept.principal_investigator_user_id == other.user_id
    assert contact.user is user and user.external_contact is contact


def test_link_matching_users(session):
    address = _address()
    contact = make_external_contact(session, email=address)
    contract = make_contract(session, pi_contact=contact)
    user = make_user(session)
    session.add(EmailAddress(user_id=user.user_id, email_address=address.upper(),
                             is_primary=False))
    session.flush()
    assert ExternalContact.link_matching_users(session) >= 1
    assert contact.user_id == user.user_id
    assert contract.principal_investigator_user_id == user.user_id


def test_link_matching_users_skips_a_shared_address(session):
    address = _address()
    contact = make_external_contact(session, email=address)
    for _ in range(2):
        user = make_user(session)
        session.add(EmailAddress(user_id=user.user_id, email_address=address,
                                 is_primary=False))
    session.flush()
    ExternalContact.link_matching_users(session)
    assert contact.user_id is None


def test_find_or_create_refreshes_a_found_contact(session):
    """The edit form's prefilled Name and Organization are editable, so a change must persist."""
    address = _address()
    first = ExternalContact.find_or_create(session, name='Pat Doe', email=address)
    again = ExternalContact.find_or_create(session, name='Patricia Doe', email=address.upper(),
                                           organization='Uni')
    assert again is first and first.name == 'Patricia Doe' and first.organization == 'Uni'
    assert first.modified_time is not None
    stamp = first.modified_time
    assert ExternalContact.find_or_create(session, name='patricia doe', email=address) is first
    assert first.name == 'Patricia Doe' and first.modified_time == stamp
    bare = ExternalContact.find_or_create(session, name=f"Bare {next_seq('XCB')}")
    assert ExternalContact.find_or_create(session, name=bare.name, organization='Lab') is bare
    assert bare.organization == 'Lab' and bare.email is None
