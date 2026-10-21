"""Minimal async client for the official Threads Graph API (graph.threads.com)."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

BASE_URL = "https://graph.threads.com/v1.0"
POST_FIELDS = (
    "id,text,media_type,permalink,timestamp,username,has_replies,is_quote_post,is_reply,"
    "link_attachment_url,topic_tag,is_verified"
)
MIN_POST_FIELDS = "id,text,media_type,permalink,timestamp,username"
EARLIEST_SINCE = 1688540400  # Threads API rejects anything older

SETUP_HINT = (
    "Set THREADS_ACCESS_TOKEN (long-lived Threads user token). Keyword search over PUBLIC posts needs the "
    "threads_keyword_search permission approved in App Review; without it Meta only searches your own posts. "
    "profile_lookup/profile_posts need threads_profile_discovery (advanced access). See README.md."
)


class ThreadsAPIError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class ThreadsClient:
    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 4,
        cache_ttl: float = 3600,
    ):
        self._token = token
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30, transport=transport)
        self._sem = asyncio.Semaphore(concurrency)
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._cache_ttl = cache_ttl
        self._post_fields = os.environ.get("THREADS_POST_FIELDS", POST_FIELDS)
        self.queries_sent = 0

    @classmethod
    def from_env(cls) -> "ThreadsClient":
        token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
        if not token:
            raise ThreadsAPIError("THREADS_ACCESS_TOKEN is not set. " + SETUP_HINT)
        return cls(token, base_url=os.environ.get("THREADS_API_BASE", BASE_URL))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        key = (path, tuple(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._cache_ttl:
            return hit[1]
        async with self._sem:
            for attempt in range(3):
                self.queries_sent += 1
                resp = await self._http.get(path, params={**params, "access_token": self._token})
                if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                break
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            msg = err.get("message") or resp.text[:300]
            code = err.get("code")
            hint = ""
            if code in (10, 200) or resp.status_code == 403 or "permission" in msg.lower():
                hint = " -> " + SETUP_HINT
            elif code == 190:
                hint = " -> token expired/invalid: refresh the long-lived token."
            raise ThreadsAPIError(f"Threads API {resp.status_code} on {path}: {msg}{hint}", code, resp.status_code)
        self._cache[key] = (time.monotonic(), body)
        return body

    async def keyword_search(
        self,
        q: str,
        search_type: str = "TOP",
        search_mode: str = "KEYWORD",
        media_type: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        author_username: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "q": q,
            "search_type": search_type.upper(),
            "search_mode": search_mode.upper(),
            "media_type": media_type.upper() if media_type else None,
            "since": max(since, EARLIEST_SINCE) if since else None,
            "until": until,
            "limit": max(1, min(limit, 100)),
            "author_username": author_username.lstrip("@") if author_username else None,
        }
        try:
            body = await self._get("/keyword_search", {**params, "fields": self._post_fields})
        except ThreadsAPIError as e:
            # Older/limited apps can reject optional fields; retry once with the bare minimum.
            if e.code != 100 or self._post_fields == MIN_POST_FIELDS:
                raise
            self._post_fields = MIN_POST_FIELDS
            body = await self._get("/keyword_search", {**params, "fields": self._post_fields})
        return body.get("data", [])

    async def profile_lookup(self, username: str) -> dict[str, Any]:
        return await self._get("/profile_lookup", {"username": username.lstrip("@")})

    async def profile_posts(self, username: str, limit: int = 25) -> list[dict[str, Any]]:
        body = await self._get(
            "/profile_posts",
            {"username": username.lstrip("@"), "fields": self._post_fields, "limit": max(1, min(limit, 100))},
        )
        return body.get("data", [])
