"""contract_person_link: a SAM user links to the user card; an external contact is plain text."""

from flask import render_template_string

from sam.projects.contracts import ContractPerson, ExternalContact

_TEMPLATE = ("{% from 'dashboards/fragments/contract_bits.html' import contract_person_link %}"
             "{{ contract_person_link(person, True, compact=compact) }}")


def _render(app, person, compact=False):
    with app.test_request_context():
        return render_template_string(_TEMPLATE, person=person, compact=compact)


def test_contact_renders_name_and_email_without_a_link(app):
    contact = ExternalContact(name='Pat Doe', email='pat@uni.example')
    html = _render(app, ContractPerson(contact=contact))
    assert 'Pat Doe' in html and '(pat@uni.example)' in html
    assert 'hx-get' not in html and 'External contact' in html


def test_compact_contact_drops_the_email(app):
    html = _render(app, ContractPerson(contact=ExternalContact(name='Pat Doe', email='pat@uni.example')), compact=True)
    assert 'Pat Doe' in html and '(pat@uni.example)' not in html


def test_user_renders_the_user_link(app, session):
    from tests.factories.core import make_user
    user = make_user(session)
    html = _render(app, ContractPerson(user=user))
    assert 'hx-get' in html and user.username in html


def test_none_renders_a_dash(app):
    assert '—' in _render(app, None)
