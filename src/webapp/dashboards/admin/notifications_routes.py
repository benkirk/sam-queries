"""Admin dashboard — Notification delivery log.

The page an operator reaches from the Configuration tile's ``Details »``.
Structurally this is the XRAS action-log page, which is the same problem
already solved well: facet chips with self-exclusion, a sortable paginated
table, and a detail modal per row.

WARNING: **One permission tier above the tile, deliberately.** The Configuration
card is ``VIEW_SYSTEM_CONFIG`` and renders counts; **every row here names a
real person's email address**, so it is ``SYSTEM_ADMIN``. The gate is at the
route rather than in the template, so a view-source cannot reveal what the
page chose not to draw — the same rule ``get_xras_pending_recipients``
follows in the query layer.

There is deliberately **no `sam-admin` equivalent**, a divergence from the
XRAS precedent where CLI and web share a query layer *so the two cannot
drift*. ``sam/queries/notifications.py`` is still built as a shared layer —
the door stays open — but nothing on the CLI consumes it yet.

See ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 8.
"""

import logging

from flask import abort, make_response, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import Session

from sam.notify import NOTIFICATION_KINDS, NOTIFICATION_STATUSES, NotifyConfig
from sam.notify.addressing_store import NotificationAddressing
from sam.notify.kinds import (
    FAMILIES, addressing_scopes, get_family, get_kind, kinds_in_family,
    scope_family,
)
from sam.notify.models import NotificationLog
from sam.notify.render import TemplateRenderer, shipped_template_names
from sam.notify.samples import palette, preview_context
from sam.notify.template_store import NotificationTemplateOverride
from sam.manage import management_transaction
from sam import Project
from sam.queries.notification_previews import is_project_kind, messages_for_project
from sam.queries.projects import search_projects_by_code_or_title
from sam.schemas.forms import AddAddressingForm, NotificationTemplateForm
from sam.queries.notifications import (
    count_recent_notifications,
    facet_notifications,
    get_addressing_rows,
    get_recent_notifications,
    get_template_overrides,
    summarize_notifications,
)
from webapp.extensions import db
from webapp.utils.faceted_log import build_facet_strip, parse_window
from webapp.utils.form_handler import FlattenedFieldErrors, FormError, HtmxFormHandler
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_modal_not_found, read_tab, register_typeahead,
)
from webapp.utils.notify import get_notifier
from webapp.utils.rbac import require_permission, Permission

from .blueprint import bp

logger = logging.getLogger(__name__)

#: The htmx form and swap target the facet chips write into.
_FORM_ID = 'notificationsFilterForm'
_FRAGMENT_TARGET = 'notificationsTableContainer'

#: Same default window as the XRAS action-log page.
_DEFAULT_DAYS = 30
_PER_PAGE = 50

_TABS = ('log', 'templates', 'addressing')
_PREVIEW_ROLES = ('lead', 'admin', 'user')
_EDITOR_TEMPLATE = 'dashboards/admin/fragments/notification_template_editor.html'


def _renderer() -> TemplateRenderer:
    """A renderer that sees the saved overrides, on its own short session."""
    return TemplateRenderer(session_factory=lambda: Session(db.engine))


def _template_rows():
    """One row per shipped template file, with the kind and facility it serves."""
    overrides = get_template_overrides(db.session)
    rows = []
    for name in shipped_template_names():
        stem, fmt = name.rsplit('.', 1)
        kind = next((k for k in NOTIFICATION_KINDS.values()
                     if stem == k.template_base
                     or stem.startswith(k.template_base + '-')), None)
        if kind is None:
            continue
        rows.append({
            'name': name, 'stem': stem, 'fmt': fmt, 'kind': kind.key,
            'label': kind.label,
            'facility': stem[len(kind.template_base) + 1:] or None,
            'customized': overrides.get(name),
        })
    return rows


def _template_row_or_404(name):
    for row in _template_rows():
        if row['name'] == name:
            return row
    abort(404)


def _parse_filters(args):
    """Read the query string into ``(filters, page)``.

    Multi-valued dimensions come through ``getlist`` so a chip strip can
    express "status in (failed, suppressed)".
    """
    since, page = parse_window(args, default_days=_DEFAULT_DAYS,
                               per_page=_PER_PAGE)
    filters = {
        'since': since,
        'statuses': [s for s in args.getlist('status') if s],
        'kinds': [k for k in args.getlist('kind') if k],
        'channels': [c for c in args.getlist('channel') if c],
        'search': (args.get('search', '') or '').strip() or None,
    }
    return filters, page


