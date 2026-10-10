"""``report/project/{projcode}`` and ``report/usage/project/{projcode}``."""

from flask import request

from sam.queries import heuv as q
from sam.schemas import heuv as h
from webapp.extensions import db

from . import bp, dump, found_or_400, heuv_api_required, HeuvBadRequest
from .project import active_project_or_400


@bp.route('/report/project/<projcode>', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_report_project(projcode):
    project = found_or_400(q.find_project(db.session, projcode), 'getReportProject.projcode',
                           f'Project {projcode} does not exist.')
    return dump(h.ReportProjectSchema(), q.report_project(project))


@bp.route('/report/usage/project/<projcode>', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_project_usage_report(projcode):
    project = active_project_or_400(projcode, 'getProjectUsageReport.projcode')
    if 'reportDate' in request.args:
        raise HeuvBadRequest('getProjectUsageReport.reportDate', 'reportDate is not supported.')
    return dump(h.ProjectUsageReportSchema(), q.project_usage_report(db.session, project))
