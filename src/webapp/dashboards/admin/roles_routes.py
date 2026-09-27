"""Admin > Roles & access: the samuel_role_* catalog, edited in place.

Every route is MANAGE_ROLES. Writes go through the model layer, which owns
the two invariants (acyclic roles, a surviving MANAGE_ROLES holder); the
handlers here only map a refusal to a form error. Each write drops this
process's catalog snapshot; the other replicas catch up within RBAC_DB_TTL.
"""

import logging

from flask import abort, current_app, make_response, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.groups import AdhocGroup
from sam.core.users import User
from sam.manage import management_transaction
from sam.resources.facilities import Facility
from sam.schemas.forms import AddGrantForm, SaveRoleForm
from sam.security.permissions import Permission
from sam.security.rbac_catalog import SUBJECT_TYPES
from sam.security.rbac_reports import build_effective, build_keys, permission_groups
from sam.security.samuel_roles import (RbacInvariantError, SamuelRole, SamuelRoleGrant,
                                       load_catalog)
from webapp.extensions import db
from webapp.utils.form_handler import FlattenedFieldErrors, FormError, HtmxFormHandler
from webapp.utils.htmx import read_flag, read_tab
from webapp.utils.rbac import invalidate_catalog, require_permission, require_permission_any_facility

from .blueprint import bp

logger = logging.getLogger(__name__)

_TABS = ('grants', 'roles', 'check')
_MISSING = 'dashboards/admin/fragments/roles_tables_missing.html'


def _tables_present() -> bool:
    """False until the DDL has run here; every card then draws one notice."""
    from flask import g
    from sqlalchemy import inspect as sa_inspect
    if not hasattr(g, '_samuel_role_tables'):
        g._samuel_role_tables = sa_inspect(db.engine).has_table('samuel_role')
    return g._samuel_role_tables
_GRANT_FORM = 'dashboards/admin/fragments/roles_grant_form_htmx.html'
_ROLE_FORM = 'dashboards/admin/fragments/roles_role_form_htmx.html'


def _active_roles():
    return (db.session.query(SamuelRole).filter(SamuelRole.is_active)
            .order_by(SamuelRole.name).all())


def _grant_form_context(form=None):
    """Choices for the add-grant form; ``form`` carries the values to keep."""
    form = form if form is not None else request.form
    keys = build_keys(db.session, config_names=_config_keys())
    return {
        'form': form,
        'subject_type': form.get('subject_type') or 'user',
        'subject_types': [(t, {'user': 'User', 'group': 'POSIX group',
                               'apikey': 'API key'}[t]) for t in SUBJECT_TYPES],
        'groups': (db.session.query(AdhocGroup).filter(AdhocGroup.is_active)
                   .order_by(AdhocGroup.group_name).all()),
        'key_options': [(k['name'], k['name']) for k in keys['keys']],
        'roles': _active_roles(),
        'permissions': [(p.value, p.value.replace('_', ' '))
                        for p in sorted(Permission, key=lambda p: p.value)],
        'facilities': (db.session.query(Facility).filter(Facility.is_active)
                       .order_by(Facility.facility_name).all()),
        'post_url': url_for('admin_dashboard.roles_grant_add'),
        'form_url': url_for('admin_dashboard.roles_grant_form'),
    }


@bp.route('/roles', methods=['GET'])
@login_required
@require_permission_any_facility(Permission.ACCESS_ADMIN_DASHBOARD)
@require_permission(Permission.MANAGE_ROLES)
def roles():
    """The page shell: grants, roles and a check tab."""
    return render_template('dashboards/admin/roles.html',
                           active_tab=read_tab('tab', _TABS, 'grants'), source=_source(),
                           tables_present=_tables_present())


def _source() -> str:
    return current_app.config.get('RBAC_SOURCE', 'defaults')


def _config_keys():
    """The webapp's own view of the ``API_KEYS_<NAME>`` keys, not the CLI's environment scan."""
    return sorted(current_app.config.get('API_KEYS') or {})


