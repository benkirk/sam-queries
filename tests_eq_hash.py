"""
Test suite for __eq__ and __hash__ implementations in SAM models.

These tests demonstrate why proper __eq__ and __hash__ are critical
and verify correct behavior in various scenarios.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sam_models import Base, User, Project, Account, Allocation


# ============================================================================
# Fixture Setup
# ============================================================================

@pytest.fixture
def session():
    """Create an in-memory database for testing."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


# ============================================================================
# Test: Basic Equality
# ============================================================================

def test_user_equality_same_id(session):
    """Users with same ID are equal, even from different queries."""
    user = User(username='jdoe', unix_uid=1000)
    session.add(user)
    session.commit()
    user_id = user.user_id

    # Clear session to force new query
    session.expunge_all()

    user1 = session.query(User).get(user_id)
    user2 = session.query(User).get(user_id)

    assert user1 == user2, "Same user_id should be equal"
    assert user1 is not user2, "But they are different Python objects"


def test_user_inequality_different_id(session):
    """Users with different IDs are not equal."""
    user1 = User(username='jdoe', unix_uid=1000)
    user2 = User(username='asmith', unix_uid=1001)
    session.add_all([user1, user2])
    session.commit()

    assert user1 != user2, "Different user_ids should not be equal"


def test_user_inequality_different_type():
    """User should not equal non-User objects."""
    user = User(username='jdoe', unix_uid=1000)
    user.user_id = 1

    assert user != 1, "User should not equal int"
    assert user != "jdoe", "User should not equal string"
    assert user != None, "User should not equal None"


def test_transient_user_equality():
    """Unsaved users use object identity."""
    user1 = User(username='jdoe', unix_uid=1000)
    user2 = User(username='jdoe', unix_uid=1000)

    assert user1 != user2, "Transient objects without IDs are not equal"
    assert user1 == user1, "But object equals itself"


# ============================================================================
# Test: Set Operations
# ============================================================================

def test_set_deduplication(session):
    """Sets properly deduplicate users with same ID."""
    user = User(username='jdoe', unix_uid=1000)
    session.add(user)
    session.commit()
    user_id = user.user_id

    session.expunge_all()

    # Query same user multiple times
    user1 = session.query(User).get(user_id)
    user2 = session.query(User).get(user_id)
    user3 = session.query(User).get(user_id)

    user_set = {user1, user2, user3}

    assert len(user_set) == 1, "Set should contain only one user"
    assert user1 in user_set


def test_set_operations_without_proper_hash():
    """Demonstrates problem WITHOUT proper __hash__."""

    class BadUser:
        """User without __hash__ - uses default object identity."""
        def __init__(self, user_id, username):
            self.user_id = user_id
            self.username = username

        def __eq__(self, other):
            if not isinstance(other, BadUser):
                return False
            return self.user_id == other.user_id
        # No __hash__ - Python uses default object.__hash__

    user1 = BadUser(1, 'jdoe')
    user2 = BadUser(1, 'jdoe')

    # This creates a set with TWO items! 😱
    bad_set = {user1, user2}
    assert len(bad_set) == 2, "Without __hash__, set has duplicates!"


def test_set_union_intersection(session):
    """Test set operations work correctly."""
    users = [
        User(username=f'user{i}', unix_uid=1000+i)
        for i in range(5)
    ]
    session.add_all(users)
    session.commit()

    session.expunge_all()

    # Create overlapping sets
    set1 = set(session.query(User).filter(User.username.in_(['user0', 'user1', 'user2'])).all())
    set2 = set(session.query(User).filter(User.username.in_(['user1', 'user2', 'user3'])).all())

    union = set1 | set2
    intersection = set1 & set2
    difference = set1 - set2

    assert len(union) == 4, "Union should have user0, user1, user2, user3"
    assert len(intersection) == 2, "Intersection should have user1, user2"
    assert len(difference) == 1, "Difference should have user0"


# ============================================================================
# Test: Dictionary Operations
# ============================================================================

