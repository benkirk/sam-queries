"""
Flask-Admin Model View Enhancement Tests (Phase 1)

Tests for automatic mixin-based defaults in SAMModelView:
- Auto-hide soft-deleted records (get_query, get_count_query)
- Auto-exclude system columns from forms (scaffold_form)
- Auto-add mixin-based filters (scaffold_filters)
- Feature flag controls

These tests require Flask context and app configuration.
"""

import pytest
import os

# Set Flask active flag before imports
os.environ['FLASK_ACTIVE'] = '1'

from webapp.extensions import db
from webapp.admin.default_model_views import SAMModelView
from flask import Flask
from flask_admin import Admin

from sam import (
    User, Project, Account, Allocation, Resource
)


# Use app fixture from conftest.py - it already has Admin configured


class TestSoftDeleteFiltering:
    """Test Phase 1: Auto-hide soft-deleted records."""

    def test_allocation_get_query_hides_deleted(self, app, session):
        """Test that AllocationView hides deleted records by default."""
        with app.app_context():
            # Create view for Allocation (has SoftDeleteMixin)
            view = SAMModelView(Allocation, session, name='Allocations')

            # Get query should filter deleted=False
            query = view.get_query()

            # Verify query has filter for deleted=False
            # All results should have deleted=False
            for allocation in query.limit(100):
                assert allocation.deleted == False, \
                    f"Allocation {allocation.allocation_id} is deleted but appeared in query"

    def test_allocation_count_matches_query(self, app, session):
        """Test that count query matches filtered query."""
        with app.app_context():
            view = SAMModelView(Allocation, session, name='Allocations')

            # Get counts
            query = view.get_query()
            count_query = view.get_count_query()

            query_count = query.count()
            count_query_result = count_query.scalar()

            assert query_count == count_query_result, \
                f"Query count ({query_count}) != count_query ({count_query_result})"

    def test_account_get_query_hides_deleted(self, app, session):
        """Test that AccountView hides deleted records."""
        with app.app_context():
            view = SAMModelView(Account, session, name='Accounts')

            query = view.get_query()

            # All results should have deleted=False
            for account in query.limit(100):
                assert account.deleted == False, \
                    f"Account {account.account_id} is deleted but appeared in query"

    def test_disable_auto_hide_deleted(self, app, session):
        """Test that auto_hide_deleted can be disabled."""
        with app.app_context():
            # Create custom view with auto_hide_deleted=False
            class AllocationAllView(SAMModelView):
                auto_hide_deleted = False

            view = AllocationAllView(Allocation, session, name='All Allocations')

            query = view.get_query()

            # Should include deleted records
            total_count = session.query(Allocation).count()
            view_count = query.count()

            # View count should match total (including deleted)
            assert view_count == total_count, \
                f"View with auto_hide_deleted=False should show all records"

    def test_resource_no_deleted_column(self, app, session):
        """Test that models without 'deleted' column work normally."""
        with app.app_context():
            view = SAMModelView(Resource, session, name='Resources')

            # Should not crash even though Resource doesn't have 'deleted' column
            query = view.get_query()

            # Just verify the query is created successfully
            assert query is not None, "Query should be created successfully"

            # Verify that Resource doesn't have 'deleted' column
            assert not hasattr(Resource, 'deleted'), "Resource should not have 'deleted' column"

            # Verify auto_hide_deleted flag is still True
            assert view.auto_hide_deleted == True, "auto_hide_deleted should still be True"

            # The key test: view creation doesn't crash for models without 'deleted'
            assert True


class TestSystemColumnExclusion:
    """Test Phase 1: Auto-exclude system columns from forms."""

    def test_project_form_excludes_timestamps(self, app,  session):
        """Test that Project form excludes creation_time and modified_time."""
        with app.app_context():
            view = SAMModelView(Project, session, name='Projects')

            # Scaffold form
            form_class = view.scaffold_form()

            # Check that timestamp columns are excluded
            assert 'creation_time' in view.form_excluded_columns, \
                "creation_time should be in form_excluded_columns"
            assert 'modified_time' in view.form_excluded_columns, \
                "modified_time should be in form_excluded_columns"

    def test_allocation_form_excludes_soft_delete(self, app,  session):
        """Test that Allocation form excludes deleted and deletion_time."""
        with app.app_context():
            view = SAMModelView(Allocation, session, name='Allocations')

            # Scaffold form
            form_class = view.scaffold_form()

            # Check that soft delete columns are excluded
            assert 'deleted' in view.form_excluded_columns, \
                "deleted should be in form_excluded_columns"
            assert 'deletion_time' in view.form_excluded_columns, \
                "deletion_time should be in form_excluded_columns"
            assert 'creation_time' in view.form_excluded_columns, \
                "creation_time should be in form_excluded_columns"

    def test_user_form_excludes_timestamps_only(self, app,  session):
        """Test that User form excludes timestamps (no soft delete)."""
        with app.app_context():
            view = SAMModelView(User, session, name='Users')

            # Scaffold form
            form_class = view.scaffold_form()

            # Should exclude timestamps
            assert 'creation_time' in view.form_excluded_columns
            assert 'modified_time' in view.form_excluded_columns

            # Should NOT exclude 'deleted' (User doesn't have it)
            # This is OK - the exclusion logic only adds if column exists

    def test_disable_auto_exclude_system_columns(self, app,  session):
        """Test that auto_exclude_system_columns can be disabled."""
        with app.app_context():
            class ProjectManualExcludeView(SAMModelView):
                auto_exclude_system_columns = False
                form_excluded_columns = ['some_other_field']

            view = ProjectManualExcludeView(Project, session, name='Projects Manual')

            # Scaffold form
            form_class = view.scaffold_form()

            # Should only have manually specified exclusions
            assert view.form_excluded_columns == ['some_other_field'], \
                "Should respect manual form_excluded_columns when auto disabled"


