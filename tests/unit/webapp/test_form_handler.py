"""Unit tests for the HTMX form-handler lifecycle.

Covers `webapp.utils.form_handler.HtmxFormHandler` (the template-method
base class), `FormError`, the `_KwargFormHandler` adapter behind
`handle_htmx_form_post`, and the `modal_triggers` helper.

The hook matrix here is the parity gate for the adapter rewrite: every
behavior `handle_htmx_form_post` documented before the rewrite must hold
after it (same kwargs, same error routing, same success responses).
"""

import json

import pytest
from jinja2 import ChoiceLoader, DictLoader
from marshmallow import ValidationError, fields, validates_schema

from sam.schemas.forms import HtmxFormSchema
from webapp.utils.fk_validation import FKValidationError
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import handle_htmx_form_post, modal_triggers


class DemoForm(HtmxFormSchema):
    name = fields.String(required=True)
    amount = fields.Float(load_default=None)

    @validates_schema
    def _cross_field(self, data, **kwargs):
        if data.get('name') == 'crossfail':
            raise ValidationError('Cross-field failure.')


class DemoBoom(Exception):
    pass


class CapturingHandler(HtmxFormHandler):
    """Handler that records lifecycle calls and captures error renders
    instead of touching Jinja — the hook-routing tests don't need
    templates."""

    schema_cls = DemoForm

    def __init__(self, **entities):
        super().__init__(**entities)
        self.calls = []
        self.rendered = None

    def perform(self, data):
        self.calls.append(('perform', data))
        return dict(data, saved=True)

    def after_commit(self, result):
        self.calls.append(('after_commit', result))

    def triggers(self, result):
        return modal_triggers('reloadDemoCard')

    def render_errors(self, errors, field_errors=None):
        self.rendered = (list(errors), dict(field_errors or {}))
        return 'ERRORS-RENDERED'


@pytest.fixture
def demo_template(app):
    """Temporarily register a marker template so the *default*
    `render_errors` (real Jinja render) can be exercised."""
    name = '_test_form_handler_demo.html'
    source = ("errors={{ errors|join('|') }};"
              "fields={{ field_errors.keys()|sort|join(',') }};"
              "form_name={{ form.get('name', '') }};"
              "marker={{ marker|default('') }}")
    original = app.jinja_loader
    app.jinja_loader = ChoiceLoader([DictLoader({name: source}), original])
    try:
        yield name
    finally:
        app.jinja_loader = original


class TestHandlerLifecycle:

    def test_success_flow_runs_perform_then_after_commit(self, app):
        with app.test_request_context(
                '/x', method='POST', data={'name': 'demo', 'amount': '2.5'}):
            handler = CapturingHandler()
            resp = handler.handle()

        assert [c[0] for c in handler.calls] == ['perform', 'after_commit']
        payload = handler.calls[0][1]
        assert payload == {'name': 'demo', 'amount': 2.5}  # coerced by schema
        assert handler.calls[1][1] == dict(payload, saved=True)

        triggers = json.loads(resp.headers['HX-Trigger'])
        assert 'closeActiveModal' in triggers
        assert 'reloadDemoCard' in triggers
        assert triggers['showToast']['message'] == 'Saved successfully.'

    def test_schema_error_renders_inline_field_errors(self, app):
        with app.test_request_context('/x', method='POST',
                                      data={'amount': 'not-a-number'}):
            handler = CapturingHandler()
            resp = handler.handle()

        assert resp == 'ERRORS-RENDERED'
        assert handler.calls == []  # perform never ran
        form_level, field_errors = handler.rendered
        assert form_level == []
        assert set(field_errors) == {'name', 'amount'}

    def test_cross_field_error_is_form_level(self, app):
        with app.test_request_context('/x', method='POST',
                                      data={'name': 'crossfail'}):
            handler = CapturingHandler()
            handler.handle()

        form_level, field_errors = handler.rendered
        assert form_level == ['Cross-field failure.']
        assert field_errors == {}

    def test_form_error_from_clean_skips_perform(self, app):
        class Handler(CapturingHandler):
            def clean(self, data):
                raise FormError('No changes provided.')

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert handler.calls == []
        assert handler.rendered == (['No changes provided.'], {})

    def test_validation_error_from_clean_with_bare_message(self, app):
        class Handler(CapturingHandler):
            def clean(self, data):
                raise ValidationError('Rejected by clean().')

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert handler.rendered == (['Rejected by clean().'], {})

    def test_form_error_from_perform(self, app):
        class Handler(CapturingHandler):
            def perform(self, data):
                raise FormError('Domain said no.', 'Second reason.')

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert handler.rendered == (['Domain said no.', 'Second reason.'], {})

    def test_fk_validation_error_from_perform(self, app):
        class Handler(CapturingHandler):
            def perform(self, data):
                raise FKValidationError(['Selected lead does not exist.'])

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert handler.rendered == (['Selected lead does not exist.'], {})

    def test_exception_map_callable_and_static(self, app):
        class Handler(CapturingHandler):
            exception_map = (
                (DemoBoom, lambda e: f'Mapped: {e}'),
                (RuntimeError, 'Static mapped message.'),
            )
            def perform(self, data):
                raise self.boom  # noqa: B902 — injected per test

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler(boom=DemoBoom('kapow'))
            handler.handle()
            assert handler.rendered == (['Mapped: kapow'], {})

            handler = Handler(boom=RuntimeError('ignored'))
            handler.handle()
            assert handler.rendered == (['Static mapped message.'], {})

    def test_unmapped_exception_gets_error_prefix(self, app):
        class Handler(CapturingHandler):
            error_prefix = 'Error saving demo'
            def perform(self, data):
                raise ValueError('bad value')

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert handler.rendered == (['Error saving demo: bad value'], {})

    def test_after_commit_not_called_on_error(self, app):
        class Handler(CapturingHandler):
            def perform(self, data):
                self.calls.append(('perform', data))
                raise FormError('nope')

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            handler.handle()

        assert [c[0] for c in handler.calls] == ['perform']

    def test_partial_load_skips_required_fields(self, app):
        class Handler(CapturingHandler):
            partial = True

        with app.test_request_context('/x', method='POST', data={}):
            handler = Handler()
            handler.handle()

        # name (required) tolerated when absent; perform ran
        assert handler.rendered is None
        assert handler.calls[0][0] == 'perform'
        assert 'name' not in handler.calls[0][1]

    def test_success_redirect_flow(self, app):
        class Handler(CapturingHandler):
            success_message = 'Created.'
            success_redirect = '/somewhere/else'
            def detail(self, result):
                return 'DEMO0001'

        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = Handler()
            resp = handler.handle()
            from flask import get_flashed_messages
            flashes = get_flashed_messages(with_categories=True)

        assert resp.headers['HX-Redirect'] == '/somewhere/else'
        assert resp.get_data(as_text=True) == ''
        assert ('success', 'Created. DEMO0001') in flashes

    def test_constructor_entities_become_attributes(self, app):
        sentinel = object()
        handler = CapturingHandler(project=sentinel, extra=42)
        assert handler.project is sentinel
        assert handler.extra == 42

    def test_perform_is_required(self, app):
        with app.test_request_context('/x', method='POST', data={'name': 'n'}):
            handler = HtmxFormHandler()
            handler.schema_cls = DemoForm
            with pytest.raises(NotImplementedError):
                handler.perform({})

    def test_default_render_errors_context(self, app, demo_template):
        class Handler(CapturingHandler):
            def context(self):
                return {'marker': 'CTX'}
            # restore the real template render
            render_errors = HtmxFormHandler.render_errors

        with app.test_request_context('/x', method='POST',
                                      data={'name': 'crossfail'}):
            handler = Handler()
            handler.template = demo_template
            html = handler.handle()

        assert 'errors=Cross-field failure.' in html
        assert 'form_name=crossfail' in html
        assert 'marker=CTX' in html

    def test_default_render_errors_field_errors(self, app, demo_template):
        class Handler(CapturingHandler):
            render_errors = HtmxFormHandler.render_errors

        with app.test_request_context('/x', method='POST',
                                      data={'amount': 'zzz'}):
            handler = Handler()
            handler.template = demo_template
            html = handler.handle()

        assert 'fields=amount,name' in html


