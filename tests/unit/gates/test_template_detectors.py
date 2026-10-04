"""Template detectors of scripts/sweep_inventory.py held at zero: bs4-classes and row-buttons.

Bootstrap 5.3 dropped them (`thead-light`, `float-left`, `font-weight-bold`, ...), so on a template
they style nothing, silently. The 5.3 spelling or a house class (`table-subtle`) is the fix.
An icon-only outline button in a table cell is a row action: `.btn-row` (components.css), muted until
the row is hovered, with the verb in `title` and `aria-label`.
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
