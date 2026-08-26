"""Incoming XRAS POSTs must apply with no outbound API in the graph.

The ingest + dispatch + handler path (`webapp/api/xras/`, `sam/xras/`) is what
XRAS calls to hand SAM an action; it resolves everything it needs from the
payload plus the SAM DB. If any module on that path imports the outbound client
(`sam/integration/xras_api/`), a future consolidation that "shares a helper"
could quietly make an inbound POST depend on `XRAS_OUTGOING_ENABLED` — so a SAM
outage of the outbound key would start rejecting the actions XRAS is pushing.

A convention would never fail anything; only a test does. It is the sibling of
`management_transaction`-only-in-handlers (imported in `handlers/base` and
nowhere else under `sam.xras`), pinned here so the seam cannot close by accident.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / 'src'

#: The inbound-serving packages. Every module here must stay outbound-free.
INBOUND_PKGS = [SRC / 'sam' / 'xras', SRC / 'webapp' / 'api' / 'xras']

#: The forbidden module prefix — the outbound HTTP client package.
OUTBOUND = 'sam.integration.xras_api'


def _inbound_modules():
    for pkg in INBOUND_PKGS:
        yield from sorted(pkg.rglob('*.py'))


def _imported_modules(path: Path) -> set:
    """Every dotted module imported anywhere in the file, lazy imports included."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


@pytest.mark.parametrize('path', list(_inbound_modules()),
                         ids=lambda p: str(p.relative_to(SRC)))
def test_inbound_never_imports_outbound_client(path):
    offenders = sorted(m for m in _imported_modules(path)
                       if m == OUTBOUND or m.startswith(OUTBOUND + '.'))
    assert not offenders, (
        f'{path.relative_to(SRC)} imports the outbound XRAS client '
        f'({offenders}). Incoming POSTs must apply without the outbound API; '
        f'share a pure helper over the payload + the SAM DB instead.')


def test_the_gate_actually_covers_the_apply_path():
    """A guard on an empty file list is green and worthless. Pin the two anchor
    modules so a moved/renamed package cannot silently empty the parametrize."""
    covered = {str(p.relative_to(SRC)) for p in _inbound_modules()}
    for anchor in ('sam/xras/dispatch.py', 'webapp/api/xras/actions.py'):
        assert anchor in covered, f'boundary gate stopped covering {anchor}'
