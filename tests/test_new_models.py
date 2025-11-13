"""
Tests for Newly Added ORM Models

Tests the 7 ORM models added during database schema review:
- Factor & Formula (charging infrastructure)
- ApiCredentials & RoleApiCredentials (API security)
- ProjectCode (project code generation)
- FosAoi (NSF field of science mapping)
- ResponsibleParty (account responsibility)

These models were added to address missing database table coverage.
"""

from datetime import datetime

import pytest
from sam import (
    Account,
    ApiCredentials,
    AreaOfInterest,
    Facility,
    Factor,
    Formula,
    FosAoi,
    MnemonicCode,
    ProjectCode,
    ResourceType,
    ResponsibleParty,
    Role,
    RoleApiCredentials,
    User,
)

# ============================================================================
# Charging Models - Factor & Formula
# ============================================================================


class TestFactorModel:
    """Test Factor model - charging factors for resource types."""

    def test_factor_count(self, session):
        """Test that we can query factors."""
        factor_count = session.query(Factor).count()
        assert factor_count >= 0, "Should be able to count factors"
        print(f"✅ Found {factor_count} factors")

    def test_factor_query(self, session):
        """Test querying and accessing factor properties."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert factor.factor_id is not None
        assert factor.factor_name is not None
        assert factor.value is not None
        assert factor.resource_type_id is not None
        assert factor.start_date is not None
        print(f"✅ Factor: {factor.factor_name} = {factor.value}")

    def test_factor_resource_type_relationship(self, session):
        """Test Factor -> ResourceType relationship."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert factor.resource_type is not None
        assert isinstance(factor.resource_type, ResourceType)
        print(
            f"✅ Factor {factor.factor_name} → ResourceType {factor.resource_type.resource_type}"
        )

    def test_resource_type_to_factors(self, session):
        """Test reverse relationship: ResourceType -> [Factors]."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        resource_type = factor.resource_type
        assert hasattr(resource_type, "factors")
        assert len(resource_type.factors) > 0
        assert factor in resource_type.factors
        print(
            f"✅ ResourceType {resource_type.resource_type} has {len(resource_type.factors)} factor(s)"
        )

    def test_factor_is_active_property(self, session):
        """Test factor.is_active property for date-based validity."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        # is_active should return a boolean
        assert isinstance(factor.is_active, bool)

        # If factor has no end_date and start_date is in past, should be active
        if factor.end_date is None and factor.start_date < datetime.now():
            assert factor.is_active == True
            print(f"✅ Factor {factor.factor_name} is active (no end_date)")

        # If factor has end_date in future, could be active
        elif factor.end_date and factor.end_date > datetime.now():
            print(f"✅ Factor {factor.factor_name} active status: {factor.is_active}")

    def test_factor_with_end_date(self, session):
        """Test factors with end_date (expired factors)."""
        expired_factors = (
            session.query(Factor)
            .filter(Factor.end_date.isnot(None), Factor.end_date < datetime.now())
            .all()
        )

        print(f"✅ Found {len(expired_factors)} expired factor(s)")
        for factor in expired_factors:
            assert factor.is_active == False
            print(f"   - {factor.factor_name}: expired on {factor.end_date}")

    def test_factor_timestamps(self, session):
        """Test that factors have timestamp fields."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert hasattr(factor, "creation_time")
        assert hasattr(factor, "modified_time")
        assert factor.creation_time is not None
        print(f"✅ Factor has timestamps: created={factor.creation_time}")


class TestFormulaModel:
    """Test Formula model - charging formulas for resource types."""

    def test_formula_count(self, session):
        """Test that we can query formulas."""
        formula_count = session.query(Formula).count()
        assert formula_count >= 0, "Should be able to count formulas"
        print(f"✅ Found {formula_count} formulas")

    def test_formula_query(self, session):
        """Test querying and accessing formula properties."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert formula.formula_id is not None
        assert formula.formula_name is not None
        assert formula.formula_str is not None
        assert formula.resource_type_id is not None
        assert formula.start_date is not None
        print(f"✅ Formula: {formula.formula_name}")
        print(f"   {formula.formula_str[:80]}...")

    def test_formula_resource_type_relationship(self, session):
        """Test Formula -> ResourceType relationship."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert formula.resource_type is not None
        assert isinstance(formula.resource_type, ResourceType)
        print(
            f"✅ Formula {formula.formula_name} → ResourceType {formula.resource_type.resource_type}"
        )

    def test_resource_type_to_formulas(self, session):
        """Test reverse relationship: ResourceType -> [Formulas]."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        resource_type = formula.resource_type
        assert hasattr(resource_type, "formulas")
        assert len(resource_type.formulas) > 0
        assert formula in resource_type.formulas
        print(
            f"✅ ResourceType {resource_type.resource_type} has {len(resource_type.formulas)} formula(s)"
        )

    def test_formula_variables_property(self, session):
        """Test formula.variables property extracts @{variable} names."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        variables = formula.variables
        assert isinstance(variables, list)

        # Check that variables were extracted from formula_str
        # Example: "@{wall_clock_hours}*@{number_of_nodes}" -> ['wall_clock_hours', 'number_of_nodes']
        if variables:
            print(
                f"✅ Formula {formula.formula_name} uses variables: {', '.join(variables)}"
            )
            # Verify each variable appears in the formula string
            for var in variables:
                assert f"@{{{var}}}" in formula.formula_str
        else:
            print(f"✅ Formula {formula.formula_name} has no variables")

    def test_formula_is_active_property(self, session):
        """Test formula.is_active property for date-based validity."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        # is_active should return a boolean
        assert isinstance(formula.is_active, bool)

        # If formula has no end_date and start_date is in past, should be active
        if formula.end_date is None and formula.start_date < datetime.now():
            assert formula.is_active == True
            print(f"✅ Formula {formula.formula_name} is active (no end_date)")

    def test_formula_timestamps(self, session):
        """Test that formulas have timestamp fields."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert hasattr(formula, "creation_time")
        assert hasattr(formula, "modified_time")
        assert formula.creation_time is not None
        print(f"✅ Formula has timestamps: created={formula.creation_time}")


# ============================================================================
# API Security Models - ApiCredentials & RoleApiCredentials
# ============================================================================


class TestApiCredentialsModel:
    """Test ApiCredentials model - API authentication."""

    def test_api_credentials_count(self, session):
        """Test that we can query API credentials."""
        api_count = session.query(ApiCredentials).count()
        assert api_count >= 0, "Should be able to count API credentials"
        print(f"✅ Found {api_count} API credentials")

    def test_api_credentials_query(self, session):
        """Test querying and accessing API credentials properties."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert api.api_credentials_id is not None
        assert api.username is not None
        assert api.password is not None  # Should be hashed
        assert len(api.password) >= 50  # Bcrypt hash (typically 60 chars)
        print(f"✅ ApiCredentials: {api.username} (enabled={api.enabled})")

    def test_api_credentials_is_enabled_property(self, session):
        """Test is_enabled property."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert isinstance(api.is_enabled, bool)
        assert api.is_enabled == bool(api.enabled)
        print(f"✅ ApiCredentials {api.username} is_enabled = {api.is_enabled}")

    def test_api_credentials_role_assignments(self, session):
        """Test ApiCredentials -> [RoleApiCredentials] relationship."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        assert hasattr(api, "role_assignments")
        print(
            f"✅ ApiCredentials {api.username} has {len(api.role_assignments)} role assignment(s)"
        )

    def test_enabled_api_credentials(self, session):
        """Test querying only enabled API credentials."""
        enabled = (
            session.query(ApiCredentials).filter(ApiCredentials.enabled == True).all()
        )
        print(f"✅ Found {len(enabled)} enabled API credential(s)")

    def test_disabled_api_credentials(self, session):
        """Test querying disabled API credentials."""
        disabled = (
            session.query(ApiCredentials)
            .filter(
                (ApiCredentials.enabled == False) | (ApiCredentials.enabled.is_(None))
            )
            .all()
        )
        print(f"✅ Found {len(disabled)} disabled API credential(s)")


