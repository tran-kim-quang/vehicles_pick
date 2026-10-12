import wave
from io import BytesIO

from .audio import SoundDeviceInput


async def record_reference(
    seconds: float,
    device: int | None,
    *,
    input_source=None,
) -> bytes:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    source = input_source or SoundDeviceInput()
    target_bytes = round(16000 * seconds) * 2
    chunks = bytearray()
    await source.open(device)
    try:
        async for frame in source.frames():
            remaining = target_bytes - len(chunks)
            chunks.extend(frame[:remaining])
            if len(chunks) >= target_bytes:
                break
    finally:
        await source.close()

    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(chunks)
    return output.getvalue()
