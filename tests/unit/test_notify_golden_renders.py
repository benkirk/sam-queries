"""Golden renders of every shipped notification template.

Each `{stem}.{txt,html}` is rendered against `preview_context` for a lead and
a plain member and compared with a committed snapshot. Text is byte-exact;
HTML is compared whitespace-normalized, because a `{% extends %}` layout
cannot be byte-stable without `trim_blocks`. Regenerate with
`NOTIFY_RENDER_REGEN=1 pytest tests/unit/test_notify_golden_renders.py` and
review the diff like any other code change.
"""

import os
from pathlib import Path

import pytest

from sam.notify import Message
from sam.notify.kinds import NOTIFICATION_KINDS
from sam.notify.render import TemplateRenderer
from sam.notify.samples import preview_context, sample_recipient

SNAPSHOT_DIR = Path(__file__).parent / 'snapshots' / 'notify_renders'
REGEN = os.environ.get('NOTIFY_RENDER_REGEN') == '1'
ROLES = ('lead', 'user')


def _stems():
    """(kind, facility, stem) for every shipped variant, via the resolver."""
    renderer = TemplateRenderer()
    seen = []
    for kind in sorted(NOTIFICATION_KINDS):
        facilities = ('UNIV', 'WNA') if NOTIFICATION_KINDS[kind].facility_aware else (None,)
        for facility in facilities:
            message = Message(kind=kind, recipient=sample_recipient(),
                              subject='s', facility=facility)
            stem = renderer.resolve(message)
            if stem and (kind, facility, stem) not in seen:
                seen.append((kind, facility, stem))
    return seen


STEMS = _stems()
CASES = [(kind, facility, stem, role)
         for kind, facility, stem in STEMS for role in ROLES]


def _normalize(fmt, body):
    return body if fmt == 'txt' else ' '.join(body.split())


@pytest.fixture(scope='module')
def renderer():
    return TemplateRenderer()


@pytest.mark.parametrize('kind,facility,stem,role', CASES,
                         ids=[f'{c[2]}-{c[3]}' for c in CASES])
def test_render_matches_snapshot(renderer, kind, facility, stem, role):
    context = preview_context(kind, facility, role)
    message = Message(kind=kind, recipient=sample_recipient(role),
                      subject=context['subject'], context=context,
                      facility=facility)
    rendered = renderer.render(message)
    assert rendered.template_text == f'{stem}.txt'
    for fmt, body in (('txt', rendered.text), ('html', rendered.html)):
        if body is None:
            continue
        path = SNAPSHOT_DIR / f'{stem}.{role}.{fmt}'
        if REGEN:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            continue
        assert path.exists(), f'{path} missing; run with NOTIFY_RENDER_REGEN=1'
        assert _normalize(fmt, body) == _normalize(fmt, path.read_text()), (
            f'{path.name} drifted; regenerate with NOTIFY_RENDER_REGEN=1 '
            'if the change is intentional')


def test_every_stem_is_covered():
    assert len(STEMS) == 8, STEMS


@pytest.mark.skipif(REGEN, reason='regenerating')
def test_no_orphan_snapshots():
    expected = {f'{stem}.{role}.{fmt}' for _, _, stem in STEMS
                for role in ROLES for fmt in ('txt', 'html')}
    on_disk = {p.name for p in SNAPSHOT_DIR.iterdir()}
    assert on_disk == expected, sorted(on_disk ^ expected)
