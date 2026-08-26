"""Static guard: a table cell holding an action-button strip must not wrap.

Two icon buttons side by side are ~105 px; the auto table layout shrinks the
Actions column to the widest single button and the second drops underneath,
turning every row into two button rows (94 px on the NSF Programs tab, 127 px
on the XRAS Activations card before its strip got ``flex-nowrap``). Same
tier and shape as ``test_collapse_trigger_rows.py``: template source, no DB,
no Flask, macro names matched as well as literal markup.

The rule: a ``<td>`` that carries two or more action buttons and is an action
cell (``text-end``, or any of the shared action macros) must say so with
``text-nowrap`` / ``white-space:nowrap``, or wrap the strip in ``flex-nowrap``.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'

_CELL = re.compile(r'<td\b([^>]*)>(.*?)</td>', re.S)
_ACTION = re.compile(
    r'<button\b'
    r'|edit_modal_button\('
    r'|delete_row_button\('
    r'|<a\b[^>]*class="[^"]*\bbtn\b',
    re.S,
)
_MACRO = re.compile(r'edit_modal_button\(|delete_row_button\(')


def _action_cells():
    """(path, line, attrs, body) for every cell that is an action strip."""
    for path in sorted(TEMPLATES.rglob('*.html')):
        text = path.read_text()
        for m in _CELL.finditer(text):
            attrs, body = m.group(1), m.group(2)
            if len(_ACTION.findall(body)) < 2:
                continue
            if 'text-end' not in attrs and not _MACRO.search(body):
                continue
            yield path, text.count('\n', 0, m.start()) + 1, attrs, body


def _no_wrap(attrs, body):
    return 'nowrap' in attrs or 'flex-nowrap' in body


def test_action_strips_cannot_wrap():
    bad = [f'{p.relative_to(TEMPLATES)}:{line}'
           for p, line, attrs, body in _action_cells() if not _no_wrap(attrs, body)]
    assert not bad, (
        'action cells that can wrap onto two button rows (add text-nowrap to '
        'the <td>, or flex-nowrap to the btn-group):\n  ' + '\n  '.join(bad))


def test_scan_is_not_vacuous():
    assert sum(1 for _ in _action_cells()) >= 10