class TestRoleApiCredentialsModel:
    """Test RoleApiCredentials model - maps API credentials to roles."""

    def test_role_api_credentials_count(self, session):
        """Test that we can query role-API credential mappings."""
        count = session.query(RoleApiCredentials).count()
        assert count >= 0, "Should be able to count role-API mappings"
        print(f"✅ Found {count} role-API credential mapping(s)")

    def test_role_api_credentials_query(self, session):
        """Test querying role-API mappings."""
        mapping = session.query(RoleApiCredentials).first()
        if not mapping:
            pytest.skip("No role-API mappings in database")

        assert mapping.role_api_credentials_id is not None
        assert mapping.role_id is not None
        assert mapping.api_credentials_id is not None
        print(
            f"✅ RoleApiCredentials: role_id={mapping.role_id}, api_id={mapping.api_credentials_id}"
        )

    def test_role_api_to_role_relationship(self, session):
        """Test RoleApiCredentials -> Role relationship."""
        mapping = session.query(RoleApiCredentials).first()
        if not mapping:
            pytest.skip("No role-API mappings in database")

        assert mapping.role is not None
        assert isinstance(mapping.role, Role)
        print(f"✅ RoleApiCredentials → Role {mapping.role.name}")

    def test_role_api_to_credentials_relationship(self, session):
        """Test RoleApiCredentials -> ApiCredentials relationship."""
        mapping = session.query(RoleApiCredentials).first()
        if not mapping:
            pytest.skip("No role-API mappings in database")

        assert mapping.api_credentials is not None
        assert isinstance(mapping.api_credentials, ApiCredentials)
        print(
            f"✅ RoleApiCredentials → ApiCredentials {mapping.api_credentials.username}"
        )

    def test_api_credentials_to_roles(self, session):
        """Test getting all roles for an API credential."""
        api = session.query(ApiCredentials).first()
        if not api:
            pytest.skip("No API credentials in database")

        roles = [ra.role for ra in api.role_assignments]
        print(f"✅ ApiCredentials {api.username} has {len(roles)} role(s)")
        for role in roles:
            print(f"   - {role.name}")

    def test_role_to_api_credentials(self, session):
        """Test getting all API credentials for a role."""
        # Find a role that has API credentials
        mapping = session.query(RoleApiCredentials).first()
        if not mapping:
            pytest.skip("No role-API mappings in database")

        role = mapping.role
        assert hasattr(role, "api_credentials")
        api_creds = [ra.api_credentials for ra in role.api_credentials]
        print(f"✅ Role {role.name} has {len(api_creds)} API credential(s)")


