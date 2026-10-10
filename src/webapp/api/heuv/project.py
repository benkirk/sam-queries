"""``search/projcode`` and ``project/{projcode}/hierarchy``."""

from flask import request

from sam.queries import heuv as q
from sam.schemas import heuv as h
from webapp.extensions import db

from . import bp, dump, found_or_400, heuv_api_required, HeuvBadRequest


def active_project_or_400(projcode: str, prefix: str):
    project = found_or_400(q.find_project(db.session, projcode), prefix, f'Project {projcode} does not exist.')
    if not project.active:
        raise HeuvBadRequest(prefix, f'Project {projcode} is not active.')
    return project


@bp.route('/search/projcode', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def search_matching_projcodes():
    rows = q.search_projcodes(db.session, request.args.get('fragment'), request.args.get('username'))
    return dump(h.ProjcodeSearchSchema(many=True), rows)


@bp.route('/project/<projcode>/hierarchy', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_hierarchy_of_project(projcode):
    project = active_project_or_400(projcode, 'getHierarchyOfProject.projcode')
    return dump(h.ProjectHierarchySchema(), q.project_hierarchy(project))
