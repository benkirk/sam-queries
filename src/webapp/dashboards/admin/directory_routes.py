"""Admin dashboard: the Directory panels (docs/plans/DIRECTORY_CLIENT.md step 3).

A panel is an operator-initiated read of one person or organization from the directory
with SAM's drift beside it; Apply writes the kinds whose ``DIRECTORY_APPLY_<KIND>`` lever
is on, inside ``management_transaction``. The directory being down is a 200 with an alert,
never a 500.
"""

import json

from flask import make_response, render_template
from flask_login import login_required

from sam.core.organizations import Institution, Organization
from sam.core.users import User
from sam.integration.directory import DirectoryUnavailable
from sam.integration.directory.config import DirectoryConfig
from sam.manage import management_transaction
from sam.manage.directory import (reconcile_institution, reconcile_organization,
                                  reconcile_person)
from webapp.api.ldapsync.sync import restore_hook
from webapp.extensions import db
from webapp.utils.directory_panel import (institution_panel, organization_panel,
                                          person_panel)
from webapp.utils.htmx import htmx_not_found
from webapp.utils.rbac import (Permission, require_permission,
                               require_permission_any_facility)

from .blueprint import bp

USER_PANEL = 'dashboards/admin/fragments/user_directory_panel.html'
ORG_PANEL = 'dashboards/admin/fragments/org_directory_panel.html'
INST_PANEL = 'dashboards/admin/fragments/inst_directory_panel.html'


def _reader():
    from sam.integration.directory.registry import reader_from_environment
    return reader_from_environment()


@bp.route('/htmx/user/<username>/directory', methods=['GET'])
@login_required
@require_permission_any_facility(Permission.VIEW_USERS)
def htmx_user_directory(username):
    sam_user = User.get_by_username(db.session, username)
    if sam_user is None:
        return htmx_not_found('User')
    return render_template(USER_PANEL, sam_user=sam_user, panel=person_panel(db.session, sam_user))


@bp.route('/htmx/user/<username>/directory/apply', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_USERS)
def htmx_user_directory_apply(username):
    sam_user = User.get_by_username(db.session, username)
    if sam_user is None:
        return htmx_not_found('User')
    config = DirectoryConfig.from_environment()
    reader = _reader()
    try:
        record = reader.person(sam_user.upid) if reader.configured and sam_user.upid else None
    except DirectoryUnavailable:
        return render_template(USER_PANEL, sam_user=sam_user, panel=person_panel(db.session, sam_user))
    result = None
    if record is not None:
        with management_transaction(db.session):
            result = reconcile_person(db.session, sam_user, record, config=config,
                                      on_reactivate=restore_hook())
        db.session.refresh(sam_user)
    panel = person_panel(db.session, sam_user, record=record, applied=result)
    return render_template(USER_PANEL, sam_user=sam_user, panel=panel)


@bp.route('/htmx/organization/<int:org_id>/directory', methods=['GET'])
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def htmx_organization_directory(org_id):
    org = db.session.get(Organization, org_id)
    if org is None:
        return htmx_not_found('Organization')
    return render_template(ORG_PANEL, org=org, panel=organization_panel(db.session, org))


@bp.route('/htmx/organization/<int:org_id>/directory/apply', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_organization_directory_apply(org_id):
    org = db.session.get(Organization, org_id)
    if org is None:
        return htmx_not_found('Organization')
    config = DirectoryConfig.from_environment()
    reader = _reader()
    try:
        record = reader.organization(org_id) if reader.configured else None
    except DirectoryUnavailable:
        return render_template(ORG_PANEL, org=org, panel=organization_panel(db.session, org))
    result = None
    if record is not None:
        with management_transaction(db.session):
            result = reconcile_organization(db.session, org, record, config=config)
        db.session.refresh(org)
    panel = organization_panel(db.session, org, record=record, applied=result)
    resp = make_response(render_template(ORG_PANEL, org=org, panel=panel))
    if result is not None and result.applied:
        resp.headers['HX-Trigger'] = json.dumps({'reloadOrganizationsCard': {}})
    return resp


@bp.route('/htmx/institution/<int:inst_id>/directory', methods=['GET'])
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def htmx_institution_directory(inst_id):
    inst = db.session.get(Institution, inst_id)
    if inst is None:
        return htmx_not_found('Institution')
    return render_template(INST_PANEL, inst=inst, panel=institution_panel(db.session, inst))


@bp.route('/htmx/institution/<int:inst_id>/directory/apply', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_institution_directory_apply(inst_id):
    inst = db.session.get(Institution, inst_id)
    if inst is None:
        return htmx_not_found('Institution')
    config = DirectoryConfig.from_environment()
    reader = _reader()
    try:
        record = reader.institution(inst_id) if reader.configured else None
    except DirectoryUnavailable:
        return render_template(INST_PANEL, inst=inst, panel=institution_panel(db.session, inst))
    result = None
    if record is not None:
        with management_transaction(db.session):
            result = reconcile_institution(db.session, inst, record, config=config)
        db.session.refresh(inst)
    panel = institution_panel(db.session, inst, record=record, applied=result)
    resp = make_response(render_template(INST_PANEL, inst=inst, panel=panel))
    if result is not None and result.applied:
        resp.headers['HX-Trigger'] = json.dumps({'reloadOrganizationsCard': {}})
    return resp
