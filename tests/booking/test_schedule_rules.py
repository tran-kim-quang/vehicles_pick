from datetime import datetime

import pytest

from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, ScheduleProposal, ExtractionResult
from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.validators import resolve_schedule


NOW = datetime.fromisoformat('2026-10-13T10:00:00+07:00')


def test_hour_then_day_keeps_hour_and_defaults_current_month_year(session):
    registry = FieldRegistry()
    registry.apply(session, BookingPatch(booking_mode='scheduled', schedule=ScheduleProposal(hour=19)), NOW)
    assert session.schedule_proposal.hour == 19
    assert 'scheduled_at' in registry.missing_fields(session)
    registry.apply(session, BookingPatch(schedule=ScheduleProposal(day=14)), NOW)
    assert session.fields['scheduled_at'].value.isoformat() == '2026-10-14T19:00:00+07:00'


def test_day_only_update_keeps_minutes(session):
    registry = FieldRegistry()
    registry.apply(session, BookingPatch(booking_mode='scheduled', schedule=ScheduleProposal(hour=19, minute=30)), NOW)
    registry.apply(session, BookingPatch(schedule=ScheduleProposal(day=14)), NOW)
    assert session.fields['scheduled_at'].value.minute == 30


@pytest.mark.parametrize('proposal', [ScheduleProposal(date='2026-11-01', hour=19), ScheduleProposal(date='2026-09-30', hour=19), ScheduleProposal(date='2027-10-14', hour=19), ScheduleProposal(date='2026-10-13', hour=9)])
def test_rejects_other_month_year_and_past(proposal):
    with pytest.raises(ValueError):
        resolve_schedule(proposal, NOW, 'Asia/Ho_Chi_Minh')


def test_hour_without_period_uses_24_hour_time():
    assert resolve_schedule(ScheduleProposal(day=14, hour=8), NOW, 'Asia/Ho_Chi_Minh').hour == 8


def test_partial_schedule_question_only_asks_day(session):
    dialogue = DialogueManager(FieldRegistry())
    result = dialogue.handle(session, ExtractionResult(patch=BookingPatch(name='Quang', phone='0345329699', booking_mode='scheduled', schedule=ScheduleProposal(hour=19))), NOW)
    assert result.field == 'scheduled_at'
    assert '19:00' in result.text
    assert 'ngày' in result.text
    assert 'mấy giờ' not in result.text
