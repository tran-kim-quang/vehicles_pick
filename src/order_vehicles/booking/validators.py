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
    if day is None and proposal.relative_days is not None:
        day = local_now.date() + timedelta(days=proposal.relative_days)
    if day is None or proposal.hour is None:
        raise ValueError("date and time are required")
    hour = proposal.hour
    if 1 <= hour <= 12 and proposal.period is None:
        raise ValueError("am or pm is required")
    if proposal.period == "am":
        hour = 0 if hour == 12 else hour
    elif proposal.period == "pm":
        hour = 12 if hour == 12 else hour + 12
    elif proposal.period != "24h" and hour <= 12:
        raise ValueError("ambiguous hour")
    scheduled = datetime.combine(day, time(hour, proposal.minute), zone)
    if scheduled <= local_now:
        raise ValueError("scheduled time must be in the future")
    return scheduled
