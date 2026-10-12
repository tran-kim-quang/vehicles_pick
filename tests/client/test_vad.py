import struct

from order_vehicles.clients.python.vad import EnergyVAD
from order_vehicles.clients.python.turns import TurnController


class MarkerVAD:
    def is_speech(self, pcm: bytes) -> bool:
        return pcm == b"speech"


def test_vad_200ms_start_700ms_end() -> None:
    turns = TurnController(MarkerVAD(), frame_ms=20)
    events = []
    for _ in range(10):
        events.extend(turns.feed(b"speech"))
    assert [event.elapsed_ms for event in events if event.kind == "start"] == [200]

    events = []
    for _ in range(35):
        events.extend(turns.feed(b"silence"))
    assert [event.elapsed_ms for event in events if event.kind == "end"] == [700]


def test_preroll_preserves_first_syllable() -> None:
    turns = TurnController(MarkerVAD(), frame_ms=20)
    for _ in range(5):
        assert turns.feed(b"silence") == []
    events = []
    for _ in range(10):
        events.extend(turns.feed(b"speech"))
    audio = [event.pcm for event in events if event.kind == "frame"]
    assert audio[:5] == [b"silence"] * 5
    assert audio[5:] == [b"speech"] * 10


def test_turn_maximum_30s() -> None:
    turns = TurnController(MarkerVAD(), frame_ms=20, max_turn_ms=30_000)
    events = []
    for _ in range(1500):
        events.extend(turns.feed(b"speech"))
    assert events[-1].kind == "end"
    assert events[-1].elapsed_ms == 30_000


def test_callback_overflow_invalidates_turn() -> None:
    turns = TurnController(MarkerVAD())
    for _ in range(10):
        turns.feed(b"speech")
    events = turns.overflow()
    assert [event.kind for event in events] == ["overflow"]
    assert turns.active is False


def test_energy_vad_reads_signed_little_endian_pcm() -> None:
    quiet = struct.pack("<160h", *([10] * 160))
    loud = struct.pack("<160h", *([2000] * 160))
    vad = EnergyVAD(threshold=500)
    assert not vad.is_speech(quiet)
    assert vad.is_speech(loud)
