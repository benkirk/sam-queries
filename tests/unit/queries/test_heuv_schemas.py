"""HEUV output schemas render legacy's bytes: key order, nulls, date formats (plan Appendix A)."""

from datetime import datetime

from sam.schemas import heuv as h
from webapp.api.helpers import compact_json


def dump(schema, obj, many=False):
    return compact_json(schema(many=many).dump(obj))


def test_user_group_row():
    row = dict(username='benkirk', group_name='ncar', unix_gid=1000, primary=True, project=False, projcode=None)
    assert dump(h.UserGroupSchema, [row], many=True) == (
        '[{"username":"benkirk","groupName":"ncar","unixGid":1000,"primary":true,"project":false,"projcode":null}]')


def test_assigned_project_dates_render_in_utc():
    obj = dict(projcode='SCSG0001', primary=False, title='CSG systems project', resource_assignments=[
        dict(resource_name='Casper', start_date=datetime(2026, 6, 15, 11, 22, 32), end_date=None),     # MDT
        dict(resource_name='Cheyenne', start_date=datetime(2022, 1, 19, 11, 45, 5),                    # MST
             end_date=datetime(2023, 12, 30, 23, 59, 59)),
    ])
    assert dump(h.AssignedProjectSchema, obj) == (
        '{"projcode":"SCSG0001","primary":false,"title":"CSG systems project","resourceAssignments":['
        '{"resourceName":"Casper","startDate":"2026-06-15 17:22:32","endDate":null},'
        '{"resourceName":"Cheyenne","startDate":"2022-01-19 18:45:05","endDate":"2023-12-31 06:59:59"}]}')


def test_wallclock_nesting_and_ymd():
    obj = dict(username='benkirk', resources=[dict(resource_name='Derecho', queues=[dict(queue_name='main', exemptions=[
        dict(active=False, start_date=datetime(2024, 9, 4), end_date=datetime(2025, 9, 4, 23, 59, 59),
             hour_limit=48, comment='')])])])
    assert dump(h.WallclockExemptionsSchema, obj) == (
        '{"username":"benkirk","resources":[{"resourceName":"Derecho","queues":[{"queueName":"main","exemptions":['
        '{"active":false,"startDate":"2024-09-04","endDate":"2025-09-04","hourLimit":48,"comment":""}]}]}]}')


def test_usage_report_variants_keep_their_own_key_order():
    head = dict(resource_type='DISK', status='Normal', allocation_start_date=datetime(2026, 10, 1),
                allocation_end_date=datetime(2027, 9, 30, 23, 59, 59), allocation_propagated=False,
                allocation_amount=23, usernames=['benkirk', 'csgteam'])
    disk = dict(head, resource_name='Campaign_Store', resource_usage_type=h.DATA_HOLDINGS,
                total_holdings=7, number_of_files=547755, balance=16, user_count=2)
    expired = dict(resource_name='Laramie', resource_type='HPC', resource_usage_type=h.ACCRUED_CHARGES,
                   status='Expired', allocation_start_date=None, allocation_end_date=None,
                   allocation_propagated=False, allocation_amount=None, usernames=['benkirk'],
                   total_charges=0, adjustments=0, balance=None, threshold_reports=[],
                   threshold_limited=False, user_count=1)
    report = dict(projcode='SCSG0001', report_date=datetime(2026, 10, 10), account_reports=[disk, expired])
    assert dump(h.ProjectUsageReportSchema, report) == (
        '{"projcode":"SCSG0001","reportDate":"2026-10-10","accountReports":['
        '{"resourceName":"Campaign_Store","resourceType":"DISK","resourceUsageType":"DataHoldings","status":"Normal",'
        '"allocationStartDate":"2026-10-01","allocationEndDate":"2027-09-30","allocationPropagated":false,'
        '"allocationAmount":23,"usernames":["benkirk","csgteam"],"totalHoldings":7,"numberOfFiles":547755,'
        '"balance":16,"userCount":2},'
        '{"resourceName":"Laramie","resourceType":"HPC","resourceUsageType":"AccruedCharges","status":"Expired",'
        '"allocationStartDate":null,"allocationEndDate":null,"allocationPropagated":false,"allocationAmount":null,'
        '"usernames":["benkirk"],"totalCharges":0,"adjustments":0,"balance":null,"thresholdReports":[],'
        '"thresholdLimited":false,"userCount":1}]}')


def test_threshold_report_item():
    item = dict(period=30, percent_limit=None, allocation_amount=2060440, charges=3147, percent_usage=0, label='30-Day')
    assert dump(h.ThresholdReportSchema, item) == (
        '{"period":30,"percentLimit":null,"allocationAmount":2060440,"charges":3147,"percentUsage":0,"label":"30-Day"}')


def test_hierarchy_recurses():
    leaf = dict(projcode='NCIS0014', parent_projcode='NCIS0001', root_projcode='NCIS0001', children=[])
    root = dict(projcode='NCIS0001', parent_projcode=None, root_projcode='NCIS0001', children=[leaf])
    assert dump(h.ProjectHierarchySchema, root) == (
        '{"projcode":"NCIS0001","parentProjcode":null,"rootProjcode":"NCIS0001","children":['
        '{"projcode":"NCIS0014","parentProjcode":"NCIS0001","rootProjcode":"NCIS0001","children":[]}]}')


def test_report_project_and_access_shapes():
    rp = dict(projcode='P', title='T', lead_username='a', lead_name='A B', admin_username=None, admin_name=None,
              abstract_text='x\r\ny', hierarchical=False, accounts=[dict(resource_name='Derecho', threshold_limited=False,
              allocations=[dict(start_date=datetime(2026, 10, 1), end_date=datetime(2027, 9, 30, 23, 59, 59), active=True)])])
    assert dump(h.ReportProjectSchema, rp) == (
        '{"projcode":"P","title":"T","leadUsername":"a","leadName":"A B","adminUsername":null,"adminName":null,'
        '"abstractText":"x\\r\\ny","hierarchical":false,"accounts":[{"resourceName":"Derecho","thresholdLimited":false,'
        '"allocations":[{"startDate":"2026-10-01","endDate":"2027-09-30","active":true}]}]}')
    res = dict(resource_name='hpc', resource_type='HPC', login=True, shells=[dict(shell_name='bash', dflt=True)])
    assert dump(h.AccessibleResourceSchema, res) == (
        '{"resourceName":"hpc","resourceType":"HPC","login":true,"shells":[{"shellName":"bash","dflt":true}]}')