def test_dict_key_stability(session):
    """Users can be used as dict keys reliably."""
    user = User(username='jdoe', unix_uid=1000)
    session.add(user)
    session.commit()
    user_id = user.user_id

    session.expunge_all()

    user1 = session.query(User).get(user_id)

    # Use as dict key
    data = {user1: "some_value"}

    # Query same user again
    user2 = session.query(User).get(user_id)

    # Should retrieve value with different Python object
    assert user2 in data, "Same user should be found in dict"
    assert data[user2] == "some_value"


def test_dict_without_proper_hash():
    """Demonstrates problem WITHOUT proper __hash__."""

    class BadUser:
        def __init__(self, user_id, username):
            self.user_id = user_id
            self.username = username

        def __eq__(self, other):
            if not isinstance(other, BadUser):
                return False
            return self.user_id == other.user_id

    user1 = BadUser(1, 'jdoe')
    user2 = BadUser(1, 'jdoe')

    data = {user1: "value1"}

    # This fails! 😱
    assert user2 not in data, "Without __hash__, dict key lookup fails"


# ============================================================================
# Test: Project.users Deduplication
# ============================================================================

def test_project_users_deduplication(session):
    """Project.users properly deduplicates when user has multiple accounts."""
    from sam_models import Resource, AreaOfInterest, AreaOfInterestGroup

    # Setup
    user = User(username='jdoe', unix_uid=1000, active=True)

    area_group = AreaOfInterestGroup(name='Science')
    area = AreaOfInterest(area_of_interest='Climate', group=area_group)

    project = Project(
        projcode='TEST001',
        title='Test Project',
        project_lead_user_id=1,
        area_of_interest=area,
        active=True
    )

    resource1 = Resource(
        resource_name='Derecho',
        resource_type_id=1,
        resource_id=1
    )
    resource2 = Resource(
        resource_name='GLADE',
        resource_type_id=2,
        resource_id=2
    )

    session.add_all([user, area_group, area, project, resource1, resource2])
    session.commit()

    # User has accounts on TWO resources for same project
    account1 = Account(project=project, resource=resource1)
    account2 = Account(project=project, resource=resource2)
    session.add_all([account1, account2])
    session.commit()

    from datetime import datetime
    from sam_models import AccountUser

    # Add user to both accounts
    au1 = AccountUser(
        account=account1,
        user=user,
        start_date=datetime.utcnow()
    )
    au2 = AccountUser(
        account=account2,
        user=user,
        start_date=datetime.utcnow()
    )
    session.add_all([au1, au2])
    session.commit()

    session.expunge_all()

    # Reload project
    project = session.query(Project).first()

    # Should have ONE user, not two
    assert len(project.users) == 1, "User should be deduplicated"
    assert project.users[0].username == 'jdoe'


# ============================================================================
# Test: Cross-Session Behavior
# ============================================================================

