import math
import struct


class EnergyVAD:
    """Small deterministic RMS VAD for signed little-endian PCM16 frames."""

    def __init__(self, threshold: float = 500.0) -> None:
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        self.threshold = threshold

    def is_speech(self, pcm: bytes) -> bool:
        if not pcm:
            return False
        if len(pcm) % 2:
            raise ValueError("PCM16 frame must contain an even number of bytes")
        samples = struct.iter_unpack("<h", pcm)
        square_sum = 0
        count = 0
        for (sample,) in samples:
            square_sum += sample * sample
            count += 1
        return math.sqrt(square_sum / count) >= self.threshold