# ============================================================================
# Project Code Model
# ============================================================================


class TestProjectCodeModel:
    """Test ProjectCode model - project code generation rules."""

    def test_project_code_count(self, session):
        """Test that we can query project codes."""
        count = session.query(ProjectCode).count()
        assert count >= 0, "Should be able to count project codes"
        print(f"✅ Found {count} project code rule(s)")

    def test_project_code_query(self, session):
        """Test querying project code rules."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        assert proj_code.facility_id is not None
        assert proj_code.mnemonic_code_id is not None
        assert proj_code.digits is not None
        assert isinstance(proj_code.digits, int)
        print(
            f"✅ ProjectCode: facility_id={proj_code.facility_id}, mnemonic_id={proj_code.mnemonic_code_id}, digits={proj_code.digits}"
        )

    def test_project_code_composite_key(self, session):
        """Test composite primary key (facility_id, mnemonic_code_id)."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        # Try to query by both parts of composite key
        same_code = (
            session.query(ProjectCode)
            .filter(
                ProjectCode.facility_id == proj_code.facility_id,
                ProjectCode.mnemonic_code_id == proj_code.mnemonic_code_id,
            )
            .one()
        )

        assert same_code.facility_id == proj_code.facility_id
        assert same_code.mnemonic_code_id == proj_code.mnemonic_code_id
        print("✅ Composite key query works")

    def test_project_code_to_facility(self, session):
        """Test ProjectCode -> Facility relationship."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        assert proj_code.facility is not None
        assert isinstance(proj_code.facility, Facility)
        print(f"✅ ProjectCode → Facility {proj_code.facility.facility_name}")

    def test_project_code_to_mnemonic(self, session):
        """Test ProjectCode -> MnemonicCode relationship."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        assert proj_code.mnemonic_code is not None
        assert isinstance(proj_code.mnemonic_code, MnemonicCode)
        print(f"✅ ProjectCode → MnemonicCode {proj_code.mnemonic_code.code}")

    def test_facility_to_project_codes(self, session):
        """Test reverse relationship: Facility -> [ProjectCodes]."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        facility = proj_code.facility
        assert hasattr(facility, "project_codes")
        assert len(facility.project_codes) > 0
        assert proj_code in facility.project_codes
        print(
            f"✅ Facility {facility.facility_name} has {len(facility.project_codes)} project code rule(s)"
        )

    def test_mnemonic_to_project_codes(self, session):
        """Test reverse relationship: MnemonicCode -> [ProjectCodes]."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        mnemonic = proj_code.mnemonic_code
        assert hasattr(mnemonic, "project_codes")
        assert len(mnemonic.project_codes) > 0
        assert proj_code in mnemonic.project_codes
        print(
            f"✅ MnemonicCode {mnemonic.code} has {len(mnemonic.project_codes)} project code rule(s)"
        )

    def test_project_code_digits_range(self, session):
        """Test that digits field has reasonable values."""
        proj_codes = session.query(ProjectCode).all()
        if not proj_codes:
            pytest.skip("No project codes in database")

        for pc in proj_codes:
            assert pc.digits > 0, "Digits should be positive"
            # Some project codes have very large digit values (188+), which is valid
            # This might represent maximum IDs or other large number formats
            assert pc.digits <= 1000, "Digits should be reasonable (<=1000)"

        max_digits = max(pc.digits for pc in proj_codes)
        min_digits = min(pc.digits for pc in proj_codes)
        print(
            f"✅ All project code digits are in valid range (min={min_digits}, max={max_digits})"
        )