@bp.route('/htmx/notifications', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notifications():
    """The page shell: the delivery log, the template editor and addressing, as tabs."""
    config = NotifyConfig.from_environment()
    template_rows = _template_rows()
    selected_name = request.args.get('name') or None
    if selected_name and selected_name not in {r['name'] for r in template_rows}:
        abort(404)
    # `?kind=` is the editor's deep link into the log, pre-filtered.
    preselect_kind = request.args.get('kind') or None
    if preselect_kind not in NOTIFICATION_KINDS:
        preselect_kind = None
    return render_template(
        'dashboards/admin/notifications.html',
        summary=summarize_notifications(
            db.session, queued_stale_seconds=config.queued_stale_seconds),
        config=config.summary(),
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
        initial_log_url=url_for('admin_dashboard.notifications_log',
                                kind=preselect_kind),
        preselect_kind=preselect_kind,
        all_statuses=list(NOTIFICATION_STATUSES),
        all_kinds=sorted(NOTIFICATION_KINDS),
        default_days=_DEFAULT_DAYS,
        active_tab=read_tab('tab', _TABS, 'log'),
        template_rows=template_rows,
        selected_name=selected_name,
    )


@bp.route('/htmx/notifications/log', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notifications_log():
    """HTMX fragment: the filtered, paginated table plus its facet chips."""
    filters, page = _parse_filters(request.args)
    offset = (page['n'] - 1) * page['per_page']

    rows = get_recent_notifications(db.session, **filters,
                                    limit=page['per_page'], offset=offset)
    total = count_recent_notifications(db.session, **filters)

    # Self-excluding rollups: each dimension's chips ignore its OWN filter
    # while honoring every other one. See facet_notifications.
    status_counts = facet_notifications(db.session, 'status', **filters)
    kind_counts = facet_notifications(db.session, 'kind', **filters)
    channel_counts = facet_notifications(db.session, 'channel', **filters)

    # Zero-filled in vocabulary order, out-of-vocabulary appended. Channel has
    # no declared vocabulary, so it sorts by count instead of by position.
    status_facets = build_facet_strip(status_counts, NOTIFICATION_STATUSES)
    kind_facets = build_facet_strip(kind_counts, sorted(NOTIFICATION_KINDS))
    channel_facets = build_facet_strip(channel_counts)

    return render_template(
        'dashboards/admin/fragments/notifications_log.html',
        rows=rows, total=total, page=page, filters=filters,
        status_facets=status_facets,
        kind_facets=kind_facets,
        channel_facets=channel_facets,
        form_id=_FORM_ID,
        target_id=_FRAGMENT_TARGET,
        fragment_url=url_for('admin_dashboard.notifications_log'),
    )


@bp.route('/htmx/notifications/<int:log_id>', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_detail(log_id: int):
    """Modal body: everything about one delivery attempt.

    Rendered bodies are **not** stored, so there is nothing to leak here
    beyond the columns — but the columns include the recipient, which is why
    this route carries the same ``SYSTEM_ADMIN`` gate as the table.
    """
    row = db.session.get(NotificationLog, log_id)
    if row is None:
        return htmx_modal_not_found('Notification')
    return render_template(
        'dashboards/admin/fragments/notification_detail_modal.html', row=row)


def _editor_context(name: str, *, source=None, **extra):
    """Everything the editor fragment renders; ``source`` defaults to the live body."""
    rows = _template_rows()
    row = next(r for r in rows if r['name'] == name)
    return {
        'row': row,
        'source': _renderer().source(name) if source is None else source,
        'variables': palette(row['kind'], row['facility']),
        'template_rows': rows, 'selected_name': name,
        'roles': _PREVIEW_ROLES,
        'preview_url': url_for('admin_dashboard.notification_template_preview',
                               name=name),
        'save_url': url_for('admin_dashboard.notification_template_save',
                            name=name),
        'reset_url': url_for('admin_dashboard.notification_template_reset',
                             name=name),
        'log_url': url_for('admin_dashboard.notifications', tab='log',
                           kind=row['kind']),
        'project_kind': is_project_kind(row['kind']),
        'project_search_url': url_for(
            'admin_dashboard.htmx_project_search_for_preview'),
        'audience_url': url_for('admin_dashboard.notification_template_audience',
                                name=name),
        **extra,
    }


def _project_audience(row, args):
    """``(project, messages, error)`` for a ``project_id`` in a request mapping.

    No ``project_id`` means sample mode: ``(None, [], None)``. A project that
    yields no message explains why in ``error``; the preview shows it in
    the pane rather than failing the request.
    """
    raw = (args.get('project_id') or '').strip()
    if not raw:
        return None, [], None
    if not is_project_kind(row['kind']):
        return None, [], 'This template is not about a project.'
    project = db.session.get(Project, int(raw)) if raw.isdigit() else None
    if project is None:
        return None, [], 'Unknown project.'
    messages = messages_for_project(db.session, row['kind'], project,
                                    requested_by=current_user.username)
    if not messages:
        why = ('no allocation with an end date' if row['kind'] == 'expiration'
               else 'no lead or admin with an email address')
        return project, [], f'{project.projcode} has {why}, so nothing would be sent.'
    return project, messages, None


def _undeclared(row, body: str) -> list:
    """Names the body reads that no builder supplies; never raises."""
    try:
        return sorted(_renderer().undeclared_names(
            body, preview_context(row['kind'], row['facility'])))
    except Exception:
        return []


@bp.route('/htmx/notifications/templates/<name>', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_editor(name: str):
    """HTMX fragment: one template's source, its variables, and a preview pane."""
    _template_row_or_404(name)
    return render_template(_EDITOR_TEMPLATE, **_editor_context(name))


class _SaveTemplateHandler(FlattenedFieldErrors, HtmxFormHandler):
    """Save an edited body as the override for one shipped template."""

    schema_cls = NotificationTemplateForm
    template = _EDITOR_TEMPLATE
    error_prefix = 'Error saving template'

    def clean(self, data):
        # Render-on-save, not merely compile: a sandbox refusal is a runtime
        # error. A body that cannot render must never reach a real send,
        # where it would fail per recipient in the ledger.
        try:
            _renderer().render_source(
                self.row['name'], data['body'],
                preview_context(self.row['kind'], self.row['facility']))
        except Exception as exc:
            raise FormError(f'The template does not render: '
                            f'{type(exc).__name__}: {exc}')
        return data

    def perform(self, data):
        name = self.row['name']
        existing = NotificationTemplateOverride.get_by_name(db.session, name)
        if existing is None:
            NotificationTemplateOverride.create(
                db.session, name=name, body=data['body'],
                modified_by=current_user.username)
        else:
            existing.update(body=data['body'], modified_by=current_user.username)
        return data['body']

    def context(self):
        return _editor_context(self.row['name'],
                               source=request.form.get('body', ''))

    def on_success(self, body):
        return render_template(
            self.template,
            **_editor_context(self.row['name'], notice='Template saved.',
                              warnings=_undeclared(self.row, body)))


@bp.route('/htmx/notifications/templates/<name>', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_save(name: str):
    row = _template_row_or_404(name)
    return _SaveTemplateHandler(row=row).handle()


@bp.route('/htmx/notifications/templates/<name>/reset', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_reset(name: str):
    """Delete the override; the shipped file is the baseline and comes back."""
    _template_row_or_404(name)
    with management_transaction(db.session):
        existing = NotificationTemplateOverride.get_by_name(db.session, name)
        if existing is not None:
            db.session.delete(existing)
    return render_template(
        _EDITOR_TEMPLATE,
        **_editor_context(name, notice='Restored the shipped default.'))


@bp.route('/htmx/notifications/templates/<name>/preview', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_preview(name: str):
    """HTMX fragment: the posted body rendered against sample data.

    Always 200: htmx does not swap a 4xx/5xx, so a broken template must
    answer inside the pane, as an error panel, not as a dead button.
    """
    row = _template_row_or_404(name)
    body = request.form.get('body', '')
    role = request.form.get('role')
    if role not in _PREVIEW_ROLES:
        role = _PREVIEW_ROLES[0]
    renderer = _renderer()
    result = {'row': row, 'error': None, 'warnings': [], 'text': None,
              'html': None, 'audience': None, 'subject': None, 'role': role,
              'info': None,
              'addressing': get_notifier().addressing_for(
                  row['kind'], row['facility']).as_dict()}
    project, messages, info = _project_audience(row, request.form)
    if info:
        result['info'] = info
        return render_template(
            'dashboards/admin/fragments/notification_template_preview.html',
            **result)
    if messages:
        wanted = request.form.get('recipient')
        message = next((m for m in messages if m.recipient.address == wanted),
                       messages[0])
        context = renderer.context_for(message)
        result['subject'] = message.subject
        result['audience'] = (f'{project.projcode}, as it would reach '
                              f'{message.recipient.name or message.recipient.address} '
                              f'({message.recipient.role})')
    else:
        context = preview_context(row['kind'], row['facility'], role)
        result['subject'] = context['subject']
    try:
        result['warnings'] = sorted(renderer.undeclared_names(body, context))
        rendered = renderer.render_source(name, body, context)
    except Exception as exc:        # a template bug, whatever its type
        result['error'] = f'{type(exc).__name__}: {exc}'
    else:
        result['text' if row['fmt'] == 'txt' else 'html'] = rendered
    return render_template(
        'dashboards/admin/fragments/notification_template_preview.html',
        **result)


@bp.route('/htmx/notifications/templates/<name>/recipients', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_template_audience(name: str):
    """HTMX fragment: the "preview as" control, real people once a project is picked.

    The response triggers ``reloadTemplatePreview`` so the pane re-renders
    only after the recipient select exists.
    """
    row = _template_row_or_404(name)
    project, messages, error = _project_audience(row, request.args)
    resp = make_response(render_template(
        'dashboards/admin/fragments/notification_template_audience.html',
        row=row, roles=_PREVIEW_ROLES, project=project, messages=messages,
        error=error))
    resp.headers['HX-Trigger'] = 'reloadTemplatePreview'
    return resp


def _search_projects_for_preview(q, active_only):
    return search_projects_by_code_or_title(db.session, q, active=True)[:10]


# Same search and results template as the parent-project picker, gated on
# this surface's own permission.
register_typeahead(
    bp,
    rule='/htmx/notifications/templates/project-search',
    endpoint='htmx_project_search_for_preview',
    permission=Permission.SYSTEM_ADMIN,
    search=_search_projects_for_preview,
    template='dashboards/admin/fragments/project_search_results_fk_htmx.html',
    ctx_key='projects',
    min_len=1,
)


# ------------------------------------------------------------- addressing
_ADDRESSING_FORM = 'dashboards/admin/fragments/notification_addressing_form.html'


def scope_label(scope: str) -> str:
    """Human text for an addressing scope: the family, a kind, or a facility variant."""
    if scope in FAMILIES:
        return f'All {FAMILIES[scope].label.lower()}'
    stem, _, facility = scope.partition('-')
    label = get_kind(stem).label
    return f'{label} — {facility}' if facility else label


def _addressing_cards():
    """One dict per family: env defaults, operator rows, and the add-form choices."""
    rows = get_addressing_rows(db.session)
    cards = []
    for key in sorted(FAMILIES):
        family = get_family(key)
        env = NotifyConfig.addressing(key)
        cards.append({
            'key': key, 'label': family.label,
            'kinds': [k.label for k in kinds_in_family(key)],
            'env': env.as_dict(),
            'env_rows': [('cc', a) for a in env.cc] + [('bcc', a) for a in env.bcc],
            'rows': [r for r in rows if r.family == key],
            'scopes': [(s, scope_label(s)) for s in addressing_scopes(key)],
            'post_url': url_for('admin_dashboard.notification_addressing_add',
                                family=key),
        })
    return cards


@bp.route('/htmx/notifications/addressing', methods=['GET'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_addressing_card():
    """HTMX fragment: per-family copy lists, deployment defaults first."""
    return render_template(
        'dashboards/admin/fragments/notification_addressing_card.html',
        cards=_addressing_cards(), scope_label=scope_label)


def _family_or_404(family: str):
    if family not in FAMILIES:
        abort(404)
    return next(c for c in _addressing_cards() if c['key'] == family)


@bp.route('/htmx/notifications/addressing/<family>', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_addressing_add(family: str):
    """Add one cc/bcc address for a scope of this family."""
    card = _family_or_404(family)

    def do_add(data):
        if scope_family(data['scope']) != family:
            raise ValueError(f"{data['scope']} is not a {card['label']} scope")
        if NotificationAddressing.get_by_entry(db.session, **data):
            raise ValueError(f"{data['address']} is already a {data['field']} "
                             f"for {scope_label(data['scope'])}")
        return NotificationAddressing.create(
            db.session, created_by=current_user.username, **data)

    return handle_htmx_form_post(
        schema_cls=AddAddressingForm,
        template=_ADDRESSING_FORM,
        do_action=do_add,
        success_triggers={'reloadAddressingCard': {}},
        success_message='Address added.',
        success_detail=lambda row: f'{row.field}: {row.address} '
                                   f'for {scope_label(row.scope)}',
        error_prefix='Not added',
        extra_context={'card': card},
    )


@bp.route('/htmx/notifications/addressing/<int:row_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def notification_addressing_delete(row_id: int):
    """Remove one operator-added copy; deployment defaults are not rows."""
    row = db.session.get(NotificationAddressing, row_id)
    if row is None:
        return '', 404
    with management_transaction(db.session):
        db.session.delete(row)
    response = make_response('')
    response.headers['HX-Trigger'] = 'reloadAddressingCard'
    return response
