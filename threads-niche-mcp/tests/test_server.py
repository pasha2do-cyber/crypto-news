"""End-to-end: MCP tool calls against a mocked Threads Graph API."""

import json
import time

import httpx
import pytest

from threads_niche_mcp import server
from threads_niche_mcp.threads_api import ThreadsClient

NOW = int(time.time())

CANNED = {
    "MRR": [
        {"id": "1", "username": "indie_kate", "text": "Our micro SaaS just crossed $7.2k MRR", "timestamp": NOW - 3600,
         "permalink": "https://www.threads.com/@indie_kate/post/1"},
    ],
    "first sale": [
        {"id": "2", "username": "ops_mike", "text": "AI automation agency update: $11k/mo from 4 clients (n8n)",
         "timestamp": NOW - 7200, "permalink": "https://www.threads.com/@ops_mike/post/2"},
        {"id": "3", "username": "flowbuilder", "text": "Built ai agents for a clinic, now $5k a month retainer",
         "timestamp": NOW - 7200, "permalink": "https://www.threads.com/@flowbuilder/post/3"},
    ],
    "заработала": [
        {"id": "4", "username": "astro_lena", "text": "Астролог: вышла на 400к ₽ в месяц на разборах натальной карты",
         "timestamp": NOW - 3600, "permalink": "https://www.threads.com/@astro_lena/post/4",
         "link_attachment_url": "https://t.me/astro_lena"},
    ],
}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["access_token"] == "test-token"
    if request.url.path.endswith("/keyword_search"):
        assert "fields" in request.url.params
        return httpx.Response(200, json={"data": CANNED.get(request.url.params["q"], [])})
    if request.url.path.endswith("/profile_lookup"):
        return httpx.Response(403, json={"error": {"message": "Application does not have permission", "code": 10}})
    if request.url.path.endswith("/profile_posts"):
        return httpx.Response(200, json={"data": CANNED["first sale"][:1]})
    return httpx.Response(404, json={"error": {"message": "nope", "code": 100}})


@pytest.fixture(autouse=True)
def mocked_client():
    server._client = ThreadsClient("test-token", transport=httpx.MockTransport(handler))
    yield
    server._client = None


def _payload(result):
    if getattr(result, "structured_content", None) or getattr(result, "structuredContent", None):
        return getattr(result, "structured_content", None) or result.structuredContent
    return json.loads(result.content[0].text)


async def _call(name, args):
    from mcp import Client

    async with Client(server.mcp) as client:
        return await client.call_tool(name, args)


@pytest.mark.anyio
async def test_tools_listed():
    from mcp import Client

    async with Client(server.mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"scan_profitable_niches", "niche_deep_dive", "threads_search", "check_creator", "analyze_posts",
            "list_niches"} <= names


@pytest.mark.anyio
async def test_scan_profitable_niches():
    res = _payload(await _call("scan_profitable_niches", {"market": "both", "days": 30}))
    us = {r["niche"]: r for r in res["us"]["niches"]}
    assert us["ai_automation"]["authors_with_income_claims"] == 2
    assert us["ai_automation"]["rank"] == 1
    assert "saas_indie" in us
    cis = res["cis"]["niches"]
    assert cis[0]["niche"] == "esoteric_astrology"
    assert cis[0]["top_sales_channels"] == ["t.me"]
    assert len(res["queries"]) == 42


@pytest.mark.anyio
async def test_check_creator_degrades_gracefully():
    res = _payload(await _call("check_creator", {"username": "@ops_mike"}))
    assert "threads_profile_discovery" in res["profile_error"]
    assert res["income_evidence"][0]["username"] == "ops_mike"


@pytest.mark.anyio
async def test_analyze_posts_offline(tmp_path):
    f = tmp_path / "export.json"
    f.write_text(json.dumps({"items": [
        {"postText": "UGC creator here: $3k this month from 6 brand deals", "ownerUsername": "ugc_amy", "likes": 900},
        {"postText": "UGC: made $4k/mo, brand partnership with skincare", "ownerUsername": "ugc_jo", "likes": 120},
    ]}))
    server._client = None  # no token needed
    res = _payload(await _call("analyze_posts", {"file_path": str(f), "market": "us"}))
    assert res["us"]["niches"][0]["niche"] == "ugc_creators"
    assert res["us"]["niches"][0]["authors_with_income_claims"] == 2


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_missing_token_message_reaches_model(monkeypatch):
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    server._client = None
    res = await _call("scan_profitable_niches", {"market": "us"})
    assert res.is_error if hasattr(res, "is_error") else res.isError
    assert "THREADS_ACCESS_TOKEN is not set" in res.content[0].text
