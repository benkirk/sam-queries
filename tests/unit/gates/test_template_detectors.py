"""Template detectors of scripts/sweep_inventory.py: bs4-classes and row-buttons held at zero,
modal-alerts held by an equality ratchet.

Bootstrap 5.3 dropped them (`thead-light`, `float-left`, `font-weight-bold`, ...), so on a template
they style nothing, silently. The 5.3 spelling or a house class (`table-subtle`) is the fix.
An icon-only outline button in a table cell is a row action: `.btn-row` (components.css), muted until
the row is hovered, with the verb in `title` and `aria-label`.
A modal states its facts in one quiet panel (`.modal-facts`) and its help in glossary terms, not an
alert apiece (UNPLANNED_CITY_LEDGER.md entry 9).
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_PATH = REPO / 'scripts' / 'sweep_inventory.py'
if not _PATH.exists():
    pytest.skip('scripts/ is not in this tree', allow_module_level=True)
_spec = importlib.util.spec_from_file_location('sweep_inventory', _PATH)
inv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inv)


def test_no_bootstrap4_class_names(monkeypatch):
    monkeypatch.chdir(REPO)
    rows = inv.run(('bs4-classes',))['bs4-classes']
    sites = [f".{r['class']}  {site}" for r in rows for site in r['sites']]
    assert not sites, 'Bootstrap 4 class names that style nothing under 5.3:\n  ' + '\n  '.join(sites)


def test_row_actions_are_btn_row(monkeypatch):
    monkeypatch.chdir(REPO)
    rows = inv.run(('row-buttons',))['row-buttons']
    sites = [f"{r['file']}:{r['line']}" for r in rows]
    assert not sites, 'Icon-only outline buttons in table cells; use .btn btn-row:\n  ' + '\n  '.join(sites)


MODAL_ALERTS = 33   # equality ratchet: lower it when a modal sheds an alert; never raise it


def test_modal_alert_count_only_goes_down(monkeypatch):
    monkeypatch.chdir(REPO)
    rows = inv.run(('modal-alerts',))['modal-alerts']
    total = sum(r['count'] for r in rows)
    listing = '\n  '.join(f"{r['count']}  {r['file']}" for r in rows)
    assert total <= MODAL_ALERTS, (f'{total} alerts in modal bodies (ratchet {MODAL_ALERTS}). Put a fact in '
                                   f'.modal-facts or a glossary term instead:\n  {listing}')
    assert total == MODAL_ALERTS, f'Down to {total}: lower MODAL_ALERTS in this file to match.'
