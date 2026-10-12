from typing import Any

import httpx


class ManagementClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=30)

    def get(self, path: str, params: dict[str, Any] | None = None):
        response = self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, **kwargs: Any):
        response = self.client.post(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def post_bytes(self, path: str, **kwargs: Any) -> bytes:
        response = self.client.post(path, **kwargs)
        response.raise_for_status()
        return response.content

    def put(self, path: str, **kwargs: Any):
        response = self.client.put(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def delete(self, path: str):
        response = self.client.delete(path)
        response.raise_for_status()

    def close(self) -> None:
        self.client.close()
