"""Dismiss takes an optional reason; Comment still demands one."""
import pytest
from marshmallow import ValidationError

from sam.schemas.forms import XrasActivationEventForm, XrasDismissForm
from sam.schemas.forms.xras_activation import _COMMENT_MAX

pytestmark = pytest.mark.unit


class TestDismissForm:

    @pytest.mark.parametrize('posted', [{}, {'comment': ''}, {'comment': '   '}])
    def test_blank_becomes_none(self, posted):
        assert XrasDismissForm().load(posted) == {'comment': None}

    def test_a_reason_is_stripped_and_kept(self):
        assert XrasDismissForm().load({'comment': '  why  '}) == {'comment': 'why'}

    def test_the_length_cap_still_applies(self):
        with pytest.raises(ValidationError):
            XrasDismissForm().load({'comment': 'x' * (_COMMENT_MAX + 1)})


class TestCommentFormIsUnchanged:

    @pytest.mark.parametrize('posted', [{}, {'comment': ''}, {'comment': '   '}])
    def test_blank_is_still_rejected(self, posted):
        with pytest.raises(ValidationError):
            XrasActivationEventForm().load(posted)
