"""Every `data-confirm-*` attribute a template writes is one the confirm dialog reads.

The readers are `htmx-config.js` (hx-confirm) and `actions.js` (confirm-submit); an
attribute they ignore fails silently (`data-confirm-ok` showed the default "Confirm").
"""
import re
from _paths import REPO_ROOT

WEBAPP_ROOT = REPO_ROOT / 'src' / 'webapp'
READERS = [WEBAPP_ROOT / 'static' / 'js' / name for name in ('htmx-config.js', 'actions.js')]

_GET_ATTR = re.compile(r"getAttribute\('data-confirm-([a-z-]+)'\)")
_DATASET = re.compile(r'dataset\.confirm([A-Z]\w*)')
_WRITTEN = re.compile(r'data-confirm-([a-z][a-z-]*)')


def _read_names():
    names = set()
    for path in READERS:
        text = path.read_text(encoding='utf-8')
        names.update(_GET_ATTR.findall(text))
        names.update(re.sub(r'(?<!^)([A-Z])', r'-\1', m).lower() for m in _DATASET.findall(text))
    return names


def test_readers_parse():
    assert {'title', 'label', 'variant', 'body', 'message'} <= _read_names()


def test_every_written_confirm_attribute_is_read():
    read = _read_names()
    offenders = []
    for path in WEBAPP_ROOT.rglob('*'):
        if path.suffix not in ('.html', '.py'):
            continue
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            offenders += [f'{path.relative_to(WEBAPP_ROOT)}:{n} data-confirm-{name}'
                          for name in _WRITTEN.findall(line) if name not in read]
    assert not offenders, (f'the confirm dialog reads only data-confirm-{{{",".join(sorted(read))}}}: '
                           + ', '.join(offenders))
