"""No CSS class that nothing names: the css-dead detector of scripts/sweep_inventory.py, held at zero.

A class built at runtime (`burn-{{ n }}`) is dynamic and passes. A class styled on purpose
before anything uses it goes in the script's CSS_KEEP with its reason.
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


def test_every_styled_class_is_named_somewhere(monkeypatch):
    monkeypatch.chdir(REPO)
    dead = inv.run(('css-dead',))['css-dead']
    unnamed = [f"{r['file']}: .{r['class']}" for r in dead['classes'] if not (r['dynamic'] or r['kept'])]
    assert not unnamed, ('Styled but never named in a template, script or Python string. Delete the rule, '
                         'or add the class to CSS_KEEP in scripts/sweep_inventory.py with its reason:\n  '
                         + '\n  '.join(unnamed))
    assert not dead['stale_keeps'], f"CSS_KEEP entries no CSS styles any more: {dead['stale_keeps']}"
