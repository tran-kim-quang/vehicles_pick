from dataclasses import dataclass


@dataclass(slots=True)
class SpeechError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
