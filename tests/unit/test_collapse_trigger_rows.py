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

# ⚠️ Moving the toggle off the <tr> and onto the cells does NOT make the row
# safe — it makes the CELLS the thing that must not contain an action. A cell
# carrying both a toggle and a link has exactly the original bug, one level
# down, and the <tr>-only scan walked straight past it: two shipped instances
# were found by clicking them in a browser, not by this file.
#
# `<span>` is in the list because it is the escape hatch the fix uses — a
# chevron wrapped in its own trigger so the cell around it can hold a link —
# and a span that grew an action would be the same mistake again.
_CELL_OPEN = re.compile(r'<(td|th|span|div)\b[^>]*?>', re.S)

# ⚠️ Deliberately broader than `_ACTION`, and the difference is the whole
# reason this scan was worth adding. `_ACTION` matches an anchor only when it
# carries a `btn` class, because it was written for the admin cards' Edit and
# Delete controls. The entity-modal idiom — a projcode or username opening
# `#projectDetailsModal` / `#userDetailsModal` — is a PLAIN `<a>` with
# `text-decoration-none`, so `_ACTION` walked past every one of them. A first
# draft of this test duly passed on a cell that had been measured in a browser
# doing the exact thing it forbids.
#
# So: any `<a>` at all. A link that navigates away is no more welcome inside a
# trigger than one that opens a modal — both leave the row toggling behind
# whatever the click actually did.
#
# ⚠️ And, like `_ACTION`, it has to name MACROS as well as literal markup —
# source cannot see what a macro expands to. `request_cell()` renders a
# projcode as a modal link, and naming it here is not optional bookkeeping: a
# first draft of this test passed on the very cell that had just been measured
# in a browser opening a modal AND toggling its row, purely because the `<a>`
# lived in the macro body instead of at the call site.
_CELL_ACTION = re.compile(r'<button\b|<a\b|request_cell\(')

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


def _trigger_cells():
    """Yield (template, line, inner_html) for every collapse-trigger cell."""
    for path in sorted(TEMPLATE_ROOT.rglob('*.html')):
        raw = _JINJA_COMMENT.sub(
            lambda m: re.sub(r'[^\n]', ' ', m.group(0)),
            path.read_text(),
        )
        masked = _mask_jinja_gt(raw)
        for m in _CELL_OPEN.finditer(masked):
            if not _TRIGGER.search(m.group(0)):
                continue
            close = f'</{m.group(1)}>'
            end = raw.find(close, m.end())
            inner = raw[m.end():end] if end != -1 else raw[m.end():]
            line = raw[:m.start()].count('\n') + 1
            yield str(path.relative_to(TEMPLATE_ROOT)), line, inner


def test_no_action_button_inside_a_collapse_trigger_cell():
    """The same rule as below, applied to the fix for it.

    Relocating a toggle from the <tr> to the cells is the prescribed remedy,
    and it is only a remedy for the cells that do NOT hold an action. Put the
    toggle on the cell holding the link and the click still toggles the row —
    capture descends document → row → cell → link either way.
    """
    offenders = []
    for template, line, inner in _trigger_cells():
        hit = _CELL_ACTION.search(inner)
        if hit:
            offenders.append(f'{template}:{line} (contains {hit.group(0)!r})')

    assert not offenders, (
        'A collapse-trigger cell contains an action button or link, so '
        'clicking it will also toggle the row. Drop the toggle from THAT cell '
        '— its siblings can keep theirs — and if it held the chevron, wrap the '
        'chevron in its own trigger span so the affordance still works.\n  '
        + '\n  '.join(offenders)
    )


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
