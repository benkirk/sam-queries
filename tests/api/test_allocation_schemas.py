"""
Test Allocation, Account, Resource, and Charge schemas.

Tests the complex schemas with usage calculations that match sam_search.py output.
"""

import pytest
from datetime import datetime, timedelta
from sam.projects.projects import Project
from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.resources.resources import Resource
from webui.schemas import (
    ResourceSchema, ResourceSummarySchema, ResourceTypeSchema,
    AllocationSchema, AllocationWithUsageSchema,
    AccountSchema, AccountSummarySchema,
    CompChargeSummarySchema, DavChargeSummarySchema,
    DiskChargeSummarySchema, ArchiveChargeSummarySchema
)


class TestResourceSchemas:
    """Test Resource and ResourceType schemas."""

    def test_resource_type_schema(self, session):
        """Test ResourceTypeSchema serializes resource types."""
        from sam.resources.resources import ResourceType

        resource_type = session.query(ResourceType).filter(
            ResourceType.resource_type == 'HPC'
        ).first()

        if resource_type:
            result = ResourceTypeSchema().dump(resource_type)
            assert 'resource_type_id' in result
            assert result['resource_type'] == 'HPC'
            assert 'description' in result

    def test_resource_summary_schema(self, test_resource):
        """Test ResourceSummarySchema serializes minimal fields."""
        resource = test_resource

        if resource:
            result = ResourceSummarySchema().dump(resource)
            assert 'resource_id' in result
            assert 'resource_name' in result
            assert result['resource_name'] == 'Derecho'
            assert 'resource_type' in result
            # resource_type should be a string, not nested object
            assert isinstance(result['resource_type'], (str, type(None)))

    def test_resource_schema_full(self, test_resource):
        """Test ResourceSchema serializes full details."""
        resource = test_resource

        if resource:
            result = ResourceSchema().dump(resource)
            assert 'resource_id' in result
            assert 'resource_name' in result
            assert 'description' in result
            assert 'activity_type' in result
            assert 'charging_exempt' in result
            # resource_type should be a nested object in full schema
            if result.get('resource_type'):
                assert isinstance(result['resource_type'], dict)
                assert 'resource_type' in result['resource_type']


class TestAllocationSchemas:
    """Test Allocation schemas including usage calculations."""

    def test_allocation_schema_basic(self, session, test_project):
        """Test basic AllocationSchema without usage."""
        project = test_project
        assert project is not None

        # Get an account with allocations
        account = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).first()

        if account and account.allocations:
            alloc = account.allocations[0]
            result = AllocationSchema().dump(alloc)

            assert 'allocation_id' in result
            assert 'account_id' in result
            assert 'amount' in result
            assert 'start_date' in result
            assert 'end_date' in result
            assert 'is_active' in result
            assert 'deleted' in result

            # Should NOT have usage fields
            assert 'used' not in result
            assert 'remaining' not in result
            assert 'percent_used' not in result

    def test_allocation_with_usage_schema(self, session, test_project):
        """Test AllocationWithUsageSchema includes usage calculations."""
        project = test_project
        assert project is not None

        # Get an account with allocations
        account = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).first()

        if account and account.allocations and account.resource:
            # Find active allocation
            now = datetime.now()
            active_alloc = None
            for alloc in account.allocations:
                if alloc.is_active_at(now) and not alloc.deleted:
                    active_alloc = alloc
                    break

            if active_alloc:
                schema = AllocationWithUsageSchema()
                schema.context = {
                    'account': account,
                    'session': session,
                    'include_adjustments': True
                }
                result = schema.dump(active_alloc)

                # Should have all basic fields
                assert 'allocation_id' in result
                assert 'amount' in result

                # Should have usage fields
                assert 'used' in result
                assert 'remaining' in result
                assert 'percent_used' in result
                assert 'charges_by_type' in result
                assert 'adjustments' in result
                assert 'resource' in result

                # Validate calculations
                assert isinstance(result['used'], (int, float))
                assert isinstance(result['remaining'], (int, float))
                assert isinstance(result['percent_used'], (int, float))
                assert isinstance(result['charges_by_type'], dict)

                # Resource should be nested
                if result['resource']:
                    assert 'resource_name' in result['resource']

                # Percent used should be reasonable
                assert 0 <= result['percent_used'] <= 200  # Allow up to 200% for overages

    def test_allocation_usage_calculations(self, session, test_project):
        """Test that usage calculations match expected values."""
        project = test_project
        assert project is not None

        account = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).first()

        if account and account.allocations and account.resource:
            now = datetime.now()
            active_alloc = None
            for alloc in account.allocations:
                if alloc.is_active_at(now) and not alloc.deleted:
                    active_alloc = alloc
                    break

            if active_alloc:
                schema = AllocationWithUsageSchema()
                schema.context = {
                    'account': account,
                    'session': session,
                    'include_adjustments': True
                }
                result = schema.dump(active_alloc)

                # Validate: remaining = amount - used
                allocated = result['amount']
                used = result['used']
                remaining = result['remaining']

                assert abs((allocated - used) - remaining) < 0.01  # Allow for float precision

                # Validate: percent_used = (used / amount) * 100
                if allocated > 0:
                    expected_percent = (used / allocated) * 100
                    assert abs(result['percent_used'] - expected_percent) < 0.01


