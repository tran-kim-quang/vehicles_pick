from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import phonenumbers

from .models import ScheduleProposal


def normalize_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("name is empty")
    return value


def normalize_phone(value: str, region: str = "VN") -> str:
    number = phonenumbers.parse(value, region)
    if not phonenumbers.is_valid_number(number):
        raise ValueError("invalid phone")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def resolve_schedule(proposal: ScheduleProposal, now: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    if proposal.date is not None and proposal.relative_days is not None:
        raise ValueError("choose explicit or relative date")
    day = proposal.date
    if proposal.month is not None and proposal.month != local_now.month:
        raise ValueError("Chỉ hỗ trợ đặt xe trong tháng hiện tại.")
    if proposal.year is not None and proposal.year != local_now.year:
        raise ValueError("Chỉ hỗ trợ đặt xe trong năm hiện tại.")
    if day is None and proposal.day is not None:
        try:
            day = local_now.date().replace(day=proposal.day)
        except ValueError:
            raise ValueError("Ngày không hợp lệ trong tháng hiện tại.")
    if day is None and proposal.relative_days is not None:
        day = local_now.date() + timedelta(days=proposal.relative_days)
    if day is None or proposal.hour is None:
        raise ValueError("date and time are required")
    if (day.year, day.month) != (local_now.year, local_now.month):
        raise ValueError("Chỉ hỗ trợ đặt xe trong tháng và năm hiện tại.")
    hour = proposal.hour
    if proposal.period == "am":
        hour = 0 if hour == 12 else hour
    elif proposal.period == "pm":
        hour = 12 if hour == 12 else hour + 12
    scheduled = datetime.combine(day, time(hour, proposal.minute or 0), zone)
    if scheduled <= local_now:
        raise ValueError("Thời gian đặt xe đã qua. Vui lòng cung cấp thời gian trong tương lai.")
    return scheduled
