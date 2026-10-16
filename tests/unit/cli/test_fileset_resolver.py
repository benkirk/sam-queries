"""FilesetResolver: the one rule the disk ingest and the quota reconcile share."""
from datetime import datetime, timedelta

from cli.accounting.fileset_resolver import FilesetResolver
from sam.projects.projects import ProjectDirectory
from sam.resources.resources import ResourceType
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_seq


def _disk(session):
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    return make_resource(session, resource_type=rt, resource_name=f"RES_{next_seq('fr')}")


def _funded(session, resource):
    project = make_project(session)
    make_allocation(session, account=make_account(session, project=project, resource=resource))
    return project


def _link(session, project, path):
    ProjectDirectory.create(session, project_id=project.project_id, directory_name=path,
                            start_date=datetime.now() - timedelta(days=1))


def test_linked_path_wins_over_label(session):
    resource = _disk(session)
    owner, other = _funded(session, resource), _funded(session, resource)
    _link(session, owner, '/gpfs/x/owned')
    res = FilesetResolver(session, resource).resolve('/gpfs/x/owned', other.projcode)
    assert (res.project, res.via, res.reason) == (owner, 'path', None)


def test_label_as_projcode_with_unlinked_path(session):
    resource = _disk(session)
    project = _funded(session, resource)
    res = FilesetResolver(session, resource).resolve('/gpfs/x/new', project.projcode)
    assert (res.project, res.via, res.reason) == (project, 'projcode', 'unlinked_directory')
    assert res.action == 'create'


def test_unfunded_account_is_expired_directory(session):
    resource = _disk(session)
    project = make_project(session)
    make_account(session, project=project, resource=resource)
    res = FilesetResolver(session, resource).resolve('/gpfs/x/old', project.projcode)
    assert (res.project, res.reason) == (project, 'expired_directory')


def test_no_account_and_no_project(session):
    resource = _disk(session)
    project = make_project(session)
    r = FilesetResolver(session, resource)
    assert r.resolve('/gpfs/x/a', project.projcode).reason == 'no_account'
    assert r.resolve('/gpfs/x/b', 'ZZNOPE').reason == 'no_project'


def test_known_unowned_label(session):
    resource = _disk(session)
    res = FilesetResolver(session, resource).resolve('/gpfs/x/scratch', 'ROOT')
    assert (res.project, res.reason) == (None, 'known_unowned')


def test_sibling_label_borrows_the_one_linked_project(session):
    resource = _disk(session)
    project = _funded(session, resource)
    _link(session, project, '/quasar/rda')
    r = FilesetResolver(session, resource)
    r.learn_labels([('/quasar/rda', 'DECSX'), ('/quasar/rda_dr', 'DECSX')])
    res = r.resolve('/quasar/rda_dr', 'DECSX')
    assert (res.project, res.via) == (project, 'label')
