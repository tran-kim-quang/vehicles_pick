import unicodedata


class VehicleCatalog:
    DEFAULT_ALIASES = {
        "xe máy": "bike",
        "xe may": "bike",
        "bike": "bike",
        "ô tô": "car",
        "o to": "car",
        "xe hơi": "car",
        "car": "car",
    }

    def __init__(self, available: list[str] | tuple[str, ...], aliases=None) -> None:
        self.available = tuple(available)
        self.aliases = aliases or self.DEFAULT_ALIASES

    def resolve(self, choice: str) -> str | None:
        normalized = unicodedata.normalize("NFKC", choice).strip().casefold()
        candidate = self.aliases.get(normalized, normalized)
        return candidate if candidate in self.available else None
