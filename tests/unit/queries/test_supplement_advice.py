"""``sam.queries.supplement_advice``: the suggested Supplement over a request's date window."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from factories.summaries import make_comp_charge_summary
from sam.queries import supplement_advice as advice_mod
from sam.queries.supplement_advice import (round_up_nice, start_amount, suggest_amount,
                                           suggest_supplements)
from sam.resources.resources import ResourceType


class TestRoundUpNice:

    @pytest.mark.parametrize('x, nice', [(137_412, 140_000), (140_000, 140_000),
                                         (1_001, 1_100), (99.2, 100), (7.3, 8), (0.4, 1)])
    def test_two_significant_figures_up(self, x, nice):
        assert round_up_nice(x) == nice


class TestSuggestAmount:

    def test_the_need_beyond_the_balance(self):
        assert suggest_amount(100.0, 365, 6_500) == 30_000

    def test_a_balance_that_covers_the_window_suggests_nothing(self):
        assert suggest_amount(100.0, 30, 5_000) is None

    def test_a_spent_balance_counts_as_zero(self):
        assert suggest_amount(100.0, 10, -400) == 1_000

    @pytest.mark.parametrize('ceiling, last, capped', [(20_000, 50_000, 20_000),
                                                       (50_000, 20_000, 20_000),
                                                       (None, 20_000, 20_000),
                                                       (None, None, 37_000)])
    def test_capped_at_the_lower_of_the_limit_and_the_last_award(self, ceiling, last, capped):
        assert suggest_amount(100.0, 365, 0, ceiling=ceiling, last_amount=last) == capped

    @pytest.mark.parametrize('rate, days', [(None, 30), (0.0, 30), (100.0, 0), (100.0, -5)])
    def test_no_rate_or_no_window_suggests_nothing(self, rate, days):
        assert suggest_amount(rate, days, 0) is None


class TestStartAmount:

    @pytest.mark.parametrize('default, last, start', [(500_000, 1_001, 1_001),
                                                      (500_000, 2_000_000, 500_000),
                                                      (None, 1_001, 1_001), (5_000, None, 5_000),
                                                      (None, None, None), (0, 1_001, 1_001)])
    def test_the_lower_of_the_default_and_the_last_award(self, default, last, start):
        assert start_amount(default, last) == start


@pytest.fixture
def hpc(session):
    rt = session.query(ResourceType).filter(ResourceType.resource_type == 'HPC').one()
    return make_resource(session, resource_type=rt)


def _line(resource, **kw):
    return [dict({'resource_id': 7, 'sam_name': resource.resource_name, 'limit': None}, **kw)]


class TestSuggestSupplements:

    def test_recent_use_drives_the_rate(self, session, hpc):
        now = datetime.now()
        project = make_project(session)
        account = make_account(session, project=project, resource=hpc)
        make_allocation(session, account=account, amount=100_000,
                        start_date=now - timedelta(days=200), end_date=now + timedelta(days=165))
        make_comp_charge_summary(session, account=account, charges=9_000,
                                 activity_date=now - timedelta(days=10))
        out = suggest_supplements(session, project, _line(hpc),
                                  end_date=(now + timedelta(days=1200)).date(), now=now)[7]
        assert out.basis == 'recent' and out.rate_per_day == pytest.approx(100.0)
        assert out.remaining == pytest.approx(91_000)
        assert out.suggest == 29_000

    def test_an_ended_allocation_spreads_the_last_award(self, session, hpc):
        now = datetime.now()
        project = make_project(session)
        account = make_account(session, project=project, resource=hpc)
        end = now - timedelta(days=20)
        make_allocation(session, account=account, amount=36_500,
                        start_date=end - timedelta(days=364), end_date=end)
        floor = end.date()
        out = suggest_supplements(session, project, _line(hpc), end_date=date.today()
                                  + timedelta(days=200), floor=floor, now=now)[7]
        assert out.basis == 'last_allocation'
        assert out.rate_per_day == pytest.approx(100.0)
        # 200 days at 100/day less the 36,500 left at the old end: the balance covers it.
        assert out.suggest is None and out.remaining == pytest.approx(36_500)

    def test_the_type_limit_caps_below_the_last_award(self, session, hpc, monkeypatch):
        now = datetime.now()
        project = make_project(session)
        account = make_account(session, project=project, resource=hpc)
        make_allocation(session, account=account, amount=1_000_000,
                        start_date=now - timedelta(days=100), end_date=now + timedelta(days=30))
        monkeypatch.setattr(advice_mod, '_recent_rates',
                            lambda *a: {hpc.resource_name: 10_000.0})
        limit = SimpleNamespace(max_amount=500_000)
        out = suggest_supplements(session, project, _line(hpc, limit=limit),
                                  end_date=(now + timedelta(days=365)).date(), now=now)[7]
        assert out.suggest == 500_000

    def test_a_hand_picked_row_starts_at_the_lower_of_default_and_last(self, session, hpc):
        now = datetime.now()
        project = make_project(session)
        account = make_account(session, project=project, resource=hpc)
        make_allocation(session, account=account, amount=1_001,
                        start_date=now - timedelta(days=100), end_date=now + timedelta(days=300))
        limit = SimpleNamespace(max_amount=2_000_000, default_amount=500_000)
        out = suggest_supplements(session, project, _line(hpc, limit=limit),
                                  end_date=(now + timedelta(days=10)).date(), now=now)[7]
        assert out.suggest is None and out.start == 1_001

    def test_a_resource_the_project_never_held_gets_no_advice(self, session, hpc):
        project = make_project(session)
        assert suggest_supplements(session, project, _line(hpc),
                                   end_date=date.today() + timedelta(days=90)) == {}

    def test_a_yes_no_resource_gets_no_advice(self, session, hpc):
        now = datetime.now()
        project = make_project(session)
        account = make_account(session, project=project, resource=hpc)
        make_allocation(session, account=account, start_date=now - timedelta(days=40),
                        end_date=now + timedelta(days=40))
        assert suggest_supplements(session, project, _line(hpc, yes_no=True),
                                   end_date=date.today() + timedelta(days=90)) == {}
