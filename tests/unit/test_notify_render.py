"""TemplateRenderer — facility resolution, the fallback constant, and filters.

The resolution chain is the interesting part. It replaces two symlinks
(`expiration.txt -> expiration-UNIV.txt`) whose meaning lived in the
filesystem and did not survive a wheel build; `DEFAULT_FACILITY_TEMPLATE`
says the same thing in the source.
"""

import pytest

from sam.notify import (
    DEFAULT_FACILITY_TEMPLATE, Message, Recipient, TemplateError,
    TemplateRenderer,
)


@pytest.fixture
def template_dir(tmp_path):
    """A template set mirroring the shipped one: two facilities, no generic."""
    (tmp_path / 'expiration-UNIV.txt').write_text(
        'UNIV text for {{ recipient_name }} about {{ project_code }}')
    (tmp_path / 'expiration-UNIV.html').write_text(
        '<p>UNIV html for {{ recipient_name }}</p>')
    (tmp_path / 'expiration-WNA.txt').write_text(
        'WNA text for {{ recipient_name }}')
    return tmp_path


@pytest.fixture
def renderer(template_dir):
    return TemplateRenderer(template_dir=template_dir)


def _message(**kwargs):
    kwargs.setdefault('kind', 'expiration')
    kwargs.setdefault('recipient', Recipient('pi@x.edu', name='A PI'))
    kwargs.setdefault('subject', 'Expiration Notice')
    kwargs.setdefault('context', {'project_code': 'SCSG0001'})
    return Message(**kwargs)


class TestFacilityResolution:

    def test_facility_specific_template_wins(self, renderer):
        rendered = renderer.render(_message(facility='WNA'))
        assert rendered.template_text == 'expiration-WNA.txt'
        assert 'WNA text' in rendered.text

    def test_unknown_facility_falls_back_to_the_default(self, renderer):
        """What the deleted symlinks meant, said out loud."""
        rendered = renderer.render(_message(facility='NCAR'))
        assert rendered.template_text == f'expiration-{DEFAULT_FACILITY_TEMPLATE}.txt'
        assert 'UNIV text' in rendered.text

    def test_no_facility_at_all_falls_back_to_the_default(self, renderer):
        rendered = renderer.render(_message(facility=None))
        assert rendered.template_text == 'expiration-UNIV.txt'

    def test_the_candidate_chain_is_deduped(self, renderer):
        """facility == UNIV must not try expiration-UNIV.txt twice."""
        names = renderer.candidates(_message(facility='UNIV'), 'txt')
        assert names == ['expiration-UNIV.txt', 'expiration.txt']

    def test_a_non_facility_aware_kind_skips_the_facility_step(self, renderer):
        names = renderer.candidates(
            _message(kind='xras_activation', facility='WNA'), 'txt')
        assert names == ['xras_activation.txt']


class TestHtmlIsOptional:

    def test_html_rendered_when_present(self, renderer):
        rendered = renderer.render(_message(facility='UNIV'))
        assert rendered.template_html == 'expiration-UNIV.html'
        assert '<p>UNIV html' in rendered.html

    def test_text_only_is_not_an_error(self, renderer):
        """WNA ships no .html in this fixture; the predecessor did the same."""
        rendered = renderer.render(_message(facility='WNA'))
        assert rendered.html is None
        assert rendered.template_html is None

    def test_missing_text_template_raises_with_the_chain_it_tried(self, tmp_path):
        renderer = TemplateRenderer(template_dir=tmp_path)
        with pytest.raises(TemplateError) as exc:
            renderer.render(_message(facility='UNIV'))
        assert 'expiration-UNIV.txt' in str(exc.value)