@bp.route('/htmx/roles/grants', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_grants_card():
    if not _tables_present():
        return render_template(_MISSING)
    show_revoked = read_flag(request.args, 'show_revoked')
    q = db.session.query(SamuelRoleGrant)
    if not show_revoked:
        q = q.filter(SamuelRoleGrant.is_active)
    grants = q.order_by(SamuelRoleGrant.subject_type, SamuelRoleGrant.subject_name,
                        SamuelRoleGrant.samuel_role_grant_id).all()
    names = {r.samuel_role_id: r.name for r in db.session.query(SamuelRole).all()}
    return render_template('dashboards/admin/fragments/roles_grants_card.html',
                           grants=grants, role_names=names, show_revoked=show_revoked,
                           **_grant_form_context(form=request.args))


@bp.route('/htmx/roles/grants/form', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_grant_form():
    """The add form alone, re-rendered when the subject type changes."""
    if not _tables_present():
        return render_template(_MISSING)
    return render_template(_GRANT_FORM, **_grant_form_context(form=request.args))


class _AddGrantHandler(FlattenedFieldErrors, HtmxFormHandler):
    schema_cls = AddGrantForm
    template = _GRANT_FORM
    error_prefix = 'Not granted'
    success_message = 'Grant added.'
    exception_map = ((RbacInvariantError, lambda e: str(e)), (ValueError, lambda e: str(e)))

    def clean(self, data):
        subject_type = data['subject_type']
        if subject_type == 'user':
            user = db.session.get(User, data['subject_user_id']) if data.get('subject_user_id') else None
            if user is None:
                raise FormError('Pick a user.')
            data['subject_name'] = user.username
        elif subject_type == 'group':
            name = data.get('subject_name')
            if not name or AdhocGroup.get_by_name(db.session, name) is None:
                raise FormError('Pick a POSIX group.')
        else:
            name = data.get('subject_name')
            if not name or name not in {k['name'] for k in build_keys(db.session, config_names=_config_keys())['keys']}:
                raise FormError('Pick an API key.')
        if bool(data.get('role_name')) == bool(data.get('permission')):
            raise FormError('Choose a role or a single permission, not both.')
        data['role'] = None
        if data.get('role_name'):
            data['role'] = SamuelRole.get_by_name(db.session, data['role_name'])
            if data['role'] is None or not data['role'].active:
                raise FormError(f"No active role named {data['role_name']!r}.")
        if data.get('facility_name'):
            if not db.session.query(Facility).filter(
                    Facility.facility_name == data['facility_name']).first():
                raise FormError(f"No facility named {data['facility_name']!r}.")
        return data

    def perform(self, data):
        return SamuelRoleGrant.create(
            db.session, subject_type=data['subject_type'], subject_name=data['subject_name'],
            by=current_user.username, role=data['role'],
            permission=Permission(data['permission']) if data.get('permission') else None,
            facility_name=data.get('facility_name'), note=data.get('note'))

    def after_commit(self, row):
        invalidate_catalog()

    def context(self):
        return _grant_form_context()

    def triggers(self, row):
        return {'reloadGrantsCard': {}}

    def detail(self, row):
        return repr(row)[1:-1]


@bp.route('/htmx/roles/grants', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_grant_add():
    return _AddGrantHandler().handle()


@bp.route('/htmx/roles/grants/<int:grant_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_grant_revoke(grant_id: int):
    """Stamp the grant revoked. A refusal replaces the row with the reason."""
    row = db.session.get(SamuelRoleGrant, grant_id)
    if row is None:
        return '', 404
    try:
        with management_transaction(db.session):
            row.revoke(by=current_user.username)
    except RbacInvariantError as exc:
        return render_template('dashboards/admin/fragments/roles_grant_refused_row.html',
                               message=str(exc))
    invalidate_catalog()
    response = make_response('')
    response.headers['HX-Trigger'] = 'reloadGrantsCard'
    return response


# ---- Roles -----------------------------------------------------------------

def _role_editor_context(role=None, form=None):
    """Everything the editor draws: the role, its parent's closure, the matrix."""
    catalog = load_catalog(db.session)
    form = form if form is not None else request.form
    parent_name = form.get('extends') if form else None
    if role is not None and not form:
        parent_name = next((r.name for r in db.session.query(SamuelRole).all()
                            if r.samuel_role_id == role.extends_role_id), None)
    inherited = set(p.value for p in catalog.roles.get(parent_name, ())) if parent_name else set()
    if form:
        direct = set(form.getlist('permissions')) if hasattr(form, 'getlist') else set(form.get('permissions', ()))
    else:
        direct = {p.value for p in role.direct_permissions()} if role else set()
    return {
        'role': role, 'form': form,
        'roles': [r for r in _active_roles() if role is None or r.samuel_role_id != role.samuel_role_id],
        'groups': permission_groups(),
        'direct': direct, 'inherited': inherited,
        'extends': parent_name or '',
        'post_url': (url_for('admin_dashboard.roles_role_save', name=role.name) if role
                     else url_for('admin_dashboard.roles_role_create')),
    }


@bp.route('/htmx/roles/roles', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_roles_card():
    if not _tables_present():
        return render_template(_MISSING)
    catalog = load_catalog(db.session)
    rows = db.session.query(SamuelRole).order_by(SamuelRole.name).all()
    names = {r.samuel_role_id: r.name for r in rows}
    holders = {}
    for g in db.session.query(SamuelRoleGrant).filter(SamuelRoleGrant.is_active,
                                                       SamuelRoleGrant.samuel_role_id.isnot(None)).all():
        holders[g.samuel_role_id] = holders.get(g.samuel_role_id, 0) + 1
    return render_template('dashboards/admin/fragments/roles_roles_card.html',
                           roles=[{
                               'row': r, 'extends': names.get(r.extends_role_id),
                               'direct': len(r.direct_permissions()),
                               'effective': len(catalog.roles.get(r.name, ())),
                               'holders': holders.get(r.samuel_role_id, 0),
                           } for r in rows])


@bp.route('/htmx/roles/roles/form', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_role_new_form():
    if not _tables_present():
        return render_template(_MISSING)
    return render_template(_ROLE_FORM, **_role_editor_context(form=request.args))


def _role_or_404(name: str) -> SamuelRole:
    role = SamuelRole.get_by_name(db.session, name)
    if role is None:
        abort(404)
    return role


@bp.route('/htmx/roles/roles/<name>/form', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_role_edit_form(name: str):
    return render_template(_ROLE_FORM, **_role_editor_context(_role_or_404(name), form=None))


class _SaveRoleHandler(FlattenedFieldErrors, HtmxFormHandler):
    schema_cls = SaveRoleForm
    template = _ROLE_FORM
    error_prefix = 'Not saved'
    success_message = 'Role saved.'
    exception_map = ((RbacInvariantError, lambda e: str(e)), (ValueError, lambda e: str(e)))
    role = None

    def clean(self, data):
        if self.role is None and SamuelRole.get_by_name(db.session, data['name']) is not None:
            raise FormError(f"A role named {data['name']!r} already exists.")
        data['parent'] = None
        if data.get('extends'):
            data['parent'] = SamuelRole.get_by_name(db.session, data['extends'])
            if data['parent'] is None or not data['parent'].active:
                raise FormError(f"No active role named {data['extends']!r}.")
            if self.role is not None and data['parent'].samuel_role_id == self.role.samuel_role_id:
                raise FormError('A role cannot extend itself.')
        # Unchecked means unchecked: the box is always drawn on an edit.
        data['active'] = 'active' in request.form if self.role is not None else True
        return data

    def perform(self, data):
        perms = [Permission(v) for v in data['permissions']]
        by = current_user.username
        if self.role is None:
            return SamuelRole.create(db.session, name=data['name'], permissions=perms, by=by,
                                     description=data.get('description'), extends=data['parent'])
        self.role.update(by=by, description=data.get('description') or '',
                         extends=data['parent'], clear_extends=data['parent'] is None,
                         active=data['active'])
        self.role.set_permissions(perms, by=by)
        return self.role

    def after_commit(self, role):
        invalidate_catalog()

    def context(self):
        return _role_editor_context(self.role)

    def triggers(self, role):
        return {'reloadRolesCard': {}}

    def detail(self, role):
        return f'{role.name}: {len(role.direct_permissions())} direct permission(s)'


@bp.route('/htmx/roles/roles', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_role_create():
    return _SaveRoleHandler().handle()


@bp.route('/htmx/roles/roles/<name>', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_role_save(name: str):
    return _SaveRoleHandler(role=_role_or_404(name)).handle()


# ---- Check -----------------------------------------------------------------

@bp.route('/htmx/roles/check', methods=['GET'])
@login_required
@require_permission(Permission.MANAGE_ROLES)
def roles_check():
    """What a subject resolves to, with the grants that produced it."""
    if not _tables_present():
        return render_template(_MISSING)
    subject = (request.args.get('subject') or '').strip()
    payload = build_effective(db.session, subject) if subject else None
    return render_template('dashboards/admin/fragments/roles_check_card.html',
                           subject=subject, payload=payload)
