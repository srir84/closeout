"""A small client for Mailpit's API (simulators, tests, privacy erase)."""

from __future__ import annotations

from typing import Any

import httpx


class Mailpit:
    def __init__(self, api_url: str) -> None:
        self.base = api_url.rstrip("/")

    def messages(self, limit: int = 500) -> list[dict[str, Any]]:
        r = httpx.get(f"{self.base}/api/v1/messages", params={"limit": limit}, timeout=10)
        r.raise_for_status()
        return r.json().get("messages") or []

    def search(self, query: str, limit: int = 500) -> list[dict[str, Any]]:
        r = httpx.get(f"{self.base}/api/v1/search", params={"query": query, "limit": limit}, timeout=10)
        r.raise_for_status()
        return r.json().get("messages") or []

    def message(self, mid: str) -> dict[str, Any]:
        r = httpx.get(f"{self.base}/api/v1/message/{mid}", timeout=10)
        r.raise_for_status()
        return r.json()

    def headers(self, mid: str) -> dict[str, list[str]]:
        r = httpx.get(f"{self.base}/api/v1/message/{mid}/headers", timeout=10)
        r.raise_for_status()
        return r.json()

    def delete(self, ids: list[str] | None = None) -> None:
        body = {"IDs": ids} if ids else {}
        r = httpx.request("DELETE", f"{self.base}/api/v1/messages", json=body, timeout=10)
        r.raise_for_status()

    def delete_search(self, query: str) -> None:
        r = httpx.delete(f"{self.base}/api/v1/search", params={"query": query}, timeout=10)
        r.raise_for_status()
