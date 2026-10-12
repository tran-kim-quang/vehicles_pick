import pytest

from order_vehicles.speech.application.leases import ResourceLeases
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile, VoiceRef


@pytest.mark.asyncio
async def test_busy_worker_blocks_global_select() -> None:
    leases = ResourceLeases()

    async with leases.acquire(RuntimeProfile.default()):
        with pytest.raises(SpeechError) as caught:
            await leases.assert_mutable("global:selection")

    assert caught.value.code == "RESOURCE_IN_USE"


@pytest.mark.asyncio
async def test_voice_delete_blocks_while_leased() -> None:
    leases = ResourceLeases()
    ref = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234")
    profile = RuntimeProfile.default().model_copy(update={"voice_ref": ref})

    async with leases.acquire(profile):
        with pytest.raises(SpeechError) as caught:
            await leases.assert_mutable(ref.encode())

    assert caught.value.code == "RESOURCE_IN_USE"


@pytest.mark.asyncio
async def test_cancel_and_exception_release_lease() -> None:
    leases = ResourceLeases()
    ref = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234")
    profile = RuntimeProfile.default().model_copy(update={"voice_ref": ref})

    with pytest.raises(RuntimeError):
        async with leases.acquire(profile):
            raise RuntimeError("cancelled work")

    await leases.assert_mutable(ref.encode())
    await leases.assert_mutable("global:selection")
