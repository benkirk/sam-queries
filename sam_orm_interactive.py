#!/usr/bin/env python
# coding: utf-8

# In[ ]:


from sam_models import *
from sam_orm_queries import create_sam_engine, get_session, find_user_by_username, get_project_with_full_details

engine, SessionLocal = create_sam_engine()


# In[ ]:


with get_session(SessionLocal) as session:
    # Find a user
    user = find_user_by_username(session, 'benkirk')
    if user:
        print(f"Found user: {user.full_name}")
        print(f"Primary GID: {user.primary_gid}")
        print(f"Primary email: {user.primary_email}")
        print(f"All emails: {', '.join(user.all_emails)}")

        # Get detailed email info
        print("Detailed email information:")
        for email_info in user.get_emails_detailed():
            primary_marker = " (PRIMARY)" if email_info['is_primary'] else ""
            active_marker = "" if email_info['active'] else " (INACTIVE)"
            print(f"  - {email_info['email']}{primary_marker}{active_marker}")

        # Find projects
        print("Detailed project information:")
        for p in user.all_projects:
            label = "" if p.active else " ** INACTIVE **"
            print(f"  {p.projcode}, {p.title}{label}")


# In[ ]:


def project_details(project):
    if project:
        print(f"--- Project Details ---")
        print(f"Project: {project.projcode}")
        print(f"Title: {project.title}")
        print(f"Lead: {project.lead.full_name}")
        if project.admin and project.lead != project.admin:
           print(f"Admin: {project.admin.full_name}") 
        for d in project.directories:
            label = "" if d.is_currently_active else " ** INACTIVE **"
            print(f"  Directory: {d.directory_name}{label}")
        # Show allocations by resource
        print(f"Allocations by resource:")
        allocs_by_resource = project.get_all_allocations_by_resource()        
        for resource_name, alloc in allocs_by_resource.items():
            resource = Resource.get_by_name(session,resource_name)
            label = "" if resource.is_active else " ** INACTIVE RESOURCE??? **"
            print(f"  {resource_name:12}: {alloc.amount:,.0f} (expires {alloc.end_date.date()}){label}")

        # Show users on project
        print(f"Users:")
        for user in project.users:
            print(f"  {user.username:12} {user.display_name:30} <{user.primary_email}>")


# In[ ]:


with get_session(SessionLocal) as session:
    # Find a project
    project = get_project_with_full_details(session, 'SCSG0001')
    project_details(project)


# In[ ]:


from sam_orm_queries import get_projects_expiring_soon
with get_session(SessionLocal) as session:
    # Get expiring projects (simple) - all resources
    print("\n--- Projects Expiring Soon (30 days, all resources) ---")
    expiring = get_projects_expiring_soon(session, days=30)
    expiring = get_projects_by_allocation_end_date(session, 
                                                   start_date=datetime.now(),
                                                   end_date=datetime.now() + timedelta(days=30),                                                   
                                                   facility_names=['UNIV', 'WNA'])
    #expiring = list(set(expiring))
    print(f"Found {len(expiring)} allocations expiring")
    for proj, alloc, res_name, days in expiring:
        print(f"\n{proj.projcode} / {days} days remaining")
        project_details(proj)


# In[ ]:


from sam_models_jobs_queries import *

projcode='SCSG0001'
start_date = datetime(2024, 1, 1)
end_date = datetime(2024, 12, 31)
resource='Casper'

with get_session(SessionLocal) as session:

    top_users = get_user_usage_on_project(        
                    session, projcode,
                    start_date,
                    end_date,
                    limit=5)
    for user in top_users:
        print(f"{user['username']:12}: {user['charges']:.2f}")

    usage = get_project_usage_summary(session,
                                      projcode,
                                      start_date,
                                      end_date,
                                      resource)
    #print(usage)
    print(f"Project {projcode} ran {usage['total_jobs']} and used {usage['total_core_hours']:2f} core hours")

    trend = get_daily_usage_trend(session,
                                  projcode,
                                  start_date,
                                  end_date,
                                  resource)
    for day in trend[:5]:
        print(f"{day['date']}: {day['jobs']} jobs, {day['charges']} charges")


# In[ ]:


with get_session(SessionLocal) as session:
    for projcode in [ 'CESM0002', 'CESM0028', 'P93300065' ]:
        # Find a project
        project = get_project_with_full_details(session, projcode)
        project_details(project)    

        ancestors = project.get_ancestors()
        print(f"Ancestors: {[p.projcode for p in ancestors]}")

        # Navigate down the tree
        descendants = project.get_descendants()
        print(f"All descendants: {len(descendants)}")
        print(project.print_tree())


# In[ ]:


with get_session(SessionLocal) as session:

    active_users = User.get_active_users(session)
    print(len(active_users))
    #for user in active_users:
    #    for project in user.projects:
    #        print(user,project)            

    active_projects = Project.get_active_projects(session)
    print(len(active_projects))


# In[ ]:


from sam_orm_queries import get_projects_with_expired_allocations, get_projects_by_allocation_end_date
from datetime import datetime, timedelta
with get_session(SessionLocal) as session:
    # Get expiring projects (simple) - all resources
    print("\n--- Projects Post-Expiry (90 days after, all resources) ---")
    expiring = get_projects_by_allocation_end_date(session, 
                                                   start_date=datetime.now() - timedelta(days=150),
                                                   end_date=datetime.now() - timedelta(days=90),                                                   
                                                   facility_names=['UNIV', 'WNA'])

    #expiring = list(set(expiring))
    print(f"Found {len(expiring)} recently expired allocations")
    #print(expiring)
    for proj, alloc, res_name, days in expiring:
        allocs_by_resource = proj.get_all_allocations_by_resource()
        #if allocs_by_resource: continue
        print(f"\n{proj.projcode} / {days} days since expiration")
        print(alloc)
        project_details(proj)



# In[ ]:





# In[ ]:




