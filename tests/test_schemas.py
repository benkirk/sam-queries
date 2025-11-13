"""
Test marshmallow-sqlalchemy schemas for API serialization.

Tests:
- Schema serialization (dump) for User and Project models
- Three-tier schema strategy (Full, List, Summary)
- Nested relationships
- Method fields (@property serialization)
- DateTime handling
"""

import pytest
from datetime import datetime
from sam.core.users import User
from sam.projects.projects import Project
from webui.schemas import (
    UserSchema, UserListSchema, UserSummarySchema,
    ProjectSchema, ProjectListSchema, ProjectSummarySchema
)


class TestUserSchemas:
    """Test User schema serialization."""

    def test_user_summary_schema(self, session):
        """Test UserSummarySchema serializes minimal fields."""
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        # Serialize with UserSummarySchema
        result = UserSummarySchema().dump(user)

        # Should only have minimal fields
        assert 'user_id' in result
        assert 'username' in result
        assert result['username'] == 'benkirk'
        assert 'full_name' in result
        assert 'email' in result

        # Should NOT have detailed fields
        assert 'institutions' not in result
        assert 'organizations' not in result
        assert 'roles' not in result
        assert 'first_name' not in result

    def test_user_list_schema(self, session):
        """Test UserListSchema serializes list fields."""
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        # Serialize with UserListSchema
        result = UserListSchema().dump(user)

        # Should have key fields
        assert result['username'] == 'benkirk'
        assert 'full_name' in result
        assert 'display_name' in result
        assert 'email' in result
        assert 'active' in result
        assert 'locked' in result
        assert 'charging_exempt' in result

        # Should NOT have nested relationships
        assert 'institutions' not in result
        assert 'organizations' not in result
        assert 'roles' not in result

    def test_user_schema_full(self, session):
        """Test UserSchema serializes full details with relationships."""
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        # Serialize with UserSchema
        result = UserSchema().dump(user)

        # Should have all fields
        assert result['username'] == 'benkirk'
        assert 'full_name' in result
        assert 'first_name' in result
        assert 'middle_name' in result
        assert 'last_name' in result
        assert 'email' in result
        assert 'active' in result
        assert 'charging_exempt' in result

        # Should have nested relationships
        assert 'institutions' in result
        assert isinstance(result['institutions'], list)
        assert 'organizations' in result
        assert isinstance(result['organizations'], list)
        assert 'roles' in result
        assert isinstance(result['roles'], list)

        # Should have timestamps
        assert 'creation_time' in result
        assert 'modified_time' in result

    def test_user_list_schema_many(self, session):
        """Test UserListSchema with many=True."""
        users = session.query(User).filter(User.active == True).limit(5).all()
        assert len(users) > 0

        # Serialize multiple users
        result = UserListSchema(many=True).dump(users)

        assert isinstance(result, list)
        assert len(result) == len(users)
        assert all('username' in u for u in result)
        assert all('email' in u for u in result)

    def test_user_schema_property_fields(self, session):
        """Test that @property fields are serialized correctly."""
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        result = UserSchema().dump(user)

        # These come from @property methods
        assert result['full_name'] == user.full_name
        assert result['display_name'] == user.display_name
        assert result['email'] == user.primary_email


