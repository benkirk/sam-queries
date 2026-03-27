"""
Tests for project-related ORM models: ProjectCode, FosAoi, and ResponsibleParty.
"""

import pytest

from sam import (
    ProjectCode, FosAoi, ResponsibleParty,
    Facility, MnemonicCode, AreaOfInterest,
    Account, User
)


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
        print(f"✅ ProjectCode: facility_id={proj_code.facility_id}, mnemonic_id={proj_code.mnemonic_code_id}, digits={proj_code.digits}")

    def test_project_code_composite_key(self, session):
        """Test composite primary key (facility_id, mnemonic_code_id)."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        same_code = session.query(ProjectCode).filter(
            ProjectCode.facility_id == proj_code.facility_id,
            ProjectCode.mnemonic_code_id == proj_code.mnemonic_code_id
        ).one()

        assert same_code.facility_id == proj_code.facility_id
        assert same_code.mnemonic_code_id == proj_code.mnemonic_code_id
        print(f"✅ Composite key query works")

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
        assert hasattr(facility, 'project_codes')
        assert len(facility.project_codes) > 0
        assert proj_code in facility.project_codes
        print(f"✅ Facility {facility.facility_name} has {len(facility.project_codes)} project code rule(s)")

    def test_mnemonic_to_project_codes(self, session):
        """Test reverse relationship: MnemonicCode -> [ProjectCodes]."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")

        mnemonic = proj_code.mnemonic_code
        assert hasattr(mnemonic, 'project_codes')
        assert len(mnemonic.project_codes) > 0
        assert proj_code in mnemonic.project_codes
        print(f"✅ MnemonicCode {mnemonic.code} has {len(mnemonic.project_codes)} project code rule(s)")

    def test_project_code_digits_range(self, session):
        """Test that digits field has reasonable values."""
        proj_codes = session.query(ProjectCode).all()
        if not proj_codes:
            pytest.skip("No project codes in database")

        for pc in proj_codes:
            assert pc.digits > 0, "Digits should be positive"
            assert pc.digits <= 1000, "Digits should be reasonable (<=1000)"

        max_digits = max(pc.digits for pc in proj_codes)
        min_digits = min(pc.digits for pc in proj_codes)
        print(f"✅ All project code digits are in valid range (min={min_digits}, max={max_digits})")

    def test_facility_project_code_generation(self, session):
        """Test facility project code generation setup."""
        facility = session.query(Facility).join(Facility.project_codes).first()
        if not facility:
            pytest.skip("No facilities with project codes")

        print(f"\n✅ Facility: {facility.facility_name}")
        print(f"   Project code rules: {len(facility.project_codes)}")
        for pc in facility.project_codes[:5]:
            print(f"     - {pc.mnemonic_code.code}: {pc.digits} digits")


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
        print(f"✅ FOS {fos.fos_id} ({fos.fos}) → AOI {fos.area_of_interest.area_of_interest}")

    def test_area_of_interest_to_fos(self, session):
        """Test reverse relationship: AreaOfInterest -> [FosAoi]."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        aoi = fos.area_of_interest
        assert hasattr(aoi, 'fos_mappings')
        assert len(aoi.fos_mappings) > 0
        assert fos in aoi.fos_mappings
        print(f"✅ AreaOfInterest {aoi.area_of_interest} has {len(aoi.fos_mappings)} FOS mapping(s)")

    def test_fos_id_unique(self, session):
        """Test that fos_id is unique."""
        fos_list = session.query(FosAoi).all()
        if not fos_list:
            pytest.skip("No FOS-AOI mappings in database")

        fos_ids = [f.fos_id for f in fos_list]
        assert len(fos_ids) == len(set(fos_ids)), "fos_id should be unique"
        print(f"✅ All {len(fos_ids)} FOS IDs are unique")

    def test_fos_aoi_timestamps(self, session):
        """Test that FosAoi has timestamp fields."""
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")

        assert hasattr(fos, 'creation_time')
        assert hasattr(fos, 'modified_time')
        assert fos.creation_time is not None
        print(f"✅ FosAoi has timestamps: created={fos.creation_time}")

    def test_area_of_interest_fos_mapping(self, session):
        """Test area of interest with FOS mappings."""
        aoi = session.query(AreaOfInterest).join(AreaOfInterest.fos_mappings).first()
        if not aoi:
            pytest.skip("No areas of interest with FOS mappings")

        print(f"\n✅ Area of Interest: {aoi.area_of_interest}")
        print(f"   FOS mappings: {len(aoi.fos_mappings)}")
        for fos in aoi.fos_mappings:
            print(f"     - FOS {fos.fos_id}: {fos.fos}")


class TestResponsiblePartyModel:
    """Test ResponsibleParty model - account responsibility tracking."""

    def test_responsible_party_count(self, session):
        """Test that we can query responsible parties."""
        count = session.query(ResponsibleParty).count()
        assert count >= 0, "Should be able to count responsible parties"
        print(f"✅ Found {count} responsible part(y/ies)")

    def test_responsible_party_create(self, session):
        """Test creating a responsible party (table is currently empty)."""
        account = session.query(Account).first()
        user = session.query(User).first()

        if not account or not user:
            pytest.skip("Need account and user in database")

        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type='PI'
        )

        session.add(rp)
        session.flush()

        assert rp.responsible_party_id is not None
        assert rp.account_id == account.account_id
        assert rp.user_id == user.user_id
        assert rp.responsible_party_type == 'PI'
        assert rp.account == account
        assert rp.user == user
        print(f"✅ Created ResponsibleParty and verified relationships")

        session.rollback()

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

        assert hasattr(account, 'responsible_parties')
        print(f"✅ Account has {len(account.responsible_parties)} responsible part(y/ies)")

    def test_user_to_responsible_accounts(self, session):
        """Test reverse relationship: User -> [ResponsibleParty]."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        assert hasattr(user, 'responsible_accounts')
        print(f"✅ User has {len(user.responsible_accounts)} responsible account(s)")

    def test_responsible_party_timestamps(self, session):
        """Test that ResponsibleParty has timestamp fields."""
        account = session.query(Account).first()
        user = session.query(User).first()

        if not account or not user:
            pytest.skip("Need account and user in database")

        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type='admin'
        )

        session.add(rp)
        session.flush()

        assert hasattr(rp, 'creation_time')
        assert hasattr(rp, 'modified_time')
        assert rp.creation_time is not None
        print(f"✅ ResponsibleParty has timestamps: created={rp.creation_time}")

        session.rollback()