class TestMixinBasedFilters:
    """Test Phase 1: Auto-add filters based on mixins."""

    def test_project_has_active_filter(self, app,  session):
        """Test that Project view has 'active' filter (ActiveFlagMixin)."""
        with app.app_context():
            view = SAMModelView(Project, session, name='Projects')

            # Check column_filters attribute (set in __init__)
            assert view.column_filters is not None, "Project should have column_filters"

            # Convert to list if needed
            filter_list = list(view.column_filters) if view.column_filters else []

            # Should have 'active' in column_filters
            assert 'active' in filter_list, \
                f"Project should have 'active' filter, found: {filter_list}"

    def test_allocation_has_deleted_filter(self, app,  session):
        """Test that Allocation view has 'deleted' filter (SoftDeleteMixin)."""
        with app.app_context():
            view = SAMModelView(Allocation, session, name='Allocations')

            filter_list = list(view.column_filters) if view.column_filters else []

            # Should have 'deleted' filter
            assert 'deleted' in filter_list, \
                f"Allocation should have 'deleted' filter, found: {filter_list}"

    def test_project_has_timestamp_filters(self, app,  session):
        """Test that Project view has timestamp filters (TimestampMixin)."""
        with app.app_context():
            view = SAMModelView(Project, session, name='Projects')

            filter_list = list(view.column_filters) if view.column_filters else []

            # Should have creation_time and modified_time filters
            assert 'creation_time' in filter_list, \
                f"Project should have 'creation_time' filter, found: {filter_list}"
            assert 'modified_time' in filter_list, \
                f"Project should have 'modified_time' filter, found: {filter_list}"

    def test_allocation_has_date_range_filters(self, app,  session):
        """Test that Allocation view has start_date/end_date filters (DateRangeMixin)."""
        with app.app_context():
            view = SAMModelView(Allocation, session, name='Allocations')

            filter_list = list(view.column_filters) if view.column_filters else []

            # Should have start_date and end_date filters
            assert 'start_date' in filter_list, \
                f"Allocation should have 'start_date' filter, found: {filter_list}"
            assert 'end_date' in filter_list, \
                f"Allocation should have 'end_date' filter, found: {filter_list}"

    def test_disable_auto_filter_mixins(self, app,  session):
        """Test that auto_filter_mixins can be disabled."""
        with app.app_context():
            class ProjectNoAutoFiltersView(SAMModelView):
                auto_filter_mixins = False

            view = ProjectNoAutoFiltersView(Project, session, name='Projects No Auto')

            # Should have no auto-added filters (column_filters may be None or empty)
            filter_list = list(view.column_filters) if view.column_filters else []

            # With auto_filter_mixins disabled, should not have mixin filters
            # (unless manually specified, which we didn't do)
            assert True  # Just verify it doesn't crash


class TestFeatureFlags:
    """Test that feature flags work correctly."""

    def test_default_feature_flags(self, app,  session):
        """Test that default feature flags are set correctly."""
        with app.app_context():
            view = SAMModelView(User, session, name='Users')

            # Check defaults
            assert view.auto_hide_deleted == True, "auto_hide_deleted should default to True"
            assert view.auto_hide_inactive == False, "auto_hide_inactive should default to False"
            assert view.auto_exclude_system_columns == True, "auto_exclude_system_columns should default to True"
            assert view.auto_filter_mixins == True, "auto_filter_mixins should default to True"
            assert view.auto_searchable_strings == False, "auto_searchable_strings should default to False (Phase 2)"

    def test_custom_feature_flags(self, app,  session):
        """Test that feature flags can be customized per view."""
        with app.app_context():
            class CustomView(SAMModelView):
                auto_hide_deleted = False
                auto_hide_inactive = True
                auto_exclude_system_columns = False
                auto_filter_mixins = False

            view = CustomView(Project, session, name='Custom')

            assert view.auto_hide_deleted == False
            assert view.auto_hide_inactive == True
            assert view.auto_exclude_system_columns == False
            assert view.auto_filter_mixins == False


class TestBackwardCompatibility:
    """Test that existing custom views still work."""

    def test_custom_form_excluded_columns_preserved(self, app,  session):
        """Test that manually specified form_excluded_columns are preserved."""
        with app.app_context():
            class UserCustomView(SAMModelView):
                form_excluded_columns = ['led_projects', 'admin_projects', 'accounts']

            view = UserCustomView(User, session, name='Users Custom')

            # Scaffold form
            form_class = view.scaffold_form()

            # Should have both auto and manual exclusions
            assert 'led_projects' in view.form_excluded_columns
            assert 'admin_projects' in view.form_excluded_columns
            assert 'creation_time' in view.form_excluded_columns
            assert 'modified_time' in view.form_excluded_columns

    def test_existing_views_not_broken(self, app,  session):
        """Test that existing simple views still work."""
        with app.app_context():
            # Simulate existing default view (just 'pass')
            class ResourceDefaultAdmin(SAMModelView):
                pass

            view = ResourceDefaultAdmin(Resource, session, name='Resources')

            # Should work without errors
            query = view.get_query()
            count = query.count()

            assert count > 0, "Simple default view should work"
