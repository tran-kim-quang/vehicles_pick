import httpx
import pytest

from order_vehicles.booking.confirmation import confirmation_text
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch
from order_vehicles.llm.client import ChatCompletionsLLMAdapter
from order_vehicles.llm.sentences import SentenceBuffer


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def test_sentence_buffer_does_not_split_decimal_or_abbreviation() -> None:
    buffer = SentenceBuffer()
    assert buffer.push("Giá 1.5 km, ví dụ e.g. tuyến A. ") == ["Giá 1.5 km, ví dụ e.g. tuyến A."]
    assert buffer.push("Anh muốn đặt xe lúc ") == []
    assert buffer.flush() == ["Anh muốn đặt xe lúc"]


def test_confirmation_text_bypasses_llm_rewrite(session, now, uuid_factory) -> None:
    FieldRegistry(uuid_factory=uuid_factory).apply(session, BookingPatch(name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"), now)
    assert confirmation_text(session).startswith("Xin xác nhận:")


@pytest.mark.asyncio
async def test_sse_parser_preserves_unicode_split_across_network_chunks() -> None:
    payload = 'data: {"choices":[{"delta":{"content":"Xin chào 🚕"}}]}\n\n'.encode()
    emoji = "🚕".encode()
    split_at = payload.index(emoji) + 2

    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkedStream([payload[:split_at], payload[split_at:], b"data: [DONE]\n\n"]),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ChatCompletionsLLMAdapter("http://llm.local/v1", "model", client=client)
    try:
        chunks = [chunk async for chunk in adapter.stream_reply("reply", {})]
    finally:
        await client.aclose()

    assert chunks == ["Xin chào 🚕"]
