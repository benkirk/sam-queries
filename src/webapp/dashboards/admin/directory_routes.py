"""Admin dashboard: the Directory panels (docs/plans/DIRECTORY_CLIENT.md step 3, _NEXT.md section 3).

A panel is an operator-initiated read of one person, organization, institution or group
from the directory with SAM's drift beside it; Apply writes the kinds whose
``DIRECTORY_APPLY_<KIND>`` lever is on, inside ``management_transaction``. Groups are
report-only. The directory being down is a 200 with an alert, never a 500.
"""

import json

from flask import make_response, render_template
from flask_login import login_required

from sam.core.groups import AdhocGroup
from sam.core.organizations import Institution, Organization
from sam.core.users import User
from sam.integration.directory import DirectoryUnavailable
from sam.integration.directory.config import DirectoryConfig
from sam.manage import management_transaction
from sam.manage.directory import (reconcile_institution, reconcile_organization,
                                  reconcile_person)
from sam.manage.directory_panel import (adhoc_group_panel, directory_reader,
                                        institution_panel,
                                        organization_panel, person_panel,
                                        project_group_panel)
from webapp.api.access_control import require_project_facility_permission
from webapp.api.ldapsync.sync import restore_hook
from webapp.extensions import db
from webapp.utils.htmx import htmx_not_found
from webapp.utils.rbac import (Permission, require_permission,
                               require_permission_any_facility)

from .blueprint import bp

USER_PANEL = 'dashboards/admin/fragments/user_directory_panel.html'
ORG_PANEL = 'dashboards/admin/fragments/org_directory_panel.html'
INST_PANEL = 'dashboards/admin/fragments/inst_directory_panel.html'
GROUP_PANEL = 'dashboards/admin/fragments/group_directory_panel.html'


def _apply(template, obj, key, read, reconcile, context, *, trigger=None, **kwargs):
    """The Apply POSTs: read the record, reconcile inside ``management_transaction``, re-render
    the panel with the result; the directory being down re-renders the plain panel (200)."""
    config = DirectoryConfig.from_environment()
    reader = directory_reader()
    try:
        record = read(reader)(key) if reader.configured and key is not None else None
    except DirectoryUnavailable:
        return render_template(template, **context(None, None))
    result = None
    if record is not None:
        with management_transaction(db.session):
            result = reconcile(db.session, obj, record, config=config, **kwargs)
        db.session.refresh(obj)
    resp = make_response(render_template(template, **context(record, result)))
    if trigger and result is not None and result.applied:
        resp.headers['HX-Trigger'] = json.dumps({trigger: {}})
    return resp


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
    return _apply(USER_PANEL, sam_user, sam_user.upid, lambda r: r.person, reconcile_person,
                  lambda record, result: dict(sam_user=sam_user, panel=person_panel(
                      db.session, sam_user, record=record, applied=result)),
                  on_reactivate=restore_hook())


# Report-only, so no apply routes. Named 'group directory', not 'project directory': the
# project_directory_* family is the filesystem directories.
@bp.route('/htmx/project/<projcode>/directory', methods=['GET'])
@login_required
@require_project_facility_permission(Permission.VIEW_PROJECTS)
def htmx_project_group_directory(project):
    return render_template(GROUP_PANEL, panel=project_group_panel(db.session, project))


@bp.route('/htmx/group/<group_name>/directory', methods=['GET'])
@login_required
@require_permission_any_facility(Permission.VIEW_GROUPS)
def htmx_group_directory(group_name):
    group = AdhocGroup.get_by_name(db.session, group_name)
    if group is None:
        return htmx_not_found('Group')
    return render_template(GROUP_PANEL, panel=adhoc_group_panel(db.session, group))


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
    return _apply(ORG_PANEL, org, org_id, lambda r: r.organization, reconcile_organization,
                  lambda record, result: dict(org=org, panel=organization_panel(
                      db.session, org, record=record, applied=result)),
                  trigger='reloadOrganizationsCard')


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
    return _apply(INST_PANEL, inst, inst_id, lambda r: r.institution, reconcile_institution,
                  lambda record, result: dict(inst=inst, panel=institution_panel(
                      db.session, inst, record=record, applied=result)),
                  trigger='reloadOrganizationsCard')
