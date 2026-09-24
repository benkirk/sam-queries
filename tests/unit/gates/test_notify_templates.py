"""The shipped template set — bijection with NOTIFICATION_KINDS, and packaging.

Gate (b) of NOTIFICATION_FRAMEWORK.md § 10. Two failures it exists to make
loud, both silent otherwise:

* a kind with **no template** — which surfaces as a TemplateError at send
  time, i.e. in front of a recipient who never gets their mail;
* an **orphaned template** — a file nothing can reach, which is how a
  facility variant gets edited for months without anyone receiving it.
"""

from pathlib import Path
from _paths import REPO_ROOT

import pytest

from sam.enums import FacilityName
from sam.notify import (
    DEFAULT_FACILITY_TEMPLATE, Message, NOTIFICATION_KINDS, Recipient,
    TEMPLATE_DIR, TemplateRenderer,
)
from sam.notify.render import shipped_template_names


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

        on_disk = set(shipped_template_names())
        orphans = on_disk - reachable
        assert not orphans, (
            f'templates nothing can reach: {sorted(orphans)} — either wire a '
            f'kind/facility to them or delete them')

    def test_every_partial_is_extended_or_included(self, renderer):
        """An underscore file is layout, so some shipped template must use it."""
        import jinja2.meta
        partials = {p.name for p in TEMPLATE_DIR.iterdir()
                    if p.is_file() and p.name.startswith('_')}
        referenced = set()
        for name in shipped_template_names():
            source = renderer.env.loader.get_source(renderer.env, name)[0]
            referenced.update(
                jinja2.meta.find_referenced_templates(renderer.env.parse(source)))
        assert partials and partials <= referenced, sorted(partials - referenced)

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
        pyproject = REPO_ROOT / 'pyproject.toml'
        with pyproject.open('rb') as fh:
            data = tomllib.load(fh)
        package_data = data['tool']['setuptools']['package-data']
        assert 'templates/*' in package_data['sam.notify']

    def test_jinja2_is_a_declared_dependency(self):
        """It used to arrive transitively via flask; sam.notify imports it
        directly and the CLI is not a Flask app."""
        import tomllib
        pyproject = REPO_ROOT / 'pyproject.toml'
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


def _kind_of(name):
    """``(kind_key, facility)`` for a shipped file, as the template editor maps it."""
    stem = name.rsplit('.', 1)[0]
    kind = next((k for k in NOTIFICATION_KINDS.values()
                 if stem == k.template_base or stem.startswith(k.template_base + '-')),
                None)
    return (kind.key, stem[len(kind.template_base) + 1:] or None) if kind else (None, None)


EDITABLE = [n for n in shipped_template_names() if _kind_of(n)[0]]


class TestEveryReferenceResolves:
    """What the editor's "Unknown variables" warning and a strict render say
    about the SHIPPED files: nothing. A misspelled loop field (``r.resource_nam``)
    escapes the static check but not StrictUndefined against the sample."""

    @pytest.mark.parametrize('name', EDITABLE)
    def test_no_unknown_variable(self, renderer, name):
        from sam.notify.samples import preview_context
        kind, facility = _kind_of(name)
        assert renderer.undeclared_names(renderer.source(name),
                                         preview_context(kind, facility)) == set()

    @pytest.mark.parametrize('name', EDITABLE)
    @pytest.mark.parametrize('role', ['lead', 'admin', 'user'])
    def test_it_renders_strictly_for_every_role(self, renderer, name, role):
        import jinja2
        from sam.notify.samples import preview_context
        kind, facility = _kind_of(name)
        strict = renderer.env.overlay(undefined=jinja2.StrictUndefined)
        strict.get_template(name).render(**preview_context(kind, facility, role))

    def test_an_import_at_the_top_is_not_unknown_inside_a_block(self, renderer):
        """The false positive on account_queue_summary.html: `person`/`status`."""
        source = ('{% extends "_email_base.html" %}'
                  '{% from "_account_cells.html" import person, status %}'
                  '{% block content %}{{ person(r) }}{{ status(r) }}{{ nope }}{% endblock %}')
        assert renderer.undeclared_names(source, {'r'}) == {'nope'}
