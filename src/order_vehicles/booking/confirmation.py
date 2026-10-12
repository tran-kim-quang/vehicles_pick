from .models import CallSession


def confirmation_text(session: CallSession) -> str:
    value = lambda name: session.fields[name].value
    phone_value = str(value("phone"))
    phone_value = "0" + phone_value[3:] if phone_value.startswith("+84") else phone_value.lstrip("+")
    phone = " ".join(phone_value)
    if value("booking_mode") == "now":
        when = "đi ngay"
    else:
        when = value("scheduled_at").strftime("%H:%M ngày %d/%m/%Y")
    return (
        f"Xin xác nhận: tên {value('name')}, số điện thoại {phone}, "
        f"{when}, loại xe {value('vehicle_type')}. Thông tin có đúng không?"
    )