class TestAccountSchemas:
    """Test Account schemas."""

    def test_account_summary_schema(self, session, test_project):
        """Test AccountSummarySchema serializes minimal fields."""
        project = test_project
        assert project is not None

        account = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).first()

        if account:
            result = AccountSummarySchema().dump(account)
            assert 'account_id' in result
            assert 'project' in result
            assert 'resource' in result

            # Should have nested summary objects
            if result['project']:
                assert 'projcode' in result['project']
            if result['resource']:
                assert 'resource_name' in result['resource']

    def test_account_schema_with_allocation(self, session, test_project):
        """Test AccountSchema includes active allocation with usage."""
        project = test_project
        assert project is not None

        account = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).first()

        if account and account.resource:
            schema = AccountSchema()
            schema.context = {
                'session': session,
                'include_adjustments': True
            }
            result = schema.dump(account)

            assert 'account_id' in result
            assert 'project' in result
            assert 'resource' in result
            assert 'active_allocation' in result

            # If there's an active allocation, it should have usage details
            if result['active_allocation']:
                alloc = result['active_allocation']
                assert 'used' in alloc
                assert 'remaining' in alloc
                assert 'percent_used' in alloc


class TestChargeSummarySchemas:
    """Test charge summary schemas."""

    def test_comp_charge_summary_schema(self, session):
        """Test CompChargeSummarySchema serializes HPC charges."""
        from sam.summaries.comp_summaries import CompChargeSummary

        charges = session.query(CompChargeSummary).limit(5).all()
        if charges:
            result = CompChargeSummarySchema(many=True).dump(charges)
            assert isinstance(result, list)
            assert len(result) > 0

            for charge in result:
                assert 'charge_summary_id' in charge
                assert 'activity_date' in charge
                assert 'account_id' in charge
                assert 'charges' in charge

    def test_dav_charge_summary_schema(self, session):
        """Test DavChargeSummarySchema serializes DAV charges."""
        from sam.summaries.dav_summaries import DavChargeSummary

        charges = session.query(DavChargeSummary).limit(5).all()
        if charges:
            result = DavChargeSummarySchema(many=True).dump(charges)
            assert isinstance(result, list)
            for charge in result:
                assert 'dav_charge_summary_id' in charge
                assert 'activity_date' in charge

    def test_disk_charge_summary_schema(self, session):
        """Test DiskChargeSummarySchema serializes disk charges."""
        from sam.summaries.disk_summaries import DiskChargeSummary

        charges = session.query(DiskChargeSummary).limit(5).all()
        if charges:
            result = DiskChargeSummarySchema(many=True).dump(charges)
            assert isinstance(result, list)
            for charge in result:
                assert 'disk_charge_summary_id' in charge
                assert 'terabyte_years' in charge
                assert 'bytes' in charge

    def test_archive_charge_summary_schema(self, session):
        """Test ArchiveChargeSummarySchema serializes archive charges."""
        from sam.summaries.archive_summaries import ArchiveChargeSummary

        charges = session.query(ArchiveChargeSummary).limit(5).all()
        if charges:
            result = ArchiveChargeSummarySchema(many=True).dump(charges)
            assert isinstance(result, list)
            for charge in result:
                assert 'archive_charge_summary_id' in charge
                assert 'terabyte_years' in charge


class TestSchemaIntegration:
    """Test schema integration matching sam_search.py output."""

    def test_project_allocation_usage_matches_cli(self, session, test_project):
        """
        Test that AllocationWithUsageSchema output matches sam_search.py format.

        This validates that the API will return the same data structure as the CLI.
        """
        project = test_project
        assert project is not None

        # Get all accounts for project
        accounts = session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False
        ).all()

        allocations_with_usage = []
        now = datetime.now()

        for account in accounts:
            if not account.resource:
                continue

            for alloc in account.allocations:
                if alloc.is_active_at(now) and not alloc.deleted:
                    schema = AllocationWithUsageSchema()
                    schema.context = {
                        'account': account,
                        'session': session,
                        'include_adjustments': True
                    }
                    usage_data = schema.dump(alloc)
                    allocations_with_usage.append(usage_data)

        # Should have at least one allocation with usage
        if allocations_with_usage:
            # Each allocation should have the key fields from sam_search.py output
            for alloc in allocations_with_usage:
                # Matches sam_search.py verbose output structure
                assert 'allocation_id' in alloc
                assert 'amount' in alloc  # allocated
                assert 'used' in alloc
                assert 'remaining' in alloc
                assert 'percent_used' in alloc
                assert 'resource' in alloc
                assert 'charges_by_type' in alloc

                # Resource details
                if alloc['resource']:
                    assert 'resource_name' in alloc['resource']
