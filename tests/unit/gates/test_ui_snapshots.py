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


def _two_sides(tmp_path, before, after, name='page__desktop-light.styles.json.gz'):
    for side, dump in (('a', before), ('b', after)):
        (tmp_path / side).mkdir()
        (tmp_path / side / name).write_bytes(gzip.compress(json.dumps(dump).encode()))
    return str(tmp_path / 'a'), str(tmp_path / 'b')


def test_a_custom_property_alone_is_counted_but_does_not_fail(tmp_path, capsys):
    """It is an input, not paint: where it reaches the page a standard property moves too."""
    a, b = _two_sides(tmp_path,
                      _dump({'html>body>button': {'color': 'red', '--bs-btn-hover-bg': 'blue'}}),
                      _dump({'html>body>button': {'color': 'red', '--bs-btn-hover-bg': 'navy'}}))
    assert snap.main(['--compare', a, b]) == 0
    assert 'same (1 more differ only in custom properties)' in capsys.readouterr().out
    assert snap.main(['--compare', a, b, '--strict']) == 1
    assert '--bs-btn-hover-bg: blue -> navy' in capsys.readouterr().out


def test_px_tolerance_absorbs_sub_pixel_noise_only(tmp_path):
    a, b = _two_sides(tmp_path,
                      _dump({'html>body': {'height': '5836.42px', 'transform-origin': '159.13px 10.5px'}}),
                      _dump({'html>body': {'height': '5836.41px', 'transform-origin': '159.14px 10.5px'}}))
    assert snap.main(['--compare', a, b]) == 1
    assert snap.main(['--compare', a, b, '--px-tolerance', '0.05']) == 0
    assert not snap._within('10px', '12px', 0.05)
    assert not snap._within('10px solid red', '10px solid blue', 5)


def test_compare_pixels_names_the_differing_region(tmp_path, capsys):
    Image = pytest.importorskip('PIL.Image')
    for side, dot in (('a', None), ('b', None), ('c', (3, 4))):
        (tmp_path / side).mkdir()
        img = Image.new('RGB', (10, 10), 'white')
        if dot:
            img.putpixel(dot, (0, 0, 0))
        img.save(tmp_path / side / 'panel__desktop-light.png')
    Image.new('RGB', (10, 12), 'white').save(tmp_path / 'c' / 'other__desktop-light.png')
    Image.new('RGB', (10, 10), 'white').save(tmp_path / 'a' / 'other__desktop-light.png')
    assert snap.main(['--compare-pixels', str(tmp_path / 'a'), str(tmp_path / 'b')]) == 1   # other: one side only
    capsys.readouterr()
    assert snap.main(['--compare-pixels', str(tmp_path / 'a'), str(tmp_path / 'c')]) == 1
    out = capsys.readouterr().out
    assert 'panel__desktop-light.png: differs within (3, 4, 4, 5)' in out
    assert 'other__desktop-light.png: size 10x10 -> 10x12' in out


def test_parse_step_splits_fill_text_on_the_last_equals():
    assert snap.parse_step('click:[data-bs-target="#x"]') == ('click', '[data-bs-target="#x"]', None)
    assert snap.parse_step('wait:#card') == ('wait', '#card', None)
    assert snap.parse_step('reveal:#tree .collapse') == ('reveal', '#tree .collapse', None)
    assert snap.parse_step('fill:input[name="q"]=SCSG') == ('fill', 'input[name="q"]', 'SCSG')
    for bad in ('hover:#x', 'click:', 'fill:#q'):
        with pytest.raises(ValueError):
            snap.parse_step(bad)


def test_page_sets_have_unique_names_and_parseable_steps():
    assert snap.parse_step('scroll:.pace-chart') == ('scroll', '.pace-chart', None)
    for pages in snap.PAGE_SETS.values():
        names = [name for name, _url, _steps in pages]
        assert len(names) == len(set(names))   # a name is the screenshot's file name
        for _name, url, steps in pages:
            assert url.startswith('/')
            [snap.parse_step(s) for s in steps]


def test_chart_sheet_compare_names_the_differing_rendering(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('chart_sheet', REPO / 'scripts' / 'chart_sheet.py')
    sheet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sheet)
    for side, body in (('a', '<svg/>'), ('b', '<svg/>'), ('c', '<svg><g/></svg>')):
        (tmp_path / side).mkdir()
        (tmp_path / side / 'pace.small__desktop-light.svg').write_text(body)
    assert sheet.main(['--compare', str(tmp_path / 'a'), str(tmp_path / 'b')]) == 0
    assert sheet.main(['--compare', str(tmp_path / 'a'), str(tmp_path / 'c')]) == 1
    assert 'pace.small__desktop-light.svg: differs' in capsys.readouterr().out
