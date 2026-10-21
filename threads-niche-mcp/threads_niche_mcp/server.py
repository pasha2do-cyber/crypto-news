"""MCP server: finds niches on Threads where people already post proof of making money."""

from __future__ import annotations

import asyncio
import csv
import functools
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp.exceptions import ToolError

from .analyzer import analyze_post, normalize_post, rank_niches, evidence_row
from .taxonomy import INCOME_QUERIES, NICHES
from .threads_api import ThreadsAPIError, ThreadsClient

Market = Literal["us", "cis", "both"]

mcp = MCPServer(
    "threads-niche-finder",
    instructions=(
        "Finds profitable niches on Threads by searching for posts where people show income "
        "(\"$8k/mo\", \"вышла на 300к в месяц\"), tagging them by niche and scoring niches by how many "
        "different people prove money there. Typical flow: scan_profitable_niches -> niche_deep_dive on the "
        "top 3 -> check_creator on the strongest authors. Income numbers are self-reported claims: always "
        "show evidence permalinks and say so. If the API is not configured, use analyze_posts on an export."
    ),
)

_client: ThreadsClient | None = None


def _get_client() -> ThreadsClient:
    global _client
    if _client is None:
        _client = ThreadsClient.from_env()
    return _client


def _user_errors(fn):
    """Surface setup/input problems to the model verbatim (the SDK hides unexpected exception text)."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (ThreadsAPIError, ValueError, OSError) as e:
            raise ToolError(str(e)) from e

    return wrapper


def _markets(market: Market) -> list[str]:
    return ["us", "cis"] if market == "both" else [market]


async def _run_queries(
    queries: list[str], days: int | None, limit: int, search_type: str = "TOP"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client = _get_client()
    since = int(time.time() - days * 86400) if days else None

    async def one(q: str) -> tuple[str, list[dict[str, Any]] | ThreadsAPIError]:
        try:
            return q, await client.keyword_search(q, search_type=search_type, since=since, limit=limit)
        except ThreadsAPIError as e:
            return q, e

    posts, stats = [], []
    for q, res in await asyncio.gather(*(one(q) for q in queries)):
        if isinstance(res, ThreadsAPIError):
            stats.append({"query": q, "results": 0, "error": str(res)})
            continue
        stats.append({"query": q, "results": len(res)})
        posts += [analyze_post(normalize_post(raw, rank=i, query=q)) for i, raw in enumerate(res)]
    if stats and all("error" in s for s in stats):
        raise ThreadsAPIError(stats[0]["error"])
    return posts, stats


def _warnings(stats: list[dict[str, Any]], posts: list[dict[str, Any]]) -> list[str]:
    w = []
    if stats and sum(s["results"] for s in stats) == 0:
        w.append("All searches returned 0 posts. Most likely threads_keyword_search is not approved yet, "
                 "so Meta only searches your own posts. See README -> 'Доступ к API'.")
    elif posts and len({p.get("username") for p in posts}) <= 1:
        w.append("All results come from a single account - the app probably only has access to your own posts.")
    return w


@mcp.tool()
@_user_errors
async def scan_profitable_niches(
    market: Market = "both",
    days: int = 30,
    per_query_limit: int = 50,
    top_n: int = 10,
    search_type: Literal["TOP", "RECENT"] = "TOP",
    extra_queries: list[str] | None = None,
) -> dict[str, Any]:
    """Search Threads for income-proof posts and return the top niches (1..top_n) per market.

    market: "us" (English Threads), "cis" (RU/UA/KZ Threads) or "both" (ranked separately).
    days: how far back to look. per_query_limit: posts per search query (max 100).
    extra_queries: your own phrases, e.g. ["framer template sales", "продажи шаблонов"].
    Uses ~16 search queries per market out of Meta's 2,200/24h quota.
    """
    queries = [q for m in _markets(market) for q in INCOME_QUERIES[m]] + list(extra_queries or [])
    posts, stats = await _run_queries(queries, days, per_query_limit, search_type)
    result: dict[str, Any] = {
        m: rank_niches(posts, market=m, top_n=top_n) for m in _markets(market)
    }
    result["queries"] = stats
    result["warnings"] = _warnings(stats, posts)
    result["next_step"] = "Call niche_deep_dive(niche=...) for the top 3 niches, then check_creator on top authors."
    return result


@mcp.tool()
@_user_errors
async def niche_deep_dive(niche: str, market: Market = "both", days: int = 60, limit: int = 50) -> dict[str, Any]:
    """Zoom into one niche: who earns, what they sell, at what price, through which channels.

    niche: a key from list_niches (e.g. "ai_automation", "digital_products", "esoteric_astrology").
    """
    if niche not in NICHES:
        raise ValueError(f"Unknown niche '{niche}'. Use one of: {', '.join(NICHES)}")
    kws = NICHES[niche]["keywords"]
    queries = []
    for m in _markets(market):
        local = [k for k in kws if (k.isascii() if m == "us" else not k.isascii())][:5]
        income_word = "income" if m == "us" else "доход"
        queries += local + [f"{k} {income_word}" for k in local[:3]]
    posts, stats = await _run_queries(queries, days, limit)
    posts = [p for p in posts if niche in p["niches"] and (market == "both" or p["market"] == market)]

    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in posts:
        if p.get("username"):
            by_author[p["username"]].append(p)
    authors = sorted(
        by_author.items(),
        key=lambda kv: (-sum(bool(p["income_claims"]) for p in kv[1]), -len(kv[1])),
    )
    prices = sorted(c["usd"] for p in posts for c in p["price_points"])
    return {
        "niche": niche,
        "label": NICHES[niche]["label"],
        "posts": len(posts),
        "summary": next((r for r in rank_niches(posts, market=market, top_n=len(NICHES))["niches"]
                         if r["niche"] == niche), None),
        "top_authors": [
            {
                "username": u,
                "posts": len(ps),
                "income_posts": sum(bool(p["income_claims"]) for p in ps),
                "best_claim": max((c["raw"] for p in ps for c in p["income_claims"]), key=len, default=None),
                "sales_channels": sorted({d for p in ps for d in p["sales_domains"]}),
                "markets": sorted({p["market"] for p in ps}),
            }
            for u, ps in authors[:15]
        ],
        "price_points_usd": {
            "count": len(prices),
            "min": round(prices[0]) if prices else None,
            "median": round(prices[len(prices) // 2]) if prices else None,
            "max": round(prices[-1]) if prices else None,
        },
        "sales_channels": Counter(d for p in posts for d in p["sales_domains"]).most_common(10),
        "income_evidence": [evidence_row(p) for p in posts if p["income_claims"]][:10],
        "offer_examples": [evidence_row(p) for p in posts if p["price_points"] or p["sales_domains"]][:8],
        "queries": stats,
        "warnings": _warnings(stats, posts),
    }


@mcp.tool()
@_user_errors
async def threads_search(
    query: str,
    search_type: Literal["TOP", "RECENT"] = "TOP",
    days: int | None = None,
    limit: int = 25,
    media_type: Literal["TEXT", "IMAGE", "VIDEO"] | None = None,
    author_username: str | None = None,
    topic_tag: bool = False,
) -> dict[str, Any]:
    """Raw Threads keyword (or topic-tag) search, with money/niche signals extracted for each post."""
    client = _get_client()
    raw = await client.keyword_search(
        query,
        search_type=search_type,
        search_mode="TAG" if topic_tag else "KEYWORD",
        media_type=media_type,
        since=int(time.time() - days * 86400) if days else None,
        limit=limit,
        author_username=author_username,
    )
    posts = [analyze_post(normalize_post(r, rank=i, query=query)) for i, r in enumerate(raw)]
    return {
        "query": query,
        "count": len(posts),
        "posts": [
            {
                "username": p["username"],
                "permalink": p["permalink"],
                "timestamp": p["timestamp"],
                "market": p["market"],
                "niches": p["niches"],
                "income_claims": p["income_claims"],
                "price_points": p["price_points"],
                "sales_domains": p["sales_domains"],
                "text": p["text"],
            }
            for p in posts
        ],
    }


@mcp.tool()
@_user_errors
async def check_creator(username: str, posts_limit: int = 25) -> dict[str, Any]:
    """Size up one creator: followers, 7-day reach, and money claims in their recent posts.

    Needs threads_profile_discovery (advanced access); otherwise returns the API error with a hint.
    """
    client = _get_client()
    out: dict[str, Any] = {"username": username.lstrip("@")}
    try:
        prof = await client.profile_lookup(username)
        out["profile"] = prof
        views, likes = prof.get("views_count"), prof.get("likes_count")
        if views:
            out["engagement_7d"] = round((likes or 0) / views, 4)
    except ThreadsAPIError as e:
        out["profile_error"] = str(e)
    try:
        posts = [analyze_post(normalize_post(r)) for r in await client.profile_posts(username, posts_limit)]
        out["recent_posts"] = len(posts)
        out["niches"] = Counter(n for p in posts for n in p["niches"]).most_common(3)
        out["income_evidence"] = [evidence_row(p) for p in posts if p["income_claims"]][:8]
        out["offers"] = [evidence_row(p) for p in posts if p["price_points"] or p["sales_domains"]][:8]
        out["sales_channels"] = sorted({d for p in posts for d in p["sales_domains"]})
    except ThreadsAPIError as e:
        out["posts_error"] = str(e)
    return out


def _load_posts_file(path: str) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    if p.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict):
        data = next((data[k] for k in ("data", "items", "posts", "results") if isinstance(data.get(k), list)), [data])
    return data


@mcp.tool()
@_user_errors
async def analyze_posts(
    posts: list[dict[str, Any]] | None = None,
    file_path: str | None = None,
    market: Market = "both",
    top_n: int = 10,
) -> dict[str, Any]:
    """Rank niches from posts you already have - no Threads token needed.

    Pass `posts` inline or `file_path` to a JSON / JSONL / CSV export (Threads API dump, Apify or any
    scraper). Recognised fields: text|content|caption, username|ownerUsername|author.username,
    permalink|url, timestamp|taken_at|createdAt, like_count|likeCount|likes, link_attachment_url.
    When likes are present they drive the popularity part of the score.
    """
    raw = list(posts or []) + (_load_posts_file(file_path) if file_path else [])
    if not raw:
        raise ValueError("Pass `posts` or `file_path`.")
    analyzed = [analyze_post(normalize_post(r, rank=None)) for r in raw]
    result: dict[str, Any] = {m: rank_niches(analyzed, market=m, top_n=top_n) for m in _markets(market)}
    result["input_posts"] = len(raw)
    return result


@mcp.tool()
def list_niches() -> dict[str, Any]:
    """Niche keys, labels and keyword stems, plus the income-proof seed queries per market."""
    return {
        "niches": {k: {"label": v["label"], "keywords": v["keywords"]} for k, v in NICHES.items()},
        "income_queries": INCOME_QUERIES,
    }


@mcp.prompt()
def niche_report(market: str = "both", days: int = 30) -> str:
    """Full research run: top-10 profitable niches with proof, offers and entry plan."""
    return (
        f"Use the threads-niche-finder tools. 1) scan_profitable_niches(market='{market}', days={days}). "
        "2) niche_deep_dive for the top 3 niches of each market. 3) check_creator for the 2 strongest authors per "
        "niche. Then write a ranked 1-10 list per market: niche, why it pays, typical offer and price, proof "
        "(2 permalinks with claimed numbers, marked as self-reported), sales channel, entry difficulty, and a "
        "first-week plan. Flag niches where proof comes from fewer than 3 different authors."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
