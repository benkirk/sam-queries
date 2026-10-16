"""Fixtures for the directory-client tests."""
import pytest

from factories.directory import FakeDirectory


@pytest.fixture
def fake_directory():
    return FakeDirectory()