class TestContextAndFilters:

    def test_recipient_fields_are_available_without_being_passed(self, renderer):
        rendered = renderer.render(_message())
        assert 'A PI' in rendered.text

    def test_caller_context_wins_over_the_injected_defaults(self, renderer):
        rendered = renderer.render(_message(
            context={'project_code': 'X', 'recipient_name': 'Explicit'}))
        assert 'Explicit' in rendered.text

    @pytest.mark.parametrize('filter_name', [
        'fmt_number', 'fmt_pct', 'fmt_date', 'fmt_size', 'fmt_hours',
        'fmt_factor', 'fmt_ago', 'to_local_dt', 'alloc_unit',
    ])
    def test_sam_fmt_filters_are_registered_on_the_standalone_env(
            self, renderer, filter_name):
        """register_jinja_filters writes app.jinja_env, so a bare Environment
        gets none of these unless they are registered on it — which is what put
        a hardcoded 'core-hours' in commands.py:349 for every resource type."""
        assert filter_name in renderer.env.filters

    def test_alloc_unit_actually_works_in_a_notify_template(self, tmp_path):
        (tmp_path / 'expiration-UNIV.txt').write_text(
            "{{ resource_type | alloc_unit }}")
        renderer = TemplateRenderer(template_dir=tmp_path)
        rendered = renderer.render(_message(context={'resource_type': 'DISK'}))
        assert rendered.text.strip() and rendered.text.strip() != 'core-hours'

    def test_text_is_not_html_escaped(self, tmp_path):
        """A plain-text mail must say '&', not '&amp;'."""
        (tmp_path / 'expiration-UNIV.txt').write_text('{{ title }}')
        renderer = TemplateRenderer(template_dir=tmp_path)
        rendered = renderer.render(_message(context={'title': 'Wind & Sea'}))
        assert rendered.text == 'Wind & Sea'

    def test_html_is_escaped(self, tmp_path):
        (tmp_path / 'expiration-UNIV.txt').write_text('x')
        (tmp_path / 'expiration-UNIV.html').write_text('<p>{{ title }}</p>')
        renderer = TemplateRenderer(template_dir=tmp_path)
        rendered = renderer.render(_message(context={'title': 'Wind & Sea'}))
        assert '&amp;' in rendered.html


class TestKindValidation:

    def test_an_unknown_kind_raises_before_touching_the_filesystem(self, renderer):
        with pytest.raises(ValueError, match='unknown notification kind'):
            renderer.render(_message(kind='not_a_kind'))


class TestSandbox:
    """Operators will edit these bodies, so the environment is sandboxed."""

    @pytest.fixture
    def renderer(self):
        return TemplateRenderer()

    @pytest.mark.parametrize('probe', [
        "{{ ''.__class__.__mro__ }}",
        "{{ cycler.__init__.__globals__ }}",
        "{{ recipient_name.__class__.__name__ }}",
    ])
    def test_dunder_access_is_refused(self, renderer, probe):
        from jinja2.sandbox import SecurityError
        with pytest.raises(SecurityError):
            renderer.render_source('probe.txt', probe, {'recipient_name': 'x'})

    def test_the_printf_format_filter_still_works(self, renderer):
        out = renderer.render_source('f.txt', "{{ '%-6s' | format(code) }}|",
                                     {'code': 'ab'})
        assert out == 'ab    |'

    def test_fmt_filters_still_work(self, renderer):
        out = renderer.render_source('f.txt', '{{ n | fmt_number }}', {'n': 1234})
        assert out == '1,234'


class TestRenderSource:

    @pytest.fixture
    def renderer(self):
        return TemplateRenderer()

    def test_an_html_name_escapes_and_a_txt_name_does_not(self, renderer):
        assert renderer.render_source('x.html', '{{ v }}', {'v': '<b>'}) == '&lt;b&gt;'
        assert renderer.render_source('x.txt', '{{ v }}', {'v': '<b>'}) == '<b>'

    def test_a_child_resolves_the_shipped_base(self, renderer):
        out = renderer.render_source(
            'x.html',
            '{% extends "_email_base.html" %}{% block content %}BODY{% endblock %}',
            {})
        assert '<!DOCTYPE html>' in out and 'BODY' in out
        assert 'automated notification' in out

    def test_it_shadows_only_the_named_template(self, renderer):
        """The overlay must not replace the real file for later renders."""
        renderer.render_source('expiration-UNIV.txt', 'OVERRIDDEN', {})
        message = Message(kind='expiration', facility='UNIV', subject='s',
                          recipient=Recipient('pi@x.edu', name='A PI', role='lead'))
        assert 'OVERRIDDEN' not in renderer.render(message).text

    def test_compile_source_raises_on_bad_syntax(self, renderer):
        from jinja2 import TemplateSyntaxError
        with pytest.raises(TemplateSyntaxError):
            renderer.compile_source('x.txt', '{% if %}')
        renderer.compile_source('x.txt', '{{ ok }}')

    def test_undeclared_names_ignores_known_names_loop_vars_and_globals(self, renderer):
        source = ('{% for r in resources %}{{ r.name }}{% endfor %}'
                  '{{ project_code }}{{ typo }}{{ local_tz_label() }}')
        assert renderer.undeclared_names(source, {'resources', 'project_code'}) == {'typo'}


