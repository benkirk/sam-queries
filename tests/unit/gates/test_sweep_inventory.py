"""scripts/sweep_inventory.py: each detector finds its planted case and ignores a clean one."""
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

BIG_BODY = '''
    total = 0
    for i in range(n):
        if i % 2:
            total += i * 3 + offset
        else:
            total -= i // 2 - offset
    while total > 100:
        total = total // 3 + len(str(total))
    return {"total": total, "n": n, "offset": offset}
'''


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_private_imports_across_modules(tmp_path):
    a = _write(tmp_path, 'pkg/a.py', 'def _helper(): pass\ndef public(): pass\n')
    b = _write(tmp_path, 'pkg/b.py', 'from pkg.a import _helper\n')
    c = _write(tmp_path, 'pkg/c.py', 'from pkg.a import public\nfrom .a import _helper\n')
    s = _write(tmp_path, 'pkg/_shared.py', 'def _entry(): pass\n')
    d = _write(tmp_path, 'pkg/d.py', 'from pkg._shared import _entry\n')
    rows = inv.private_imports([a, b, c, s, d], root=tmp_path)
    assert [(r['helper'], r['importers'], r['package_private']) for r in rows] == [
        ('pkg.a._helper', 2, False), ('pkg._shared._entry', 1, True)]


def test_dup_functions_finds_renamed_copies_only_above_the_size_floor(tmp_path):
    one = _write(tmp_path, 'one.py', 'def first(n, offset):' + BIG_BODY + '\ndef tiny(x):\n    return x + 1\n')
    two = _write(tmp_path, 'two.py', 'def second(n, offset):\n    """Docstrings do not count."""' + BIG_BODY
                 + '\ndef tiny(x):\n    return x + 1\n')
    rows = inv.dup_functions([one, two], root=tmp_path)
    assert len(rows) == 1 and rows[0]['copies'] == 2
    assert [s.split()[-1] for s in rows[0]['sites']] == ['first', 'second']


def test_css_dead_marks_dynamic_stems(tmp_path):
    css = _write(tmp_path, 'site.css', '/* .commented { } */\n.used { color: red }\n.gone, .burn-3 { margin: 0 }\n'
                 '@media (max-width: 10px) { .used .also-gone { padding: 0 } }\n'
                 'a[href$=".pdf"] { color: blue }\n')
    page = _write(tmp_path, 'page.html', '<div class="used burn-{{ n }}"></div>')
    rows = inv.css_dead([css], [page])
    assert [(r['class'], r['dynamic']) for r in rows] == [
        ('also-gone', False), ('gone', False), ('burn-3', True)]


def test_css_shape_counts_and_repeated_blocks(tmp_path):
    one = _write(tmp_path, 'one.css', '.a { color: red; margin: 0 }\n.b { color: blue !important }\n')
    two = _write(tmp_path, 'two.css', '.c { margin: 0; color: red; }\n.d { color: red }\n')
    shape = inv.css_shape([one, two])
    assert {r['file'].rsplit('/', 1)[1]: r['important'] for r in shape['files']} == {'one.css': 1, 'two.css': 0}
    assert len(shape['repeated_blocks']) == 1 and shape['repeated_blocks'][0]['copies'] == 2


def test_inline_styles_counts_per_template(tmp_path):
    busy = _write(tmp_path, 'busy.html', '<p style="a"></p><p style="b"></p>')
    clean = _write(tmp_path, 'clean.html', '<p class="x"></p>')
    assert [(r['file'].rsplit('/', 1)[1], r['count']) for r in inv.inline_styles([busy, clean])] == [('busy.html', 2)]


def test_js_dup_names_and_listeners(tmp_path):
    one = _write(tmp_path, 'one.js', "function sync() {}\nconst only = () => 1;\n"
                 "document.addEventListener('htmx:afterSwap', sync);\n")
    two = _write(tmp_path, 'two.js', "const sync = async (e) => e;\nhtmx.on('htmx:afterSwap', sync);\n"
                 "document.addEventListener('htmx:load', sync);\n")
    dup = inv.js_dup([one, two])
    assert [r['name'] for r in dup['names']] == ['sync']
    assert [(r['event'], r['registrations']) for r in dup['listeners']] == [('htmx:afterSwap', 2)]


def test_js_dead_globals_and_actions(tmp_path):
    lib = _write(tmp_path, 'lib.js', "window.usedGlobal = 1;\nwindow.orphan = function () {};\n"
                 "if (window.orphan == null) {}\n"
                 "registerAction('live', f);\nregisterAction('stale', f);\n")
    caller = _write(tmp_path, 'caller.js', "usedGlobal();\n// e.g. <b data-action=\"doc-only\">\n")
    page = _write(tmp_path, 'page.html', '<a data-action="live"></a><select data-action-change="ghost">')
    dead = inv.js_dead([lib, caller], [lib, caller, page])
    assert [r['name'] for r in dead['globals']] == ['orphan']
    assert [r['name'] for r in dead['unused_actions']] == ['stale']
    assert dead['unregistered_actions'] == ['ghost']


def test_runs_on_the_real_tree(monkeypatch, capsys):
    monkeypatch.chdir(REPO)
    assert inv.main(['--top', '3']) == 0
    out = capsys.readouterr().out
    for detector in ('private-imports', 'dup-functions', 'css-dead', 'css-shape', 'inline-styles', 'js-dup', 'js-dead'):
        assert f'== {detector}:' in out


def test_jscpd_is_skipped_without_npx(monkeypatch, capsys):
    monkeypatch.setattr(inv.shutil, 'which', lambda name: None)
    inv.run_jscpd('js')
    assert 'skipped, npx not found' in capsys.readouterr().out
