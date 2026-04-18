"""Tests for CreateChargeAdjustmentForm (sam.schemas.forms.adjustments).

The schema is pure input coercion + shape validation. It does NOT validate
that the submitted ``charge_adjustment_type_id`` is in the supported set —
that's a DB-backed membership check the route owns (see CLAUDE.md §9).
"""
import pytest
from marshmallow import ValidationError

from sam.schemas.forms import CreateChargeAdjustmentForm


pytestmark = pytest.mark.unit


def _valid_payload():
    return {
        'project_id': '8',
        'resource_id': '7',
        'charge_adjustment_type_id': '3',
        'amount': '100',
        'comment': 'ticket #1234',
    }


class TestHappyPath:
    def test_coerces_types(self):
        data = CreateChargeAdjustmentForm().load(_valid_payload())
        assert data['project_id'] == 8
        assert data['resource_id'] == 7
        assert data['charge_adjustment_type_id'] == 3
        assert data['amount'] == 100.0
        assert data['comment'] == 'ticket #1234'

    def test_decimal_amount_accepted(self):
        payload = _valid_payload()
        payload['amount'] = '7.5'
        data = CreateChargeAdjustmentForm().load(payload)
        assert data['amount'] == 7.5

    def test_comment_omitted_yields_none(self):
        """An absent ``comment`` falls through to load_default=None."""
        payload = _valid_payload()
        del payload['comment']
        data = CreateChargeAdjustmentForm().load(payload)
        assert data['comment'] is None

    def test_empty_comment_string_yields_none(self):
        """HtmxFormSchema strips empty strings in pre_load — the field then
        falls through to load_default=None, matching the ORM convention of
        NULL-instead-of-empty-string for optional Text columns."""
        payload = _valid_payload()
        payload['comment'] = ''
        data = CreateChargeAdjustmentForm().load(payload)
        assert data['comment'] is None

    def test_unknown_fields_dropped(self):
        """EXCLUDE drops CSRF tokens / stray fields silently."""
        payload = _valid_payload()
        payload['csrf_token'] = 'abc'
        payload['ignored'] = 'yes'
        data = CreateChargeAdjustmentForm().load(payload)
        assert 'csrf_token' not in data
        assert 'ignored' not in data


class TestAmountValidation:

    def test_zero_rejected(self):
        payload = _valid_payload()
        payload['amount'] = '0'
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert 'amount' in excinfo.value.messages

    def test_negative_rejected(self):
        payload = _valid_payload()
        payload['amount'] = '-5'
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert 'amount' in excinfo.value.messages

    def test_non_numeric_rejected(self):
        payload = _valid_payload()
        payload['amount'] = 'not-a-number'
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert 'amount' in excinfo.value.messages


class TestRequiredFields:

    @pytest.mark.parametrize('field', [
        'project_id', 'resource_id', 'charge_adjustment_type_id', 'amount',
    ])
    def test_missing_required_field(self, field):
        payload = _valid_payload()
        del payload[field]
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert field in excinfo.value.messages

    def test_non_int_project_id_rejected(self):
        payload = _valid_payload()
        payload['project_id'] = 'SCSG0001'   # projcode-style string
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert 'project_id' in excinfo.value.messages


class TestTypeIdIsShapeOnly:
    """Schema deliberately does not gate charge_adjustment_type_id on the
    webapp's supported set — that check requires a DB lookup and lives in
    the route (see CLAUDE.md §9). The schema only coerces to int."""

    @pytest.mark.parametrize('type_id_str', ['1', '2', '3', '4', '5', '6'])
    def test_any_positive_int_accepted(self, type_id_str):
        payload = _valid_payload()
        payload['charge_adjustment_type_id'] = type_id_str
        data = CreateChargeAdjustmentForm().load(payload)
        assert data['charge_adjustment_type_id'] == int(type_id_str)

    def test_non_int_rejected(self):
        payload = _valid_payload()
        payload['charge_adjustment_type_id'] = 'Credit'
        with pytest.raises(ValidationError) as excinfo:
            CreateChargeAdjustmentForm().load(payload)
        assert 'charge_adjustment_type_id' in excinfo.value.messages


class TestFlattenErrors:
    def test_errors_renderable_in_template(self):
        payload = _valid_payload()
        payload['amount'] = '-1'
        del payload['project_id']
        try:
            CreateChargeAdjustmentForm().load(payload)
        except ValidationError as e:
            flat = CreateChargeAdjustmentForm.flatten_errors(e.messages)
            assert any('Amount' in msg for msg in flat)
            assert any('Project Id' in msg for msg in flat)
