"""Fixtures shared across the notification tests."""

import pytest

from sam.notify import NullTransport


@pytest.fixture
def transport():
    return NullTransport()
