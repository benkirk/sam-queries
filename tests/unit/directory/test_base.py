"""The contract: the null reader never dials out, the kinds are fixed, records are frozen."""
import dataclasses
import inspect

import pytest

from factories.directory import FakeDirectory, make_person_record
from sam.integration.directory import (KINDS, DirectoryNotConfigured, DirectoryReader,
                                       DirectoryUnavailable, NullDirectory, PersonRecord)


def test_the_kinds_are_the_six_record_types_sam_mirrors():
    assert KINDS == ('person', 'position', 'collaboration', 'organization', 'institution',
                     'group')


def test_not_configured_is_a_kind_of_unavailable():
    assert issubclass(DirectoryNotConfigured, DirectoryUnavailable)


@pytest.mark.parametrize('read', ['person', 'organization', 'institution', 'group'])
def test_the_null_reader_refuses_every_read(read):
    reader = NullDirectory()
    assert reader.configured is False
    with pytest.raises(DirectoryNotConfigured):
        getattr(reader, read)(1)
    with pytest.raises(DirectoryNotConfigured):
        reader.search('person', 'x')


def test_the_null_reader_is_concrete_and_summarizes():
    assert not inspect.isabstract(NullDirectory)
    assert NullDirectory.from_environment().summary() == {'provider': 'none', 'configured': False}
    assert NullDirectory().check() == (True, '')


def test_the_abstract_surface():
    assert DirectoryReader.__abstractmethods__ == {
        'from_environment', 'configured', 'summary', 'person', 'organization', 'institution',
        'group', 'search'}


def test_records_are_frozen():
    record = make_person_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.username = 'other'
    assert isinstance(record, PersonRecord)


def test_the_default_session_reuses_nothing_and_still_reads():
    fake = FakeDirectory()
    fake.add(make_person_record(upid=7))
    with fake.session() as reader:
        assert reader.person(7).upid == 7
    assert fake.calls == [('person', 7)]


def test_an_unconfigured_fake_refuses_like_the_null_reader():
    with pytest.raises(DirectoryNotConfigured):
        FakeDirectory(configured=False).person(1)
