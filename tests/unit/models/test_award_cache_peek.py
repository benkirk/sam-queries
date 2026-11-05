"""cached_award: the award resolve_award cached, read without a fetch."""

from datetime import date

from sam.integration.awards import award_is_cached, cached_award, resolve_award
from sam.integration.awards.base import AwardProvider, AwardRecord
from sam.integration.awards import registry
from sam.integration.awards.cache import _adapters


class _Provider(AwardProvider):
    name = 'Peek Test Provider'
    source = 'NSF'

    def __init__(self):
        self.calls = 0

    def supports(self, source_name, contract_number):
        return source_name == 'NSF'

    def fetch(self, contract_number, *, exact=False):
        self.calls += 1
        return AwardRecord(provenance=self.name, contract_number=contract_number,
                           title='t', start_date=date(2026, 1, 1))


def test_peek_never_fetches_and_sees_what_resolve_cached(monkeypatch):
    _adapters.clear()
    provider = _Provider()
    monkeypatch.setattr(registry, '_PROVIDERS', [provider])
    assert cached_award('NSF', 'AGS-1234567') is None and provider.calls == 0
    resolve_award('NSF', 'AGS-1234567', exact=True)
    assert cached_award('NSF', 'ags-1234567').contract_number == 'AGS-1234567'
    assert provider.calls == 1
    assert cached_award('NSF', 'AGS-1234567', exact=False) is None
    _adapters.clear()


class _NoSuchAward(_Provider):
    def fetch(self, contract_number, *, exact=False):
        self.calls += 1
        return None


def test_a_definite_no_such_award_is_cached_and_not_fetched_again(monkeypatch):
    _adapters.clear()
    provider = _NoSuchAward()
    monkeypatch.setattr(registry, '_PROVIDERS', [provider])
    assert not award_is_cached('NSF', 'AGS-7654321')
    assert resolve_award('NSF', 'AGS-7654321', exact=True) is None
    assert award_is_cached('NSF', 'AGS-7654321') and cached_award('NSF', 'AGS-7654321') is None
    assert resolve_award('NSF', 'AGS-7654321', exact=True) is None and provider.calls == 1
    assert not award_is_cached('NSF', 'AGS-7654321', exact=False)
    _adapters.clear()
