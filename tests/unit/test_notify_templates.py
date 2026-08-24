"""The shipped template set — bijection with NOTIFICATION_KINDS, and packaging.

Gate (b) of NOTIFICATION_FRAMEWORK.md § 10. Two failures it exists to make
loud, both silent otherwise:

* a kind with **no template** — which surfaces as a TemplateError at send
  time, i.e. in front of a recipient who never gets their mail;
* an **orphaned template** — a file nothing can reach, which is how a
  facility variant gets edited for months without anyone receiving it.
"""

from pathlib import Path

import pytest

from sam.enums import FacilityName
from sam.notify import (
    DEFAULT_FACILITY_TEMPLATE, Message, NOTIFICATION_KINDS, Recipient,
    TEMPLATE_DIR, TemplateRenderer,
)


@pytest.fixture(scope='module')
def renderer():
    return TemplateRenderer()


def _message(kind, facility=None):
    return Message(kind=kind, facility=facility, subject='s',
                   recipient=Recipient('pi@x.edu', name='A PI', role='lead'))


class TestEveryKindResolves:

    @pytest.mark.parametrize('kind_key', sorted(NOTIFICATION_KINDS))
    @pytest.mark.parametrize('facility', [None] + [f.value for f in FacilityName])
    def test_a_text_template_exists_for_every_kind_and_facility(
            self, renderer, kind_key, facility):
        assert renderer.resolve(_message(kind_key, facility)) is not None, (
            f'kind {kind_key!r} has no text template for facility '
            f'{facility!r} — a send would raise TemplateError in front of '
            f'a recipient')

    @pytest.mark.parametrize('kind_key', sorted(NOTIFICATION_KINDS))
    def test_every_kind_renders_end_to_end_with_an_empty_context(
            self, renderer, kind_key):
        """Undefined variables render empty rather than raising; what this
        catches is a template with a syntax error, which nothing else would
        until a real send."""
        rendered = renderer.render(_message(kind_key, 'UNIV'))
        assert rendered.text.strip()


class TestTheAdjustmentNoticeNeverPresumesADirection:
    """`xras_adjustment` is the one kind whose message may be bad news.

    An Adjustment is the only action type whose amounts can be negative —
    `sam.xras.handlers.adjustment` exists precisely to honor the sign that
    legacy's copy-pasted `> 0` gate dropped. `adjust` therefore had no
    notification kind at all until the wording was written, on the grounds
    that "your allocation was cut" should not be sent by accident.

    So the words that would make a reduction read as a gift are a defect, not
    a style preference, and the subject line matters most: it is read long
    before the body can correct it.
    """

    FORBIDDEN = ('additional', 'added', 'increase', 'more time', 'extra')

    #: A reduction — the case the wording has to survive.
    CONTEXT = {
        'project_code': 'UHSS0003', 'project_title': 'A project',
        'changes': [{'resource_name': 'Derecho', 'amount': '-100,000',
                     'units': 'hours'}],
        'resources': [{'resource_name': 'Derecho', 'amount': '1.15M',
                       'units': 'hours', 'end_date': '2027-12-23'}],
    }

    @pytest.fixture
    def rendered(self, renderer):
        return renderer.render(Message(
            kind='xras_adjustment', subject='s',
            recipient=Recipient('pi@x.edu', name='A PI', role='lead'),
            context=self.CONTEXT))

    @pytest.mark.parametrize('part', ['text', 'html'])
    def test_the_body_claims_no_direction(self, rendered, part):
        """Asserted on the RENDERED part, not the source file: a Jinja comment
        explaining *why* the word is banned would otherwise trip the check on
        a template that is perfectly correct."""
        body = (getattr(rendered, part) or '').lower()
        found = [w for w in self.FORBIDDEN if w in body]
        assert not found, (
            f'the {part} part uses {found} — an Adjustment can REDUCE an '
            f'allocation, and this is the one notice that has to survive '
            f'being read by someone whose allocation shrank')

    def test_it_states_the_signed_change_and_the_resulting_total(self, rendered):
        assert '-100,000 hours' in rendered.text
        assert '1.15M hours' in rendered.text
        assert 'adjusted' in rendered.text.lower()


class TestNoOrphans:

    def test_every_shipped_file_is_reachable_from_some_kind(self, renderer):
        reachable = set()
        facilities = [None] + [f.value for f in FacilityName]
        for kind_key in NOTIFICATION_KINDS:
            for facility in facilities:
                stem = renderer.resolve(_message(kind_key, facility))
                if stem:
                    reachable.update({f'{stem}.txt', f'{stem}.html'})

        on_disk = {p.name for p in TEMPLATE_DIR.iterdir() if p.is_file()}
        orphans = on_disk - reachable
        assert not orphans, (
            f'templates nothing can reach: {sorted(orphans)} — either wire a '
            f'kind/facility to them or delete them')

    def test_the_generic_symlinks_are_gone(self):
        """They meant "UNIV" and said so only in the filesystem, which does
        not survive a wheel build. DEFAULT_FACILITY_TEMPLATE replaced them."""
        assert not (TEMPLATE_DIR / 'expiration.txt').exists()
        assert not (TEMPLATE_DIR / 'expiration.html').exists()
        assert (TEMPLATE_DIR / f'expiration-{DEFAULT_FACILITY_TEMPLATE}.txt').exists()

    def test_no_symlinks_at_all_under_the_template_dir(self):
        links = [p.name for p in TEMPLATE_DIR.iterdir() if p.is_symlink()]
        assert links == [], f'symlinked templates do not survive a wheel: {links}'


class TestPackaging:

    def test_templates_live_inside_the_package(self):
        """A path outside sam/notify/ would not be installed by package-data."""
        import sam.notify
        package_root = Path(sam.notify.__file__).parent
        assert TEMPLATE_DIR.parent == package_root

    def test_package_data_declares_them(self):
        """Without this line the wheel simply has no templates — invisible
        while everything runs from an editable install."""
        import tomllib
        pyproject = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        with pyproject.open('rb') as fh:
            data = tomllib.load(fh)
        package_data = data['tool']['setuptools']['package-data']
        assert 'templates/*' in package_data['sam.notify']

    def test_jinja2_is_a_declared_dependency(self):
        """It used to arrive transitively via flask; sam.notify imports it
        directly and the CLI is not a Flask app."""
        import tomllib
        pyproject = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        with pyproject.open('rb') as fh:
            data = tomllib.load(fh)
        names = [d.split('[')[0].split('>')[0].split('=')[0].strip()
                 for d in data['project']['dependencies']]
        assert 'jinja2' in names


class TestFacilityVariantsRenderDistinctly:

    def test_univ_and_wna_expiration_differ(self, renderer):
        """If these ever collapse to the same file the facility split is
        silently doing nothing."""
        univ = renderer.render(_message('expiration', 'UNIV'))
        wna = renderer.render(_message('expiration', 'WNA'))
        assert univ.template_text != wna.template_text
        assert univ.text != wna.text

    def test_an_unmapped_facility_gets_the_default_variant(self, renderer):
        ncar = renderer.render(_message('expiration', 'NCAR'))
        assert ncar.template_text == f'expiration-{DEFAULT_FACILITY_TEMPLATE}.txt'

    def test_text_and_html_always_come_from_the_same_variant(self, renderer):
        """The bug that made a WNA recipient see UNIV HTML: the HTML part is
        what most mail clients display."""
        for facility in [None] + [f.value for f in FacilityName]:
            rendered = renderer.render(_message('expiration', facility))
            if rendered.template_html:
                assert (rendered.template_text.rsplit('.', 1)[0]
                        == rendered.template_html.rsplit('.', 1)[0])