def test_cross_session_equality():
    """Objects from different sessions are equal if same ID."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)

    # Session 1: Create user
    with Session(engine) as session1:
        user = User(username='jdoe', unix_uid=1000)
        session1.add(user)
        session1.commit()
        user_id = user.user_id

    # Session 2: Load same user
    with Session(engine) as session2:
        user1 = session2.query(User).get(user_id)

        # Session 3: Load same user
        with Session(engine) as session3:
            user2 = session3.query(User).get(user_id)

            # Should be equal even from different sessions
            assert user1 == user2
            assert hash(user1) == hash(user2)


# ============================================================================
# Test: Collection Operations
# ============================================================================

def test_list_deduplication(session):
    """Can deduplicate lists using set()."""
    users = [
        User(username=f'user{i}', unix_uid=1000+i)
        for i in range(3)
    ]
    session.add_all(users)
    session.commit()

    session.expunge_all()

    # Create list with duplicates
    user_list = []
    for _ in range(3):
        user_list.extend(session.query(User).all())

    assert len(user_list) == 9, "List has 3 users x 3 queries = 9"

    # Deduplicate
    unique_users = list(set(user_list))

    assert len(unique_users) == 3, "Set deduplication works"


def test_membership_testing(session):
    """in operator works correctly with collections."""
    users = [
        User(username=f'user{i}', unix_uid=1000+i)
        for i in range(3)
    ]
    session.add_all(users)
    session.commit()

    session.expunge_all()

    user1 = session.query(User).filter_by(username='user0').first()
    user2 = session.query(User).filter_by(username='user0').first()
    user_other = session.query(User).filter_by(username='user1').first()

    user_set = {user1}

    assert user2 in user_set, "Same user (different object) should be in set"
    assert user_other not in user_set, "Different user should not be in set"


# ============================================================================
# Test: Edge Cases
# ============================================================================

def test_none_id_handling():
    """Transient objects (ID=None) don't break equality."""
    user1 = User(username='jdoe', unix_uid=1000)
    user2 = User(username='jdoe', unix_uid=1000)

    # Neither has an ID yet
    assert user1.user_id is None
    assert user2.user_id is None

    # Should not be equal (different object identity)
    assert user1 != user2

    # But can still be hashed and added to sets
    user_set = {user1, user2}
    assert len(user_set) == 2, "Transient objects use object identity"


def test_comparison_after_attribute_change(session):
    """Equality based on ID, not attributes."""
    user = User(username='jdoe', unix_uid=1000, first_name='John')
    session.add(user)
    session.commit()
    user_id = user.user_id

    session.expunge_all()

    user1 = session.query(User).get(user_id)
    user2 = session.query(User).get(user_id)

    # Change attribute on user1
    user1.first_name = 'Jane'

    # Still equal - based on ID
    assert user1 == user2, "Equality based on ID, not attributes"
    assert hash(user1) == hash(user2), "Hash unchanged by attributes"


def test_allocation_in_dict_by_resource(session):
    """Real-world example: allocations grouped by resource."""
    from sam_models import Resource, AreaOfInterest, AreaOfInterestGroup

    # Setup
    area_group = AreaOfInterestGroup(name='Science')
    area = AreaOfInterest(area_of_interest='Climate', group=area_group)

    project = Project(
        projcode='TEST001',
        title='Test Project',
        project_lead_user_id=1,
        area_of_interest=area,
        active=True
    )

    resources = [
        Resource(resource_name='Derecho', resource_type_id=1),
        Resource(resource_name='GLADE', resource_type_id=1)
    ]

    session.add_all([area_group, area, project] + resources)
    session.commit()

    # Create accounts and allocations
    from datetime import datetime
    allocations_by_resource = {}

    for resource in resources:
        account = Account(project=project, resource=resource)
        session.add(account)
        session.commit()

        allocation = Allocation(
            account=account,
            amount=1000.0,
            start_date=datetime.utcnow()
        )
        session.add(allocation)
        session.commit()

        # Use resource as dict key
        allocations_by_resource[resource] = allocation

    session.expunge_all()

    # Reload and verify
    derecho = session.query(Resource).filter_by(resource_name='Derecho').first()

    assert derecho in allocations_by_resource, "Resource as dict key works"


# ============================================================================
# Performance Test
# ============================================================================

def test_set_operations_performance(session):
    """Verify set operations are O(1) not O(n)."""
    import time

    # Create many users
    users = [
        User(username=f'user{i}', unix_uid=1000+i)
        for i in range(1000)
    ]
    session.add_all(users)
    session.commit()

    session.expunge_all()

    # Load all users
    all_users = session.query(User).all()
    user_set = set(all_users)

    # Membership testing should be O(1)
    test_user = all_users[500]

    start = time.time()
    for _ in range(10000):
        _ = test_user in user_set  # O(1) with proper __hash__
    elapsed = time.time() - start

    assert elapsed < 0.1, f"Set lookup too slow: {elapsed}s"
    print(f"✓ 10,000 lookups in {elapsed:.4f}s")


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