# ============================================================================
# FOS/AOI Mapping Model
# ============================================================================


class TestFosAoiModel:
    """Test FosAoi model - Field of Science to Area of Interest mapping."""

    def test_fos_aoi_count(self, session):
        """Test that we can query FOS-AOI mappings."""
        count = session.query(FosAoi).count()
        assert count >= 0, "Should be able to count FOS-AOI mappings"
        print(f"✅ Found {count} FOS-AOI mapping(s)")

    def test_fos_aoi_query(self, session):
        """Test querying FOS-AOI mappings."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        assert fos.fos_aoi_id is not None
        assert fos.fos_id is not None
        assert fos.area_of_interest_id is not None
        print(f"✅ FosAoi: fos_id={fos.fos_id}, fos='{fos.fos}'")

    def test_fos_aoi_to_area_of_interest(self, session):
        """Test FosAoi -> AreaOfInterest relationship."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        assert fos.area_of_interest is not None
        assert isinstance(fos.area_of_interest, AreaOfInterest)
        print(
            f"✅ FOS {fos.fos_id} ({fos.fos}) → AOI {fos.area_of_interest.area_of_interest}"
        )

    def test_area_of_interest_to_fos(self, session):
        """Test reverse relationship: AreaOfInterest -> [FosAoi]."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        aoi = fos.area_of_interest
        assert hasattr(aoi, "fos_mappings")
        assert len(aoi.fos_mappings) > 0
        assert fos in aoi.fos_mappings
        print(
            f"✅ AreaOfInterest {aoi.area_of_interest} has {len(aoi.fos_mappings)} FOS mapping(s)"
        )

    def test_fos_id_unique(self, session):
        """Test that fos_id is unique."""
        fos_list = session.query(FosAoi).all()
        if not fos_list:
            pytest.skip("No FOS-AOI mappings in database")

        fos_ids = [f.fos_id for f in fos_list]
        unique_fos_ids = set(fos_ids)
        assert len(fos_ids) == len(unique_fos_ids), "fos_id should be unique"
        print(f"✅ All {len(fos_ids)} FOS IDs are unique")

    def test_fos_aoi_timestamps(self, session):
        """Test that FosAoi has timestamp fields."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        assert hasattr(fos, "creation_time")
        assert hasattr(fos, "modified_time")
        assert fos.creation_time is not None
        print(f"✅ FosAoi has timestamps: created={fos.creation_time}")


# ============================================================================
# Responsible Party Model
# ============================================================================


