"""Static guard: no action button inside a Bootstrap collapse-trigger row.

**Why this tier exists.** A group row written as ``<tr data-bs-toggle="collapse">``
that also contains an action button has a bug: clicking the button expands or
collapses the row as well as performing the action. The modal still opens, so
it reads as cosmetic noise rather than a failure — which is why it went
unnoticed across four cards.

It cannot be fixed from the button. Bootstrap registers its data-api handlers
via ``EventHandler.on(document, ...)``, which passes the delegation flag as
``addEventListener``'s ``useCapture`` argument, so they run in the **capture**
phase on ``document``. Capture descends document → row → button, so Bootstrap
has already toggled the collapse before any listener on the button executes.
``data-stop-propagation`` (``static/js/actions.js``) is powerless here by
construction; its own docstring scopes it to element-level htmx bindings. The
fix is structural — put the toggle on the individual cells that should react
and leave the actions cell out of it. See
``templates/dashboards/fragments/collapse.html`` for the ``collapse_toggle``
macro and the full rationale.

**Why static rather than rendered.** This replaces
``test_no_action_button_inside_a_collapse_trigger_row``, which lived in
``test_htmx_queue_admin.py`` and rendered exactly one fragment
(``/admin/htmx/resources``). It could only ever see the card it was written
for, and a fresh instance duly landed in ``contracts_table_htmx.html`` — issue
#356. Scanning template *source* covers every fragment, including ones no test
renders and ones that do not exist yet, with no DB and no Flask. Same tier and
same shape as ``test_modal_shell_contract.py``.

The trade is that source cannot see markup a macro generates, so the scan
matches the two action-button macro *names* as well as literal ``<button>``.
Every action button in the admin fragments comes from one of those two macros.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'

# Jinja comments are stripped first: collapse.html documents the very pattern
# this test forbids, and the macro's usage example would otherwise be the
# loudest "violation" in the tree.
_JINJA_COMMENT = re.compile(r'{#.*?#}', re.S)

# Jinja delimiters, used to mask '>' that belongs to an expression rather than
# to the tag: `{% if q.dates|length > 1 %}` inside a <tr ...> would otherwise
# truncate the tag and make the check silently pass. Two real templates need
# this (organization_card.html, user_subtree.html).
_JINJA_SPAN = re.compile(r'{{.*?}}|{%.*?%}', re.S)

_TR_OPEN = re.compile(r'<tr\b[^>]*>', re.S)

# Either spelling of a trigger: the literal attribute, or the macro that emits
# it (a macro call on the <tr> would be just as wrong, and is easy to write).
_TRIGGER = re.compile(r'data-bs-toggle="collapse"|collapse_toggle\(')

# Anything clickable that performs an action. The old rendered guard looked
# only for '<button', so an action *link* would have walked straight past it.
_ACTION = re.compile(
    r'<button\b'
    r'|edit_modal_button\('
    r'|delete_row_button\('
    r'|<a\b[^>]*class="[^"]*\bbtn\b',
    re.S,
)


def _mask_jinja_gt(text):
    """Blank out '>' inside Jinja spans, preserving every offset.

    Returns a string the same length as ``text``, so match offsets from the
    masked copy index correctly into the original.
    """
    chars = list(text)
    for span in _JINJA_SPAN.finditer(text):
        for i in range(span.start(), span.end()):
            if chars[i] == '>':
                chars[i] = '\x00'
    return ''.join(chars)


def _trigger_rows():
    """Yield (template, line, row_html) for every collapse-trigger <tr>."""
    for path in sorted(TEMPLATE_ROOT.rglob('*.html')):
        raw = _JINJA_COMMENT.sub(
            lambda m: re.sub(r'[^\n]', ' ', m.group(0)),  # keep line numbers
            path.read_text(),
        )
        masked = _mask_jinja_gt(raw)
        for m in _TR_OPEN.finditer(masked):
            if not _TRIGGER.search(m.group(0)):
                continue
            end = raw.find('</tr>', m.end())
            row = raw[m.end():end] if end != -1 else raw[m.end():]
            line = raw[:m.start()].count('\n') + 1
            yield str(path.relative_to(TEMPLATE_ROOT)), line, row


def test_no_action_button_inside_a_collapse_trigger_row():
    offenders = []
    for template, line, row in _trigger_rows():
        hit = _ACTION.search(row)
        if hit:
            offenders.append(f'{template}:{line} (contains {hit.group(0)!r})')

    assert not offenders, (
        'A collapse-trigger <tr> contains an action button, so clicking the '
        'button will also toggle the row. Bootstrap\'s data-api runs in the '
        'capture phase, so no button-side guard can prevent it — move the '
        'toggle onto the non-action <td>s with the collapse_toggle macro '
        '(templates/dashboards/fragments/collapse.html).\n  '
        + '\n  '.join(offenders)
    )


def test_scan_is_not_vacuous():
    """Guard against the guard silently passing.

    A markup refactor that renamed the attribute, or a regex that stopped
    matching, would make the check above pass on an empty set. Rows with no
    buttons legitimately keep a <tr>-level trigger, so there should always be
    some.
    """
    rows = list(_trigger_rows())
    assert len(rows) >= 5, (
        f'only {len(rows)} collapse-trigger rows found across the template '
        f'tree — the scan has probably stopped matching. Found: '
        f'{[f"{t}:{n}" for t, n, _ in rows]}'
    )
