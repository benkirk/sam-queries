"""`sam.notify.kinds` — the family registry and the addressing-scope vocabulary."""

import pytest

from sam.notify.kinds import (
    FACILITY_VARIANTS, FAMILIES, NOTIFICATION_KINDS, addressing_scopes, families,
    get_family, kinds_in_family, message_scopes,
)
from sam.notify.render import shipped_template_names


class TestTheRegistry:

    def test_every_kind_belongs_to_a_registered_family(self):
        for kind in NOTIFICATION_KINDS.values():
            assert kind.family in FAMILIES, kind.key

    def test_every_family_has_at_least_one_kind(self):
        for key in FAMILIES:
            assert kinds_in_family(key), key

    def test_families_is_the_sorted_registry(self):
        assert families() == tuple(sorted(FAMILIES)) == ('expiration', 'task', 'xras')

    def test_only_the_task_family_is_not_about_a_project(self):
        assert {k for k, f in FAMILIES.items() if not f.about_project} == {'task'}

    def test_an_unknown_family_raises_with_the_vocabulary(self):
        with pytest.raises(ValueError, match='expiration, task, xras'):
            get_family('nope')


class TestAddressingScopes:

    def test_the_facility_variants_are_the_shipped_template_stems(self):
        stems = {name.rsplit('.', 1)[0] for name in shipped_template_names()}
        shipped = {s.split('-', 1)[1] for s in stems if '-' in s}
        assert shipped == set(FACILITY_VARIANTS)

    def test_expiration_offers_family_kind_and_facility_scopes(self):
        assert addressing_scopes('expiration') == [
            'expiration', 'expiration', 'expiration-UNIV', 'expiration-WNA']

    def test_xras_offers_no_facility_scopes(self):
        scopes = addressing_scopes('xras')
        assert scopes[0] == 'xras'
        assert scopes[1:] == [k.key for k in kinds_in_family('xras')]
        assert not any('-' in s for s in scopes)

    def test_an_unknown_family_raises(self):
        with pytest.raises(ValueError):
            addressing_scopes('nope')

    def test_every_message_scope_is_an_offered_scope(self):
        for kind in NOTIFICATION_KINDS.values():
            for facility in (None, *FACILITY_VARIANTS):
                offered = addressing_scopes(kind.family)
                assert set(message_scopes(kind.key, facility)) <= set(offered)

    def test_a_facility_message_matches_its_variant_only_when_aware(self):
        assert message_scopes('expiration', 'WNA') == [
            'expiration', 'expiration', 'expiration-WNA']
        assert message_scopes('xras_update', 'WNA') == ['xras', 'xras_update']
        assert message_scopes('expiration') == ['expiration', 'expiration']
