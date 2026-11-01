"""The audience registry and who belongs to which audience (XRAS_SUBMISSION_SPRINT3.md R2)."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sam.accounting import allocation_type_guide as guide_model
from sam.schemas.forms import CreateAllocationTypeGuideForm
from sam.xras import audiences
from sam.xras.audiences import Audience, user_audiences
from tests.factories import (make_institution, make_organization, make_user,
                             make_user_institution, make_user_organization)

SQL = Path(__file__).resolve().parents[3] / 'scripts' / 'sql'


class TestUserAudiences:

    @pytest.fixture
    def user(self, session):
        return make_user(session)

    def test_an_organization_and_no_institution_is_staff(self, session, user):
        make_user_organization(session, user=user, organization=make_organization(session))
        session.refresh(user)
        assert user_audiences(user) == {'ncar'}

    def test_an_institution_alone_is_university(self, session, user):
        make_user_institution(session, user=user, institution=make_institution(session))
        session.refresh(user)
        assert user_audiences(user) == {'university'}

    def test_the_institution_wins_over_the_organization(self, session, user):
        """Legacy's COALESCE(institution, 'UCAR/NCAR:' || org)."""
        make_user_organization(session, user=user, organization=make_organization(session))
        make_user_institution(session, user=user, institution=make_institution(session))
        session.refresh(user)
        assert user_audiences(user) == {'university'}

    def test_neither_is_university(self, user):
        assert user_audiences(user) == {'university'}

    def test_an_ended_organization_does_not_count(self, session, user):
        make_user_organization(session, user=user, organization=make_organization(session),
                               end_date=datetime.now() - timedelta(days=1))
        session.refresh(user)
        assert user_audiences(user) == {'university'}

    def test_no_user_is_university(self):
        assert user_audiences(None) == {'university'}

    def test_a_user_holds_every_audience_whose_rule_applies(self, monkeypatch, user):
        monkeypatch.setattr(audiences, 'AUDIENCES', audiences.AUDIENCES + (
            Audience('wyoming', 'Wyoming', lambda u: True),))
        assert user_audiences(user) == {'university', 'wyoming'}


class TestTheRegistry:

    def test_keys_fit_the_column_and_are_unique(self):
        keys = [a.key for a in audiences.AUDIENCES]
        assert len(keys) == len(set(keys)) and all(len(k) <= 16 for k in keys)

    def test_staff_types_stay_off_the_visitor_page(self):
        assert audiences.VISITOR_AUDIENCES == {'university'}

    def test_the_guide_form_reads_the_registry(self):
        assert guide_model.AUDIENCE_LABELS is audiences.AUDIENCE_LABELS
        audience = CreateAllocationTypeGuideForm().fields['audience']
        assert tuple(audience.validate.choices) == tuple(a.key for a in audiences.AUDIENCES)

    @pytest.mark.parametrize('seed', ['seed_allocation_type_guide.sql',
                                      'seed_allocation_type_guide_pg.sql'])
    def test_every_seeded_audience_is_a_registry_key(self, seed):
        rows = re.findall(r"^\s*(?:SELECT|UNION ALL SELECT)\s+\d+\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,"
                          r"\s*\d+\s*,\s*(NULL|'[^']*')", (SQL / seed).read_text(), re.M)
        assert len(rows) == 7
        assert {r.strip("'") for r in rows if r != 'NULL'} <= set(audiences.AUDIENCE_LABELS)
