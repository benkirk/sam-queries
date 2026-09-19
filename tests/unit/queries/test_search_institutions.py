"""`search_institutions`: the datalist behind the Institution fields."""

from factories import make_institution

from sam.queries.admin import search_institutions



def test_prefix_hits_first_deleted_rows_never(session):
    make_institution(session, name='The Zqx Zebra College')
    make_institution(session, name='Zqx Zebra Institute of Testing')
    make_institution(session, name='Zqx Zebra Deleted University', deleted=True)
    names = search_institutions(session, '  zqx ')
    assert names[0] == 'Zqx Zebra Institute of Testing', 'prefix match outranks a substring match'
    assert 'The Zqx Zebra College' in names
    assert not any('Deleted' in n for n in names)


def test_short_or_empty_queries_return_nothing(session):
    assert search_institutions(session, 'z') == []
    assert search_institutions(session, None) == []


def test_the_limit_holds(session):
    for i in range(12):
        make_institution(session, name=f'Zqy Limit University {i:02d}')
    assert len(search_institutions(session, 'zqy limit', limit=5)) == 5
