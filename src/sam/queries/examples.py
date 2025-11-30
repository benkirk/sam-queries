from sam import *
from sam.summaries import *
from sam.core.groups import *
from sam.queries import *

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from sqlalchemy.orm import Session


# ============================================================================
# Example Usage
# ============================================================================

def example_usage():
    from sam.session import create_sam_engine, get_session

    """Demonstrate usage of the query functions."""

    # Setup - will load from .env file
    engine, SessionLocal = create_sam_engine()

    with get_session(SessionLocal) as session:
        # Find a user
        user = find_user_by_username(session, 'benkirk')
        if user:
            print(f"Found user: {user.full_name}")
            print(f"Primary GID: {user.primary_gid}")
            print(f"Primary email: {user.primary_email}")
            print(f"All emails: {', '.join(user.all_emails)}")

            # Get detailed email info
            print("\nDetailed email information:")
            for email_info in user.get_emails_detailed():
                primary_marker = " (PRIMARY)" if email_info['is_primary'] else ""
                active_marker = "" if email_info['active'] else " (INACTIVE)"
                print(f"  - {email_info['email']}{primary_marker}{active_marker}")

            # Find projects
            for p in user.all_projects:
                label = "" if p.active else "** INACTIVE **"
                print(p,label)

        # Find users with multiple emails
        multi_email_users = get_users_with_multiple_emails(session, min_emails=2)
        print(f"\n--- Users with multiple emails ---: {len(multi_email_users)}")
        for user, count in multi_email_users[:5]:
            print(f"{user.username} ({user.full_name}): {count} emails")
            for email in user.all_emails:
                print(f"  - {email}")

        # Find users without primary email set
        print("\n--- Users without primary email ---")
        no_primary = get_users_without_primary_email(session)
        print(f"Found {len(no_primary)} active users without a primary email")
        for user in no_primary[:5]:
            print(f"  {user.username}: {', '.join(user.all_emails)}")

        # Get project details
        project = get_project_with_full_details(session, 'SCSG0001')
        if project:
            print(f"\n--- Project Details ---")
            print(f"Project: {project.projcode}")
            print(f"Title: {project.title}")
            print(f"Lead: {project.lead.full_name}")

            # Show allocations by resource
            print(f"\nAllocations by resource:")
            allocs_by_resource = project.get_all_allocations_by_resource()
            for resource_name, alloc in allocs_by_resource.items():
                print(f"  {resource_name}: {alloc.amount:,.2f} (expires {alloc.end_date})")

            # Show allocations for specific resource
            print(f"\nSpecific Allocation by resource:")
            resource_name = "Derecho"
            alloc = project.get_allocation_by_resource(resource_name)
            print(f"  {resource_name}: {alloc.amount:,.2f} (expires {alloc.end_date})")

            # Show users on project (including lead/admin)
            usernames = [u.username for u in project.roster]
            print(f"Roster ({len(usernames)}):");
            for u in usernames: print(u);

        # Show available resources
        print("\n--- Available Resources ---")
        resources = get_available_resources(session)
        for res in resources:
            status = "ACTIVE" if res['active'] else "DECOMMISSIONED"
            print(f"  {res['resource_name']:20} ({res['resource_type']:10}) {status}")

        # Get expiring projects (simple) - all resources
        print("\n--- Projects Expiring Soon (30 days, all resources) ---")
        expiring = get_projects_expiring_soon(session, days=30)
        print(f"Found {len(expiring)} allocations expiring")
        for proj, alloc, res_name, days in expiring[:5]:
            print(f"  {proj.projcode} ({res_name}): {days} days remaining")

        # Get expiring projects for specific resource
        print("\n--- Derecho Allocations Expiring Soon (30 days) ---")
        expiring_derecho = get_projects_expiring_soon(session, days=30, resource_name='Derecho')
        print(f"Found {len(expiring_derecho)} Derecho allocations expiring")
        for proj, alloc, res_name, days in expiring_derecho[:5]:
            print(f"  {proj.projcode}: {days} days remaining (expires {alloc.end_date.date()})")

        # Statistics
        print("\n--- User Statistics ---")
        stats = get_user_statistics(session)
        for key, value in stats.items():
            print(f"  {key}: {value}")

        print("\n--- Project Statistics ---")
        proj_stats = get_project_statistics(session)
        print(f"  Total: {proj_stats['total_projects']}")
        print(f"  Active: {proj_stats['active_projects']}")
        print(f"  By Facility:")
        for facility, count in proj_stats['by_facility'].items():
            print(f"    {facility}: {count}")

        # Get user's project access
        if user:
            print(f"\n--- Project Access for {user.username} ---")
            access = get_user_project_access(session, user.username)
            print(f"Total projects: {len(access)}")
            s=set()
            for item in access:
                print(f"  {item['projcode']}: {item['role']}")
                s.add(item['projcode'])
            print(len(user.projects), len(s))

        # Test get_users_on_project
        print("\n--- Users on Project SCSG0001 ---")
        project_users = get_users_on_project(session, 'SCSG0001')
        for user_info in project_users:
            print(f"  {user_info['role']:8} {user_info['username']:12} {user_info['display_name']:20} <{user_info['email']}>")


if __name__ == '__main__':
    example_usage()
