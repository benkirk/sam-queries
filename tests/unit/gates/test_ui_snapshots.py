"""scripts/ui_snapshots.py --compare: the computed-style diff, which needs no browser."""
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_PATH = REPO / 'scripts' / 'ui_snapshots.py'
if not _PATH.exists():
    pytest.skip('scripts/ is not in this tree', allow_module_level=True)
_spec = importlib.util.spec_from_file_location('ui_snapshots', _PATH)
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)


def _dump(elements):
    styles, ids = {}, {}
    for style in elements.values():
        ids.setdefault(json.dumps(style, sort_keys=True), len(ids))
        styles[str(ids[json.dumps(style, sort_keys=True)])] = style
    return {'styles': styles, 'elements': {el: ids[json.dumps(s, sort_keys=True)] for el, s in elements.items()}}


def test_diff_styles_reports_changed_properties_and_moved_elements():
    before = _dump({'html>body': {'color': 'red', 'margin': '0px'}, 'html>body>p': {'color': 'red'},
                    'html>body>b::after': {'content': '"x"'}})
    after = _dump({'html>body': {'color': 'red', 'margin': '8px'}, 'html>body>p': {'color': 'red'},
                   'html>body>i': {'color': 'red'}})
    assert snap.diff_styles(before, after) == [
        ('html>body>b::after', '(element)', 'present', 'missing'),
        ('html>body>i', '(element)', 'missing', 'present'),
        ('html>body', 'margin', '0px', '8px')]
    assert snap.diff_styles(before, before) == []


def test_diff_styles_ignores_the_server_origin_in_urls():
    url = 'url("http://localhost:{}/static/img/w.png")'
    assert snap.diff_styles(_dump({'html>body': {'background-image': url.format(5053)}}),
                            _dump({'html>body': {'background-image': url.format(5052)}})) == []


def test_compare_dirs_exits_nonzero_on_any_difference(tmp_path, capsys):
    same = _dump({'html>body': {'color': 'red'}})
    for side, dump in (('a', same), ('b', same), ('c', _dump({'html>body': {'color': 'blue'}}))):
        (tmp_path / side).mkdir()
        (tmp_path / side / 'page__desktop-light.styles.json.gz').write_bytes(gzip.compress(json.dumps(dump).encode()))
    assert snap.main(['--compare', str(tmp_path / 'a'), str(tmp_path / 'b')]) == 0
    assert snap.main(['--compare', str(tmp_path / 'a'), str(tmp_path / 'c')]) == 1
    assert 'html>body  color: red -> blue' in capsys.readouterr().out


def test_parse_step_splits_fill_text_on_the_last_equals():
    assert snap.parse_step('click:[data-bs-target="#x"]') == ('click', '[data-bs-target="#x"]', None)
    assert snap.parse_step('wait:#card') == ('wait', '#card', None)
    assert snap.parse_step('reveal:#tree .collapse') == ('reveal', '#tree .collapse', None)
    assert snap.parse_step('fill:input[name="q"]=SCSG') == ('fill', 'input[name="q"]', 'SCSG')
    for bad in ('hover:#x', 'click:', 'fill:#q'):
        with pytest.raises(ValueError):
            snap.parse_step(bad)
