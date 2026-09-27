"""Fixtures for the ticket-provider tests."""
import pytest

from factories.tickets import FakeTicketProvider


@pytest.fixture
def fake_provider():
    return FakeTicketProvider()
