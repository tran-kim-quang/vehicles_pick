import asyncio

import pytest

from order_vehicles.booking.dialogue import DialogueDecision
from order_vehicles.booking.models import CallSession, FieldValue
from order_vehicles.llm.responder import BookingResponder


class Adapter:
    def __init__(self, text="Được, bạn muốn đi ngay. Bạn cho mình xin tên nhé?"):
        self.text = text
        self.context = None

    async def stream_reply(self, instruction, context):
        self.context = context
        yield self.text


@pytest.mark.asyncio
async def test_invalid_phone_cannot_be_acknowledged_by_llm(uuid_factory):
    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    session.fields["name"] = FieldValue(id=uuid_factory(), value="Quang")
    adapter = Adapter("Đã ghi nhận số điện thoại 01245.")
    decision = DialogueDecision(kind="ask", field="phone", validation_errors=["phone"], text="Số điện thoại không đúng định dạng. Bạn vui lòng cung cấp lại số điện thoại hợp lệ nhé.")
    reply = await BookingResponder(adapter).respond("Quang, 01245", session, decision, {})
    assert "không đúng định dạng" in reply.text
    assert "cung cấp lại" in reply.text
    assert "01245" not in reply.text
    assert "Quang" in reply.text


@pytest.mark.asyncio
async def test_reply_receives_validated_new_fields_and_pending_question(uuid_factory):
    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    session.fields["booking_mode"] = FieldValue(id=uuid_factory(), value="now")
    adapter = Adapter()
    decision = DialogueDecision(kind="ask", field="name", text="Bạn tên là gì?")
    reply = await BookingResponder(adapter).respond("đặt xe ngay", session, decision, {})
    assert reply.text == adapter.text
    assert reply.kind == "ask" and reply.field == "name"
    assert adapter.context["new_information"] == {"booking_mode": "now"}
    assert adapter.context["required_question"] == "name"


@pytest.mark.asyncio
async def test_confirmation_always_preserves_canonical_readback(uuid_factory):
    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    decision = DialogueDecision(kind="confirm", text="Xin xác nhận: tên Nam, số điện thoại 0 9 0, đi ngay, loại xe car.")
    reply = await BookingResponder(Adapter("Mình đã ghi nhận thông tin.")).respond("xe car", session, decision, {})
    assert reply.text.endswith(decision.text)


@pytest.mark.asyncio
async def test_confirmation_is_read_once_even_if_llm_would_repeat_it(uuid_factory):
    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    decision = DialogueDecision(kind="confirm", text="Xin xác nhận: tên Quang. Thông tin có đúng không?")
    adapter = Adapter(decision.text)
    reply = await BookingResponder(adapter).respond("xe car", session, decision, {})
    assert reply.text == decision.text
    assert adapter.context is None


@pytest.mark.asyncio
async def test_provider_error_or_empty_output_falls_back(uuid_factory):
    class Broken(Adapter):
        async def stream_reply(self, instruction, context):
            raise RuntimeError("unavailable")
            yield ""

    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    decision = DialogueDecision(kind="ask", field="name", text="Bạn tên là gì?")
    for adapter in [Broken(), Adapter(" ")]:
        assert await BookingResponder(adapter).respond("xin chào", session, decision, {}) == decision


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed(uuid_factory):
    class Cancelled(Adapter):
        async def stream_reply(self, instruction, context):
            raise asyncio.CancelledError()
            yield ""

    with pytest.raises(asyncio.CancelledError):
        await BookingResponder(Cancelled()).respond("xin chào", CallSession.create(["car"]), DialogueDecision(kind="ask", text="Tên?"), {})
