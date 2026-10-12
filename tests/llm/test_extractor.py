import json

import httpx
import pytest

from order_vehicles.booking.models import CallSession
from order_vehicles.llm.client import ChatCompletionsLLMAdapter
from order_vehicles.llm.extractor import InformationExtractor


@pytest.mark.asyncio
async def test_extracts_multiple_fields_in_one_turn(uuid_factory) -> None:
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"patch": {"name": "Nam", "vehicle_choice": "ô tô"}, "intent": "provide"})}}]})
    adapter = ChatCompletionsLLMAdapter("http://llm.local/v1", "model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    extraction = await InformationExtractor(adapter).extract("Tôi là Nam, đặt ô tô", CallSession.create(["bike", "car"], uuid_factory=uuid_factory))
    assert extraction.patch.name == "Nam" and extraction.patch.vehicle_choice == "ô tô"


@pytest.mark.asyncio
async def test_schema_retry_once_without_applying_invalid_patch(uuid_factory) -> None:
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        content = '{"patch":{"hacker":1},"intent":"provide"}' if count == 1 else '{"patch":{"name":"Nam"},"intent":"provide"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    adapter = ChatCompletionsLLMAdapter("http://llm.local/v1", "model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    session = CallSession.create(["bike", "car"], uuid_factory=uuid_factory)
    result = await InformationExtractor(adapter).extract("Nam", session)
    assert count == 2 and session.revision == 0 and result.patch.name == "Nam"


@pytest.mark.asyncio
async def test_extra_fields_are_rejected(uuid_factory) -> None:
    class Bad:
        calls = 0
        async def extract(self, transcript, context, retry=False):
            self.calls += 1
            return {"patch": {"secret": "x"}, "intent": "provide"}
    bad = Bad()
    with pytest.raises(Exception):
        await InformationExtractor(bad).extract("x", CallSession.create(["car"], uuid_factory=uuid_factory))
    assert bad.calls == 2


def test_assistant_history_cannot_supply_customer_fields(uuid_factory) -> None:
    session = CallSession.create(["car"], uuid_factory=uuid_factory)
    context = InformationExtractor.context(session)
    assert "assistant_history" not in context and "transcript" not in context
    assert context["timezone"] == "Asia/Ho_Chi_Minh"
    assert context["service_timestamp"].endswith("+07:00")


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["cancel", "correct"])
async def test_cancel_and_correction_intents(intent, uuid_factory) -> None:
    class Fake:
        async def extract(self, transcript, context, retry=False):
            return {"patch": {}, "intent": intent}
    result = await InformationExtractor(Fake()).extract("x", CallSession.create(["car"], uuid_factory=uuid_factory))
    assert result.intent == intent
