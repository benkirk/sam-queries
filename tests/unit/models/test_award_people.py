"""Mapping an award source's person onto a SAM user.

The rules under test are the ones research showed matter:

* email matching must ignore ``is_primary`` — it is unset for many external
  contract contacts, and filtering on it produced a false 0-of-12 match rate;
* matching must be case-insensitive (agencies send ``Thomas.Haine@jhu.edu``);
* a name that matches two users is *not* a match — the form shows a hint and
  lets the operator choose rather than guessing;
* a miss returns ``None`` and never creates a user.
"""

import pytest

from sam.core.users import EmailAddress
from sam.integration.awards.base import PersonRef
from sam.integration.awards.people import resolve_person
from factories.core import make_user

pytestmark = pytest.mark.unit


def _add_email(session, user, address, *, is_primary=False, active=True):
    row = EmailAddress(user_id=user.user_id, email_address=address,
                       is_primary=is_primary, active=active)
    session.add(row)
    session.flush()
    return row


class TestResolveByEmail:

    def test_matches_a_non_primary_address(self, session):
        """The load-bearing case: external contacts have no primary flag."""
        user = make_user(session, first_name='Carrie', last_name='Black')
        _add_email(session, user, 'cblack@nsf.gov', is_primary=False)

        found = resolve_person(session, PersonRef(name='Carrie E. Black',
                                                  email='cblack@nsf.gov'))
        assert found is not None
        assert found.user_id == user.user_id

    def test_match_is_case_insensitive(self, session):
        user = make_user(session, first_name='Thomas', last_name='Haine')
        _add_email(session, user, 'thaine@jhu.edu')

        found = resolve_person(session, PersonRef(email='Thomas.Haine@JHU.EDU'))
        assert found is None       # different local part — not a match

        found = resolve_person(session, PersonRef(email='  THaine@JHU.edu '))
        assert found is not None and found.user_id == user.user_id

    def test_email_wins_over_a_conflicting_name(self, session):
        by_email = make_user(session, first_name='Robert', last_name='Smith')
        _add_email(session, by_email, 'rsmith@nsf.gov')
        make_user(session, first_name='Bob', last_name='Jones')

        found = resolve_person(session, PersonRef(name='Bob Jones',
                                                  email='rsmith@nsf.gov'))
        assert found.user_id == by_email.user_id


class TestResolveByName:

    def test_falls_back_to_first_and_last(self, session):
        user = make_user(session, first_name='Andrea', last_name='Porras-Alfaro')

        found = resolve_person(session, PersonRef(name='Andrea Porras-Alfaro',
                                                  email='nobody@example.test'))
        assert found is not None and found.user_id == user.user_id

    def test_middle_initial_is_ignored(self, session):
        user = make_user(session, first_name='Carrie', last_name='Black')

        found = resolve_person(session, PersonRef(name='Carrie E. Black'))
        assert found is not None and found.user_id == user.user_id

    def test_ambiguous_name_is_not_a_match(self, session):
        """Two people share a name — the operator picks, we do not."""
        make_user(session, first_name='John', last_name='Smith')
        make_user(session, first_name='John', last_name='Smith')

        assert resolve_person(session, PersonRef(name='John Smith')) is None

    def test_single_token_name_is_not_a_match(self, session):
        make_user(session, first_name='Cher', last_name='Cher')
        assert resolve_person(session, PersonRef(name='Cher')) is None


class TestMisses:

    def test_unknown_person_returns_none(self, session):
        person = PersonRef(name='Kevin Griffin', email='griff@ldeo.columbia.edu')
        assert resolve_person(session, person) is None
        # The caller keeps the raw values so the form can render the hint.
        assert person.label == 'Kevin Griffin <griff@ldeo.columbia.edu>'

    def test_creates_nothing(self, session):
        from sam.core.users import User
        before = session.query(User).count()
        resolve_person(session, PersonRef(name='Nobody Here',
                                          email='nobody@example.test'))
        assert session.query(User).count() == before

    @pytest.mark.parametrize('person', [
        None,
        PersonRef(),
        PersonRef(email='not-an-email'),
    ])
    def test_empty_or_malformed_input(self, session, person):
        assert resolve_person(session, person) is None
