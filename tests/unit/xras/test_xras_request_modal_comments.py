"""The Request modal shows the approver's note the handoff notice will relay."""
from pathlib import Path

import pytest

from webapp.dashboards.allocations.xras.modals import _detail_actions

pytestmark = pytest.mark.unit

TEMPLATE = Path('src/webapp/templates/dashboards/allocations/partials/'
                'xras_request_detail.html')


def _payload(**action):
    base = {'actionId': 1, 'actionType': 'Extension', 'actionStatus': 'Approved',
            'resources': [], 'allocationDates': [], 'documents': []}
    base.update(action)
    return {'actions': [base]}


def test_admin_comments_ride_next_to_user_comments():
    (row,) = _detail_actions(_payload(adminComments='Approved with caveats.',
                                      userComments='Please extend.'))
    assert row['admin_comments'] == 'Approved with caveats.'
    assert row['user_comments'] == 'Please extend.'


def test_an_absent_note_is_none_not_a_key_error():
    (row,) = _detail_actions(_payload())
    assert row['admin_comments'] is None


def test_the_template_renders_the_key():
    body = TEMPLATE.read_text()
    assert 'action.admin_comments' in body
    assert "Approver's note" in body
