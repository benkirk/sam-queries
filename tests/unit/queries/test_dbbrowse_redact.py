"""Redaction rules, plus a ratchet: a new secret-shaped SAM column forces a decision."""
import re

import pytest

from dbbrowse import DEFAULT_POLICY, load_catalog, read_only_connection, reflect_table
from dbbrowse.redact import name_tokens


@pytest.mark.parametrize('column', [
    'password', 'user_password', 'userPassword', 'client_secret', 'salt',
    'verify_code_hash', 'hash', 'refresh_token', 'token', 'api_key', 'apiKey',
    'private_key', 'secret_key',
])
def test_redacted_by_name(column):
    assert DEFAULT_POLICY.is_redacted('any', column)


@pytest.mark.parametrize('column', [
    'era_part_key', 'acct_part_key', 'dedup_key', 'job_key', 'resourceRepositoryKey',
    'resource_repository_key', 'token_type', 'api_credentials_id', 'username', 'key',
])
def test_not_redacted(column):
    assert not DEFAULT_POLICY.is_redacted('any', column)


def test_explicit_entries():
    assert DEFAULT_POLICY.is_redacted('api_credentials', 'password')


def test_name_tokens_split_camel_and_snake():
    assert name_tokens('resourceRepositoryKey') == ('resource', 'repository', 'key')
    assert name_tokens('api_credentials_id') == ('api', 'credentials', 'id')


_SUSPICIOUS = re.compile(r'pass|secret|token|hash|key|cred|salt|pwd', re.I)

# Secret-shaped names that are not secrets. Adding a column here is a review
# decision: it will be selectable, filterable and sortable in /database.
_KNOWN_SAFE = {
    ('api_credentials', 'api_credentials_id'),
    ('role_api_credentials', 'api_credentials_id'),
    ('role_api_credentials', 'role_api_credentials_id'),
    ('comp_activity', 'acct_part_key'),
    ('comp_activity', 'era_part_key'),
    ('comp_job', 'era_part_key'),
    ('manual_task', 'job_key'),
    ('notification_log', 'dedup_key'),
    ('users', 'token_type'),
    ('xras_allocation', 'resourceRepositoryKey'),
    ('xras_resource_repository_key_resource', 'resource_repository_key'),
}


def test_every_secret_shaped_sam_column_is_decided(engine):
    undecided = []
    with read_only_connection(engine) as conn:
        for entry in load_catalog(conn):
            for col in reflect_table(conn, None, entry.name).c:
                key = (entry.name, col.name)
                if (_SUSPICIOUS.search(col.name) and key not in _KNOWN_SAFE
                        and not DEFAULT_POLICY.is_redacted(*key)):
                    undecided.append(key)
    assert not undecided, (
        f'{undecided}: redact in dbbrowse/redact.py or list in _KNOWN_SAFE')