class TestKwargAdapter:
    """`handle_htmx_form_post` must behave exactly as before the rewrite."""

    def test_success_with_dynamic_triggers_and_detail(self, app):
        seen = {}

        with app.test_request_context('/x', method='POST',
                                      data={'name': 'demo'}):
            resp = handle_htmx_form_post(
                schema_cls=DemoForm,
                template='unused.html',
                do_action=lambda data: dict(data, id=7),
                success_triggers=lambda r: {'loadThing': r['id']},
                success_message='Demo saved.',
                success_detail=lambda r: f"#{r['id']}",
                after_commit=lambda r: seen.setdefault('after', r),
            )

        triggers = json.loads(resp.headers['HX-Trigger'])
        assert triggers['loadThing'] == 7
        assert triggers['showToast']['message'] == 'Demo saved.'
        assert seen['after']['id'] == 7
        assert '#7' in resp.get_data(as_text=True)

    def test_validation_error_rerenders_template(self, app, demo_template):
        with app.test_request_context('/x', method='POST', data={}):
            html = handle_htmx_form_post(
                schema_cls=DemoForm,
                template=demo_template,
                do_action=lambda data: pytest.fail('must not run'),
                success_triggers={},
                extra_context={'marker': 'STATIC'},
            )

        assert 'fields=name' in html
        assert 'marker=STATIC' in html

    def test_do_action_exception_uses_error_prefix(self, app, demo_template):
        def _boom(data):
            raise ValueError('duplicate key')

        with app.test_request_context('/x', method='POST',
                                      data={'name': 'demo'}):
            html = handle_htmx_form_post(
                schema_cls=DemoForm,
                template=demo_template,
                do_action=_boom,
                success_triggers={},
                error_prefix='Error creating demo',
                context_fn=lambda: {'marker': 'FN'},
            )

        assert 'errors=Error creating demo: duplicate key' in html
        assert 'marker=FN' in html

    def test_success_redirect_kwarg(self, app):
        with app.test_request_context('/x', method='POST',
                                      data={'name': 'demo'}):
            resp = handle_htmx_form_post(
                schema_cls=DemoForm,
                template='unused.html',
                do_action=lambda data: data,
                success_triggers={},
                success_redirect=lambda r: f"/demo/{r['name']}",
            )

        assert resp.headers['HX-Redirect'] == '/demo/demo'


class TestModalTriggers:

    def test_shape(self):
        assert modal_triggers('reloadFacilitiesCard') == {
            'closeActiveModal': {}, 'reloadFacilitiesCard': {}}
        assert modal_triggers() == {'closeActiveModal': {}}
        assert modal_triggers('a', 'b') == {
            'closeActiveModal': {}, 'a': {}, 'b': {}}

    def test_fresh_dict_per_call(self):
        first = modal_triggers('reloadX')
        first['mutated'] = True
        assert 'mutated' not in modal_triggers('reloadX')
