"""Shared fixtures for the log-archiver tests."""

import pytest

from logarchive_fakes import FakeStop


@pytest.fixture
def stop():
    return FakeStop()
