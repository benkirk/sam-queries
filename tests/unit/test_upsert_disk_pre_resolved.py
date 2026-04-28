"""Tests for the pre-resolved user/account override path on upsert_disk_charge_summary.

The override exists so the disk-charging gap-row path can write a
synthetic row with ``act_username='<unidentified>'`` while the FK side
points at the project lead — no row in the ``users`` table needs the
literal '<unidentified>' string.
"""

from datetime import date

import pytest

from sam.manage.summaries import upsert_disk_charge_summary
from sam.resources.resources import ResourceType
from factories.core import make_user
from factories.projects import make_account, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_seq


def _build_disk_graph(session):
    """Tiny disk graph: User → Project (lead=User) → Account → DISK Resource."""
    user = make_user(session)
    project = make_project(session, lead=user)
    # Reuse the 'DISK' ResourceType (canonical name) — production code
    # routes by exact resource_type string.
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    resource = make_resource(session, resource_type=rt,
                             resource_name=next_seq('DRES'))
    make_account(session, project=project, resource=resource)
    return user, project, resource


class TestPreResolvedOverride:

    def test_act_username_is_audit_label_user_is_lead(self, session):
        """Synthetic gap row: act_username carries the label literally,
        but user_id resolves to the project lead."""
        lead, project, resource = _build_disk_graph(session)
        # Account already created by _build_disk_graph; resolve it.
        from sam.accounting.accounts import Account
        account = Account.get_by_project_and_resource(
            session, project.project_id, resource.resource_id
        )
        record, action = upsert_disk_charge_summary(
            session,
            activity_date=date(2098, 6, 15),
            act_username='<unidentified>',
            act_projcode=None,
            act_unix_uid=None,
            resource_name=resource.resource_name,
            charges=1.0,
            number_of_files=0,
            bytes=1024 ** 4,
            terabyte_years=1.0,
            user=lead,
            account=account,
        )
        assert action == 'created'
        # The audit column carries the label verbatim.
        assert record.act_username == '<unidentified>'
        # The FK resolves to the project lead — no synthetic user row.
        assert record.user_id == lead.user_id
        assert record.username == lead.username
        # account_id is the resolved account.
        assert record.account_id == account.account_id

    def test_pre_resolved_user_skips_resolver(self, session):
        """If a user is supplied, _resolve_user is bypassed — the audit
        label can be ANY string (including one that doesn't exist in users)."""
        lead, project, resource = _build_disk_graph(session)
        from sam.accounting.accounts import Account
        account = Account.get_by_project_and_resource(
            session, project.project_id, resource.resource_id
        )

        # This act_username deliberately does NOT exist in users — and
        # we don't pass a unix_uid — so without the override the resolver
        # would raise. The override should make this succeed.
        # (DB column is varchar(35); use a short bogus literal.)
        bogus = 'no_such_user_xyz'
        record, _ = upsert_disk_charge_summary(
            session,
            activity_date=date(2098, 6, 16),
            act_username=bogus,
            act_projcode=None,
            act_unix_uid=None,
            resource_name=resource.resource_name,
            charges=0.5,
            number_of_files=0,
            bytes=512,
            terabyte_years=0.0,
            user=lead,
            account=account,
        )
        assert record.act_username == bogus
        assert record.user_id == lead.user_id

    def test_no_override_falls_back_to_resolver(self, session):
        """Without the override, the standard resolver path still works."""
        user, project, resource = _build_disk_graph(session)
        record, action = upsert_disk_charge_summary(
            session,
            activity_date=date(2098, 6, 17),
            act_username=user.username,
            act_projcode=project.projcode,
            act_unix_uid=user.unix_uid,
            resource_name=resource.resource_name,
            charges=2.0,
            number_of_files=1,
            bytes=2048,
            terabyte_years=0.0,
        )
        assert action == 'created'
        assert record.user_id == user.user_id
