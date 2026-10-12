from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TurnEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["start", "frame", "end", "overflow"]
    pcm: bytes | None = None
    elapsed_ms: int = 0


class TurnController:
    def __init__(
        self,
        vad,
        *,
        frame_ms: int = 20,
        pre_roll_ms: int = 200,
        speech_start_ms: int = 200,
        silence_end_ms: int = 700,
        max_turn_ms: int = 30_000,
    ) -> None:
        self.vad = vad
        self.frame_ms = frame_ms
        self.pre_roll_frames = max(1, pre_roll_ms // frame_ms)
        self.start_frames = max(1, speech_start_ms // frame_ms)
        self.end_frames = max(1, silence_end_ms // frame_ms)
        self.max_turn_ms = max_turn_ms
        self.pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self.pending_speech: list[bytes] = []
        self.active = False
        self.silence_frames = 0
        self.active_elapsed_ms = 0

    def feed(self, frame: bytes) -> list[TurnEvent]:
        speech = self.vad.is_speech(frame)
        if not self.active:
            if speech:
                self.pending_speech.append(frame)
                if len(self.pending_speech) < self.start_frames:
                    return []
                self.active = True
                self.active_elapsed_ms = len(self.pending_speech) * self.frame_ms
                events = [TurnEvent(kind="start", elapsed_ms=self.active_elapsed_ms)]
                events.extend(TurnEvent(kind="frame", pcm=item) for item in self.pre_roll)
                events.extend(TurnEvent(kind="frame", pcm=item) for item in self.pending_speech)
                self.pre_roll.clear()
                self.pending_speech.clear()
                return events
            if self.pending_speech:
                self.pre_roll.extend(self.pending_speech)
                self.pending_speech.clear()
            self.pre_roll.append(frame)
            return []

        self.active_elapsed_ms += self.frame_ms
        self.silence_frames = 0 if speech else self.silence_frames + 1
        events = [TurnEvent(kind="frame", pcm=frame)]
        if self.active_elapsed_ms >= self.max_turn_ms:
            events.append(TurnEvent(kind="end", elapsed_ms=self.max_turn_ms))
            self._reset()
        elif self.silence_frames >= self.end_frames:
            events.append(
                TurnEvent(
                    kind="end", elapsed_ms=self.silence_frames * self.frame_ms
                )
            )
            self._reset()
        return events

    def finish(self) -> list[TurnEvent]:
        if not self.active:
            self.pre_roll.clear()
            self.pending_speech.clear()
            return []
        elapsed = self.silence_frames * self.frame_ms
        self._reset()
        return [TurnEvent(kind="end", elapsed_ms=elapsed)]

    def overflow(self) -> list[TurnEvent]:
        self._reset()
        return [TurnEvent(kind="overflow")]

    def _reset(self) -> None:
        self.active = False
        self.silence_frames = 0
        self.active_elapsed_ms = 0
        self.pre_roll.clear()
        self.pending_speech.clear()
