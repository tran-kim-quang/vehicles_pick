import math
import threading
from dataclasses import dataclass, field


STAGES = {
    "input.end",
    "stt.final",
    "llm.first_text",
    "tts.first_audio",
    "audio.received",
    "playback.first_audio",
}


@dataclass
class TurnTiming:
    clock_domain: str
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, stage: str, at: float) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown timing stage: {stage}")
        if stage in self.marks:
            return
        self.marks[stage] = at

    def metrics(self) -> dict[str, float]:
        pairs = {
            "stt_seconds": ("input.end", "stt.final"),
            "llm_seconds": ("stt.final", "llm.first_text"),
            "tts_seconds": ("llm.first_text", "tts.first_audio"),
            "end_to_first_audio_seconds": ("input.end", "tts.first_audio"),
            "playback_delay_seconds": ("audio.received", "playback.first_audio"),
        }
        result = {}
        for name, (start, end) in pairs.items():
            if start in self.marks and end in self.marks:
                value = self.marks[end] - self.marks[start]
                if value < 0:
                    raise ValueError(
                        f"Timing stage {end} precedes {start} in {self.clock_domain}"
                    )
                result[name] = value
        return result


class TimingRecorder:
    SAFE_METADATA = {"model", "engine", "hardware", "profile_revision"}

    def __init__(self) -> None:
        self._records: list[tuple[str, dict[str, float], dict]] = []
        self._lock = threading.Lock()

    def record(self, timing: TurnTiming, metadata: dict) -> None:
        safe = {key: value for key, value in metadata.items() if key in self.SAFE_METADATA}
        with self._lock:
            self._records.append((timing.clock_domain, timing.metrics(), safe))

    def summary(self) -> dict:
        grouped: dict[str, dict[str, list[float]]] = {}
        metadata_by_domain: dict[str, list[dict]] = {}
        with self._lock:
            records = list(self._records)
        for domain, metrics, metadata in records:
            for name, value in metrics.items():
                grouped.setdefault(domain, {}).setdefault(name, []).append(value)
            if metadata and metadata not in metadata_by_domain.setdefault(domain, []):
                metadata_by_domain[domain].append(metadata)
        result = {
            domain: {
                name: {
                    "count": len(values),
                    "p50": self._percentile(values, 0.50),
                    "p95": self._percentile(values, 0.95),
                }
                for name, values in metrics.items()
            }
            for domain, metrics in grouped.items()
        }
        for domain, metadata in metadata_by_domain.items():
            result.setdefault(domain, {})["metadata"] = metadata
        return result

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        ordered = sorted(values)
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]
