"""Tests for the validation-outlier refactor schemas:
SetThresholdForm (sam.schemas.forms.user) and Create/EditOutageForm
(sam.schemas.forms.status).

These schemas are pure input coercion + shape validation. DB-backed checks
(account lookup, system_name → system_id resolution) stay in the routes per
CLAUDE.md §9.
"""
import datetime as dt

import pytest
from marshmallow import ValidationError

from sam.schemas.forms import SetThresholdForm, CreateOutageForm, EditOutageForm


pytestmark = pytest.mark.unit


class TestSetThresholdForm:
    def test_blank_clears_limit(self):
        """Empty string → load_default None (remove the limit)."""
        assert SetThresholdForm().load({'threshold_pct': ''}) == {'threshold_pct': None}

    def test_absent_clears_limit(self):
        assert SetThresholdForm().load({}) == {'threshold_pct': None}

    def test_valid_value(self):
        assert SetThresholdForm().load({'threshold_pct': '150'})['threshold_pct'] == 150

    @pytest.mark.parametrize('bad', ['100', '0', '-5'])
    def test_must_exceed_100(self, bad):
        with pytest.raises(ValidationError) as ei:
            SetThresholdForm().load({'threshold_pct': bad})
        assert 'threshold_pct' in ei.value.messages

    def test_non_integer_rejected(self):
        with pytest.raises(ValidationError):
            SetThresholdForm().load({'threshold_pct': 'abc'})


class TestCreateOutageForm:
    def _valid(self):
        return {'system_name': 'derecho', 'title': 'Login down', 'severity': 'minor'}

    def test_minimal_valid(self):
        data = CreateOutageForm().load(self._valid())
        assert data['system_name'] == 'derecho'
        assert data['title'] == 'Login down'
        assert data['severity'] == 'minor'
        # optional fields fall through to None; tz is popped
        assert data['start_time'] is None
        assert data['estimated_resolution'] is None
        assert 'tz' not in data

    def test_missing_required(self):
        with pytest.raises(ValidationError) as ei:
            CreateOutageForm().load({})
        assert set(ei.value.messages) == {'system_name', 'title', 'severity'}

    def test_bad_enum_values(self):
        bad = self._valid()
        bad['system_name'] = 'nope'
        bad['severity'] = 'apocalyptic'
        with pytest.raises(ValidationError) as ei:
            CreateOutageForm().load(bad)
        assert 'system_name' in ei.value.messages
        assert 'severity' in ei.value.messages

    def test_datetime_local_parsed_and_converted_to_utc(self):
        payload = self._valid()
        payload['start_time'] = '2026-06-28T10:00'
        payload['tz'] = 'America/Denver'   # MDT = UTC-6 in June
        data = CreateOutageForm().load(payload)
        # naive-UTC storage: 10:00 MDT → 16:00 UTC
        assert data['start_time'] == dt.datetime(2026, 6, 28, 16, 0)
        assert 'tz' not in data


class TestEditOutageForm:
    def test_title_optional(self):
        data = EditOutageForm().load({'status': 'monitoring', 'severity': 'major'})
        assert data['title'] is None
        assert data['status'] == 'monitoring'

    def test_status_required_and_validated(self):
        with pytest.raises(ValidationError) as ei:
            EditOutageForm().load({'severity': 'minor'})
        assert 'status' in ei.value.messages

        with pytest.raises(ValidationError) as ei:
            EditOutageForm().load({'status': 'bogus', 'severity': 'minor'})
        assert 'status' in ei.value.messages

    def test_estimated_resolution_blank_is_none(self):
        data = EditOutageForm().load(
            {'status': 'resolved', 'severity': 'minor', 'estimated_resolution': ''})
        assert data['estimated_resolution'] is None
