"""Telegram alerts. Failures are logged, never fatal."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("pumpdesk.telegram")


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id
        self.enabled = bool(token and chat_id)
        self.last_error = ""
        self._client = httpx.AsyncClient(timeout=10) if self.enabled else None

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            r = await self._client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                        json={"chat_id": self.chat_id, "text": text[:4000],
                                              "disable_web_page_preview": True})
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}: {r.text[:150]}"
                log.warning("telegram: %s", self.last_error)
                return False
            self.last_error = ""
            return True
        except httpx.HTTPError as e:
            self.last_error = f"{type(e).__name__}"
            log.warning("telegram: %s", self.last_error)
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
