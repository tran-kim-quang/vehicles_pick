import re


class SentenceBuffer:
    ABBREVIATIONS = {"e.g.", "i.e.", "v.d.", "tp."}

    def __init__(self) -> None:
        self.buffer = ""

    def push(self, delta: str) -> list[str]:
        self.buffer += delta
        result = []
        start = 0
        for match in re.finditer(r"[.!?](?=\s|$)", self.buffer):
            end = match.end()
            candidate = self.buffer[start:end].strip()
            token = candidate.split()[-1].casefold() if candidate else ""
            if token in self.ABBREVIATIONS or re.search(r"\d\.\d$", candidate):
                continue
            result.append(candidate)
            start = end
        self.buffer = self.buffer[start:].lstrip()
        return result

    def flush(self) -> list[str]:
        value = self.buffer.strip()
        self.buffer = ""
        return [value] if value else []