class _FakeSession:
    """Stands in for a SQLAlchemy session: returns canned override rows."""

    def __init__(self, rows, raise_with=None):
        self._rows = rows
        self._raise = raise_with

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _stmt):
        if self._raise is not None:
            raise self._raise
        rows = self._rows

        class _Result:
            @staticmethod
            def scalars():
                return iter(rows)
        return _Result()


class _Row:
    def __init__(self, name, body):
        self.name, self.body = name, body


class _CountingFactory:
    def __init__(self, rows=(), raise_with=None):
        self.rows, self.raise_with, self.calls = list(rows), raise_with, 0

    def __call__(self):
        self.calls += 1
        return _FakeSession(self.rows, self.raise_with)


class TestOverrides:
    """The DB-first loader: one query per renderer, files as the fallback."""

    def test_no_factory_means_no_query_and_the_file_renders(self, template_dir):
        renderer = TemplateRenderer(template_dir)
        assert renderer.override_names == []
        assert 'SCSG0001' in renderer.render(_message()).text

    def test_a_row_wins_over_the_file(self, template_dir):
        factory = _CountingFactory([_Row('expiration-UNIV.txt', 'DB {{ project_code }}')])
        renderer = TemplateRenderer(template_dir, session_factory=factory)
        rendered = renderer.render(_message())
        assert rendered.text == 'DB SCSG0001'
        assert rendered.template_text == 'expiration-UNIV.txt'
        assert renderer.override_names == ['expiration-UNIV.txt']
        assert renderer.source('expiration-UNIV.txt') == 'DB {{ project_code }}'

    def test_many_renders_cost_exactly_one_query(self, template_dir):
        factory = _CountingFactory([_Row('expiration-UNIV.txt', 'DB')])
        renderer = TemplateRenderer(template_dir, session_factory=factory)
        for _ in range(5):
            renderer.render(_message())
            renderer.render(_message(facility='WNA'))
        assert factory.calls == 1

    def test_a_database_failure_falls_back_to_the_files_with_one_warning(
            self, template_dir, caplog):
        from sqlalchemy.exc import ProgrammingError
        factory = _CountingFactory(raise_with=ProgrammingError(
            'SELECT ...', {}, Exception("Table 'sam.notification_template_override' doesn't exist")))
        renderer = TemplateRenderer(template_dir, session_factory=factory)
        with caplog.at_level('WARNING', logger='sam.notify.render'):
            for _ in range(3):
                assert 'SCSG0001' in renderer.render(_message()).text
        assert factory.calls == 1
        assert sum('overrides unavailable' in r.message for r in caplog.records) == 1

    def test_a_non_database_error_is_not_swallowed(self, template_dir):
        factory = _CountingFactory(raise_with=RuntimeError('bug'))
        renderer = TemplateRenderer(template_dir, session_factory=factory)
        with pytest.raises(RuntimeError):
            renderer.render(_message())

    def test_an_overridden_child_still_extends_the_shipped_base(self):
        body = ('{% extends "_email_base.html" %}'
                '{% block content %}CUSTOM {{ project_code }}{% endblock %}')
        factory = _CountingFactory([_Row('expiration-UNIV.html', body)])
        renderer = TemplateRenderer(session_factory=factory)
        html = renderer.render(_message()).html
        assert 'CUSTOM SCSG0001' in html and '<!DOCTYPE html>' in html

    def test_a_real_session_with_no_rows_renders_the_files(self, session):
        from sqlalchemy.orm import Session
        engine = session.get_bind()
        renderer = TemplateRenderer(session_factory=lambda: Session(engine))
        assert 'SCSG0001' in renderer.render(_message()).text