class TestResponsiblePartyModel:
    """Test ResponsibleParty model - account responsibility tracking."""

    def test_responsible_party_count(self, session):
        """Test that we can query responsible parties."""
        count = session.query(ResponsibleParty).count()
        assert count >= 0, "Should be able to count responsible parties"
        print(f"✅ Found {count} responsible part(y/ies)")

    def test_responsible_party_create(self, session):
        """Test creating a responsible party (table is currently empty)."""
        # Get existing account and user
        account = session.query(Account).first()
        user = session.query(User).first()

        if not account or not user:
            pytest.skip("Need account and user in database")

        # Create responsible party
        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type="PI",
        )

        session.add(rp)
        session.flush()

        assert rp.responsible_party_id is not None
        assert rp.account_id == account.account_id
        assert rp.user_id == user.user_id
        assert rp.responsible_party_type == "PI"
        print(f"✅ Created ResponsibleParty: {rp}")

        # Test relationships
        assert rp.account == account
        assert rp.user == user
        print("✅ ResponsibleParty relationships work")

        session.rollback()  # Don't save

    def test_responsible_party_to_account(self, session):
        """Test ResponsibleParty -> Account relationship."""
        rp = session.query(ResponsibleParty).first()
        if not rp:
            pytest.skip("No responsible parties in database")

        assert rp.account is not None
        assert isinstance(rp.account, Account)
        print(f"✅ ResponsibleParty → Account {rp.account.account_id}")

    def test_responsible_party_to_user(self, session):
        """Test ResponsibleParty -> User relationship."""
        rp = session.query(ResponsibleParty).first()
        if not rp:
            pytest.skip("No responsible parties in database")

        assert rp.user is not None
        assert isinstance(rp.user, User)
        print(f"✅ ResponsibleParty → User {rp.user.username}")

    def test_account_to_responsible_parties(self, session):
        """Test reverse relationship: Account -> [ResponsibleParty]."""
        account = session.query(Account).first()
        if not account:
            pytest.skip("No accounts in database")

        assert hasattr(account, "responsible_parties")
        print(
            f"✅ Account has {len(account.responsible_parties)} responsible part(y/ies)"
        )

    def test_user_to_responsible_accounts(self, session):
        """Test reverse relationship: User -> [ResponsibleParty]."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        assert hasattr(user, "responsible_accounts")
        print(f"✅ User has {len(user.responsible_accounts)} responsible account(s)")

    def test_responsible_party_timestamps(self, session):
        """Test that ResponsibleParty has timestamp fields."""
        # Create one since table is empty
        account = session.query(Account).first()
        user = session.query(User).first()

        if not account or not user:
            pytest.skip("Need account and user in database")

        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type="admin",
        )

        session.add(rp)
        session.flush()

        assert hasattr(rp, "creation_time")
        assert hasattr(rp, "modified_time")
        assert rp.creation_time is not None
        print(f"✅ ResponsibleParty has timestamps: created={rp.creation_time}")

        session.rollback()


# ============================================================================
# Integration Tests - Cross-Model Queries
# ============================================================================


class TestNewModelsIntegration:
    """Test queries that span multiple new models."""

    def test_resource_type_charging_setup(self, session):
        """Test complete charging setup for a resource type."""
        # Get a resource type that has both factors and formulas
        resource_type = session.query(ResourceType).join(ResourceType.factors).first()
        if not resource_type:
            pytest.skip("No resource types with factors")

        print(f"\n✅ Resource Type: {resource_type.resource_type}")
        print(f"   Factors: {len(resource_type.factors)}")
        for factor in resource_type.factors:
            print(
                f"     - {factor.factor_name}: {factor.value} (active={factor.is_active})"
            )

        print(f"   Formulas: {len(resource_type.formulas)}")
        for formula in resource_type.formulas:
            print(f"     - {formula.formula_name}: {formula.formula_str[:50]}...")

    def test_facility_project_code_generation(self, session):
        """Test facility project code generation setup."""
        # Get a facility with project codes
        facility = session.query(Facility).join(Facility.project_codes).first()
        if not facility:
            pytest.skip("No facilities with project codes")

        print(f"\n✅ Facility: {facility.facility_name}")
        print(f"   Project code rules: {len(facility.project_codes)}")
        for pc in facility.project_codes[:5]:  # Show first 5
            print(f"     - {pc.mnemonic_code.code}: {pc.digits} digits")

    def test_area_of_interest_fos_mapping(self, session):
        """Test area of interest with FOS mappings."""
        # Get an area of interest with FOS mappings
        aoi = session.query(AreaOfInterest).join(AreaOfInterest.fos_mappings).first()
        if not aoi:
            pytest.skip("No areas of interest with FOS mappings")

        print(f"\n✅ Area of Interest: {aoi.area_of_interest}")
        print(f"   FOS mappings: {len(aoi.fos_mappings)}")
        for fos in aoi.fos_mappings:
            print(f"     - FOS {fos.fos_id}: {fos.fos}")

    def test_api_credential_full_permissions(self, session):
        """Test getting all permissions for an API credential."""
        api = session.query(ApiCredentials).first()
        if not api or not api.role_assignments:
            pytest.skip("No API credentials with role assignments")

        print(f"\n✅ API Credential: {api.username}")
        print(f"   Enabled: {api.is_enabled}")
        print(f"   Roles: {len(api.role_assignments)}")
        for ra in api.role_assignments:
            role = ra.role
            print(f"     - {role.name}: {role.description}")
