"""`sam.notify.samples` — the preview input and the variable palette.

The builder-parity half lives beside each builder's own tests
(`test_expiration_message_builder.py`, `test_xras_notices_builder.py`,
`test_task_expiration_notices.py`), because those already build real messages.
This file covers the module's own contract: every kind has a sample, every
variable has a note, and the output is stable.
"""

import json

import pytest

from sam.notify.kinds import NOTIFICATION_KINDS
from sam.notify.samples import (
    INJECTED_KEYS, VARIABLE_NOTES, palette, preview_context, sample_context,
    sample_recipient,
)

KINDS = sorted(NOTIFICATION_KINDS)


def _dotted_names(context):
    names = set()
    for key, value in context.items():
        names.add(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            names.update(f'{key}.{field}' for field in value[0])
    return names


class TestEveryKindHasASample:

    @pytest.mark.parametrize('kind', KINDS)
    def test_it_is_a_non_empty_json_serializable_dict(self, kind):
        context = sample_context(kind)
        assert context
        json.dumps(context)

    @pytest.mark.parametrize('kind', KINDS)
    def test_it_is_deterministic(self, kind):
        assert sample_context(kind) == sample_context(kind)

    def test_an_unknown_kind_raises(self):
        with pytest.raises(ValueError, match='unknown notification kind'):
            sample_context('nope')

    def test_the_facility_reaches_the_expiration_sample(self):
        assert sample_context('expiration', 'WNA')['facility'] == 'WNA'
        assert sample_context('expiration')['facility'] == 'UNIV'

    def test_only_the_supplement_adds_and_only_the_adjustment_changes(self):
        for kind in KINDS:
            if not kind.startswith('xras_'):
                continue
            context = sample_context(kind)
            assert bool(context['added']) == (kind == 'xras_supplement')
            assert bool(context['changes']) == (kind == 'xras_adjustment')
        assert sample_context('xras_adjustment')['changes'][0]['amount'][0] in '+-'


class TestThePreviewContext:

    @pytest.mark.parametrize('kind', KINDS)
    def test_it_adds_exactly_the_injected_keys(self, kind):
        extra = set(preview_context(kind)) - set(sample_context(kind))
        assert extra == set(INJECTED_KEYS)

    def test_the_role_selects_the_recipient(self):
        assert preview_context('expiration', role='user')['recipient_role'] == 'user'
        assert sample_recipient('admin').role == 'admin'


class TestTheNotes:
    """The palette is only useful if every row it can show has a note."""

    @pytest.mark.parametrize('kind', KINDS)
    def test_every_variable_has_a_note(self, kind):
        missing = _dotted_names(preview_context(kind)) - set(VARIABLE_NOTES)
        assert not missing, sorted(missing)

    def test_every_note_names_a_real_variable(self):
        known = set()
        for kind in KINDS:
            for facility in (None, 'WNA'):
                known |= _dotted_names(preview_context(kind, facility))
        # `sent`/`failed` are absent on the aborted path but present here.
        assert set(VARIABLE_NOTES) <= known, sorted(set(VARIABLE_NOTES) - known)

    @pytest.mark.parametrize('kind', KINDS)
    def test_the_palette_lists_every_variable_once_with_a_note(self, kind):
        rows = palette(kind)
        names = [row['name'] for row in rows]
        assert len(names) == len(set(names))
        assert set(names) == _dotted_names(preview_context(kind))
        assert all(row['note'] for row in rows)
        assert all(row['shape'] for row in rows)