class TestProjectSchemas:
    """Test Project schema serialization."""

    def test_project_summary_schema(self, session):
        """Test ProjectSummarySchema serializes minimal fields."""
        project = Project.get_by_projcode(session, 'SCSG0001')
        assert project is not None

        # Serialize with ProjectSummarySchema
        result = ProjectSummarySchema().dump(project)

        # Should only have minimal fields
        assert 'project_id' in result
        assert 'projcode' in result
        assert result['projcode'] == 'SCSG0001'
        assert 'title' in result
        assert 'active' in result

        # Should NOT have detailed fields
        assert 'abstract' not in result
        assert 'lead' not in result
        assert 'admin' not in result

    def test_project_list_schema(self, session):
        """Test ProjectListSchema serializes list fields."""
        project = Project.get_by_projcode(session, 'SCSG0001')
        assert project is not None

        # Serialize with ProjectListSchema
        result = ProjectListSchema().dump(project)

        # Should have key fields
        assert result['projcode'] == 'SCSG0001'
        assert 'title' in result
        assert 'active' in result
        assert 'charging_exempt' in result
        assert 'area_of_interest' in result

        # Should have lead/admin as simple strings (not nested objects)
        assert 'lead_username' in result
        assert isinstance(result['lead_username'], (str, type(None)))
        assert 'admin_username' in result

        # Should NOT have full nested user objects
        assert 'lead' not in result or not isinstance(result.get('lead'), dict)

    def test_project_schema_full(self, session):
        """Test ProjectSchema serializes full details with relationships."""
        project = Project.get_by_projcode(session, 'SCSG0001')
        assert project is not None

        # Serialize with ProjectSchema
        result = ProjectSchema().dump(project)

        # Should have all fields
        assert result['projcode'] == 'SCSG0001'
        assert 'title' in result
        assert 'abstract' in result
        assert 'active' in result
        assert 'charging_exempt' in result
        assert 'area_of_interest' in result

        # Should have nested user objects (lead/admin)
        if project.lead:
            assert 'lead' in result
            assert isinstance(result['lead'], dict)
            assert 'username' in result['lead']
            assert 'full_name' in result['lead']
            assert 'email' in result['lead']

        if project.admin:
            assert 'admin' in result
            assert isinstance(result['admin'], dict)

        # Should have timestamps
        assert 'creation_time' in result
        assert 'modified_time' in result

    def test_project_list_schema_many(self, session):
        """Test ProjectListSchema with many=True."""
        projects = session.query(Project).filter(Project.active == True).limit(5).all()
        assert len(projects) > 0

        # Serialize multiple projects
        result = ProjectListSchema(many=True).dump(projects)

        assert isinstance(result, list)
        assert len(result) == len(projects)
        assert all('projcode' in p for p in result)
        assert all('title' in p for p in result)

    def test_project_schema_nested_users(self, session):
        """Test that nested user objects are serialized correctly."""
        project = Project.get_by_projcode(session, 'SCSG0001')
        assert project is not None
        assert project.lead is not None

        result = ProjectSchema().dump(project)

        # Lead should be nested UserSummarySchema
        assert 'lead' in result
        lead = result['lead']
        assert 'username' in lead
        assert 'full_name' in lead
        assert 'email' in lead
        # Should NOT have full user details (it's a summary)
        assert 'institutions' not in lead


class TestSchemaDatetimeHandling:
    """Test that datetime fields are serialized correctly."""

    def test_user_datetime_fields(self, session):
        """Test User datetime serialization."""
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        result = UserSchema().dump(user)

        # Datetime fields should be ISO format strings
        if result.get('creation_time'):
            assert isinstance(result['creation_time'], str)
            # Should be parseable as datetime
            datetime.fromisoformat(result['creation_time'])

        if result.get('modified_time'):
            assert isinstance(result['modified_time'], str)
            datetime.fromisoformat(result['modified_time'])

    def test_project_datetime_fields(self, session):
        """Test Project datetime serialization."""
        project = Project.get_by_projcode(session, 'SCSG0001')
        assert project is not None

        result = ProjectSchema().dump(project)

        # Datetime fields should be ISO format strings
        if result.get('creation_time'):
            assert isinstance(result['creation_time'], str)
            datetime.fromisoformat(result['creation_time'])

        if result.get('modified_time'):
            assert isinstance(result['modified_time'], str)
            datetime.fromisoformat(result['modified_time'])


class TestSchemaEdgeCases:
    """Test schema handling of edge cases and None values."""

    def test_user_with_no_email(self, session):
        """Test serialization of user with no email."""
        # Find a user without email or test with benkirk (should have email)
        user = User.get_by_username(session, 'benkirk')
        assert user is not None

        result = UserSchema().dump(user)
        # email can be None
        assert 'email' in result

    def test_project_with_no_admin(self, session):
        """Test serialization of project without admin."""
        # Find a project without admin
        projects = session.query(Project).filter(
            Project.active == True,
            Project.project_admin_user_id.is_(None)
        ).limit(1).all()

        if projects:
            project = projects[0]
            result = ProjectSchema().dump(project)
            # admin should be None
            assert result.get('admin') is None

    def test_empty_list_serialization(self, session):
        """Test that empty lists serialize correctly."""
        # Try to serialize empty list
        result = UserListSchema(many=True).dump([])
        assert result == []

        result = ProjectListSchema(many=True).dump([])
        assert result == []
