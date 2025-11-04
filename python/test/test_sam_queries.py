# test_sam_queries.py
"""
Quick self-test and usage demonstration for sam_queries.py.

This file spins up an in-memory SQLite database using the models in sam_models.py,
populates it with minimal sample data, and exercises each query helper.
"""

from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sam_models import Base, User, Project, Account, Allocation, Resource, EmailAddress
from sam_queries import UserQueries, ProjectQueries, ResourceQueries, AllocationQueries


def create_sample_data(session):
    """Populate the in-memory DB with minimal consistent sample data."""

    # --- Users ---
    alice = User(username="alice", first_name="Alice", last_name="Nguyen", active=True, deleted=False)
    bob = User(username="bob", first_name="Bob", last_name="Lopez", active=True, deleted=False)
    session.add_all([alice, bob])
    session.flush()

    # --- Email Addresses ---
    session.add_all([
        EmailAddress(user_id=alice.user_id, email_address="alice@example.com"),
        EmailAddress(user_id=bob.user_id, email_address="bob@example.com"),
    ])

    # --- Projects ---
    proj1 = Project(projcode="P1001", title="Atmospheric Research", active=True)
    proj2 = Project(projcode="P2002", title="Climate Simulation", active=True)
    session.add_all([proj1, proj2])
    session.flush()

    # --- Accounts ---
    acct1 = Account(account_id=1, project_id=proj1.project_id)
    acct2 = Account(account_id=2, project_id=proj2.project_id)
    session.add_all([acct1, acct2])
    session.flush()

    # --- Resources ---
    derecho = Resource(resource_name="Derecho", decommission_date=None)
    casper = Resource(resource_name="Casper", decommission_date=None)
    session.add_all([derecho, casper])
    session.flush()

    # --- Allocations ---
    now = datetime.utcnow()
    alloc1 = Allocation(
        account_id=acct1.account_id,
        resource_id=derecho.resource_id,
        start_date=now - timedelta(days=10),
        end_date=now + timedelta(days=90),
        deleted=False,
    )
    alloc2 = Allocation(
        account_id=acct2.account_id,
        resource_id=casper.resource_id,
        start_date=now - timedelta(days=20),
        end_date=now + timedelta(days=10),
        deleted=False,
    )
    session.add_all([alloc1, alloc2])

    session.commit()


def main():
    # --- Setup in-memory database ---
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    create_sample_data(session)

    print("\n=== User Queries ===")
    print("Find by username:", UserQueries.by_username(session, "alice"))
    print("Search 'bob':", [u.username for u in UserQueries.search(session, "bob")])
    print("Active users:", [u.username for u in UserQueries.active(session)])
    print("Find by email:", UserQueries.with_email(session, "alice@example.com"))

    print("\n=== Project Queries ===")
    print("Find by code:", ProjectQueries.by_code(session, "P1001"))
    print("Active projects:", [p.projcode for p in ProjectQueries.active(session)])
    print("Search 'Climate':", [p.title for p in ProjectQueries.search(session, "Climate")])

    print("\n=== Resource Queries ===")
    print("Derecho:", ResourceQueries.by_name(session, "Derecho"))
    print("Active resources:", [r.resource_name for r in ResourceQueries.active(session)])

    print("\n=== Allocation Queries ===")
    print("Active allocations:", [a.allocation_id for a in AllocationQueries.active(session)])
    print("Allocations for project P1001:", [a.allocation_id for a in AllocationQueries.for_project(session, "P1001")])
    print("Allocations for user alice:", [a.allocation_id for a in AllocationQueries.for_user(session, "alice")])

    print("\n✅ Test complete — queries executed successfully.")


if __name__ == "__main__":
    main()
