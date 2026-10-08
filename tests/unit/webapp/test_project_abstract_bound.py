"""The project abstract has a character bound on both forms and both textareas.

Legacy grew UCLA0042's abstract to 300 K characters of encoding garbage
(2026-10-07); the Details form re-submitted all of it on every save.
"""

import pytest
from marshmallow import ValidationError

from sam.schemas.forms import ABSTRACT_MAX_CHARS, CreateProjectForm, EditProjectForm


#: The create form's mode validator runs even under partial=True.
_MODE = {'projcode_mode': 'manual', 'projcode': 'TEST0001'}


def _load(schema_cls, **fields):
    return schema_cls().load({**_MODE, **fields}, partial=True)


@pytest.mark.parametrize('schema_cls', [CreateProjectForm, EditProjectForm])
class TestTheSchemaBound:

    def test_the_bound_itself_loads(self, schema_cls):
        data = _load(schema_cls, abstract='x' * ABSTRACT_MAX_CHARS)
        assert len(data['abstract']) == ABSTRACT_MAX_CHARS

    def test_one_past_the_bound_is_a_field_error(self, schema_cls):
        with pytest.raises(ValidationError) as exc:
            _load(schema_cls, abstract='x' * (ABSTRACT_MAX_CHARS + 1))
        assert 'abstract' in exc.value.messages

    def test_absent_is_still_optional(self, schema_cls):
        assert _load(schema_cls).get('abstract') is None


class TestTheTextareasCarryIt:

    def test_create_form(self, auth_client):
        html = auth_client.get('/admin/htmx/project-create-form').get_data(as_text=True)
        assert f'id="createProjectAbstract"' in html
        assert f'maxlength="{ABSTRACT_MAX_CHARS}"' in html

    def test_edit_page(self, auth_client, active_project):
        html = auth_client.get(
            f'/admin/project/{active_project.projcode}/edit?tab=details').get_data(as_text=True)
        assert 'id="editProjectAbstract"' in html
        assert f'maxlength="{ABSTRACT_MAX_CHARS}"' in html
