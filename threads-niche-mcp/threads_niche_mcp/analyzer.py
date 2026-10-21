"""Turns raw Threads posts into niche-level "someone is already making money here" signals.

Pipeline: normalize post -> detect market (us / cis) -> extract money mentions (amount, currency,
period, income vs price) -> tag niches by keyword stems -> aggregate per niche -> score 0..100.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import textwrap
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .taxonomy import FX_TO_USD, INCOME_CONTEXT, NICHES, PERIODS, PRICE_CONTEXT, SALES_DOMAINS

# ---------------------------------------------------------------- keyword matching

_WORD = r"0-9a-zа-яёіїєґәғқңөұүһ"


def _kw_pattern(kw: str) -> str:
    """Left word boundary always; right boundary only for short ASCII tokens (mrr, arr, ugc...)."""
    body = re.escape(kw.strip())
    left = rf"(?<![{_WORD}])" if kw[:1].isalnum() else ""
    right = rf"(?![{_WORD}])" if kw.isascii() and len(kw.strip()) <= 3 else ""
    return left + body + right


def _compile(words: Iterable[str]) -> re.Pattern[str]:
    return re.compile("|".join(_kw_pattern(w) for w in words), re.IGNORECASE)


_NICHE_RX = {key: _compile(n["keywords"]) for key, n in NICHES.items()}
_INCOME_RX = _compile(INCOME_CONTEXT)
_PRICE_RX = _compile(PRICE_CONTEXT)
_PERIOD_RX = [(mult, _compile(words)) for mult, words in PERIODS]

# ---------------------------------------------------------------- money extraction

_NUM = r"\d{1,3}(?:[ ,.  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_MULT = r"kk|кк|k(?![a-z])|к(?![а-яё])|тыс\.?|тысяч[аи]?|m(?![a-z])|mln|млн\.?|миллион(?:а|ов)?"
_CUR_SYM = r"[$€£₽₴₸]"
_CUR_WORD = (
    r"\$|€|£|₽|₴|₸|usd|dollars?|bucks|eur|euros?|gbp|руб(?:\.|лей|ля|ль)?|р\.|грн|гривен[ья]?|гривн[ия]|"
    r"тенге|тг|долларов|доллара|баксов|евро|byn"
)
_MONEY_RX = re.compile(
    rf"(?P<cur1>{_CUR_SYM})\s?(?P<num1>{_NUM})\s?(?P<mult1>{_MULT})?"
    rf"|(?P<num2>{_NUM})\s?(?P<mult2>{_MULT})?\s?(?P<cur2>{_CUR_WORD})(?![a-zа-яё])",
    re.IGNORECASE,
)
_SYM_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "₽": "RUB", "₴": "UAH", "₸": "KZT"}


def _currency_code(raw: str) -> str:
    r = raw.lower().strip(".")
    if r in _SYM_TO_CODE:
        return _SYM_TO_CODE[r]
    if r.startswith(("usd", "dollar", "buck", "доллар", "бакс")):
        return "USD"
    if r.startswith(("eur", "евро")):
        return "EUR"
    if r.startswith("gbp"):
        return "GBP"
    if r.startswith(("руб", "р")):
        return "RUB"
    if r.startswith(("грн", "гривн", "гривен")):
        return "UAH"
    if r.startswith(("тенге", "тг")):
        return "KZT"
    if r.startswith("byn"):
        return "BYN"
    return "USD"


def _parse_number(s: str) -> float:
    s = s.replace(" ", " ").replace(" ", " ")
    m = re.fullmatch(r"(\d{1,3}(?:[ ,.]\d{3})+)(?:[.,](\d{1,2}))?", s)
    if m:
        return float(re.sub(r"[ ,.]", "", m.group(1)) + "." + (m.group(2) or "0"))
    return float(s.replace(",", "."))


def _multiplier(raw: str | None) -> float:
    if not raw:
        return 1.0
    r = raw.lower()
    if r in ("kk", "кк") or r.startswith(("m", "млн", "миллион")):
        return 1_000_000.0
    return 1_000.0


def _fx() -> dict[str, float]:
    fx = dict(FX_TO_USD)
    try:
        fx.update({k.upper(): float(v) for k, v in json.loads(os.environ.get("THREADS_FX_JSON", "{}")).items()})
    except (ValueError, AttributeError):
        pass
    return fx


def extract_money(text: str, fx: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Find money amounts and label each as income / price / unknown, with a monthly USD estimate."""
    fx = fx or _fx()
    out = []
    for m in _MONEY_RX.finditer(text):
        num, mult, cur = (m["num1"], m["mult1"], m["cur1"]) if m["num1"] else (m["num2"], m["mult2"], m["cur2"])
        try:
            amount = _parse_number(num) * _multiplier(mult)
        except ValueError:
            continue
        code = _currency_code(cur)
        usd = amount * fx.get(code, 1.0)
        if usd < 20:  # "$5 template", "3 руб" - noise for income detection
            continue
        after = text[m.end(): m.end() + 28].lower()
        period_mult = next((mult_ for mult_, rx in _PERIOD_RX if rx.search(after)), None)
        window = text[max(0, m.start() - 60): m.end() + 14]
        kind = "income" if _INCOME_RX.search(window) else "price" if _PRICE_RX.search(window) else "unknown"
        if kind == "unknown" and period_mult is not None:
            kind = "income"  # "$8k/mo" on its own is almost always a result, not a price
        out.append({
            "raw": m.group(0).strip(),
            "amount": round(amount, 2),
            "currency": code,
            "usd": round(usd, 2),
            "kind": kind,
            "period": {1.0: "month", 1 / 12: "year", 30.0: "day", 4.33: "week"}.get(period_mult, "unspecified"),
            # "$3k in a day" is usually a best-day brag, so daily figures are not extrapolated to a month
            "monthly_usd": round(usd * period_mult, 2) if period_mult and period_mult < 30 else None,
        })
    return out


# ---------------------------------------------------------------- per-post analysis

_UA = re.compile(r"[іїєґ]", re.IGNORECASE)
_KZ = re.compile(r"[әғқңөұүһ]", re.IGNORECASE)
_CYR = re.compile(r"[а-яёіїєґәғқңөұүһ]", re.IGNORECASE)
_LAT = re.compile(r"[a-z]", re.IGNORECASE)
_URL = re.compile(r"https?://\S+|\b[\w-]+\.(?:store|me|ai|com|ee|top|ru|io|so)/\S*", re.IGNORECASE)


def detect_market(text: str) -> tuple[str, str]:
    cyr, lat = len(_CYR.findall(text)), len(_LAT.findall(text))
    if cyr and cyr >= lat * 0.6:
        lang = "uk" if _UA.search(text) else "kk" if _KZ.search(text) else "ru"
        return "cis", lang
    return "us", "en"


def tag_niches(text: str, max_tags: int = 2) -> list[str]:
    hits = {key: len(rx.findall(text)) for key, rx in _NICHE_RX.items()}
    ranked = sorted((k for k, v in hits.items() if v), key=lambda k: -hits[k])
    return ranked[:max_tags]


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _to_int(v: Any) -> int | None:
    try:
        return int(float(str(v).replace(",", "").replace(" ", "")))
    except (TypeError, ValueError):
        return None


def _parse_ts(v: Any) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    s = str(v).strip()
    if s.isdigit():
        return _parse_ts(int(s))
    s = s.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def normalize_post(raw: dict[str, Any], rank: int | None = None, query: str | None = None) -> dict[str, Any]:
    """Accepts Threads API media objects and most scraper exports (Apify etc.)."""
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    caption = raw.get("caption")
    caption = caption.get("text") if isinstance(caption, dict) else caption
    text = str(_first(raw, "text", "content", "postText", "post_text", default=caption or "") or "")
    username = _first(raw, "username", "ownerUsername", "authorUsername", "author_username") or _first(
        author, "username", "handle") or _first(user, "username") or (raw.get("author") if isinstance(
            raw.get("author"), str) else None)
    permalink = _first(raw, "permalink", "url", "postUrl", "post_url", "link")
    link = _first(raw, "link_attachment_url", "linkUrl", "external_url")
    return {
        "id": str(_first(raw, "id", "pk", "code", "shortcode", default="")),
        "username": (str(username).lstrip("@") if username else None),
        "text": text,
        "permalink": permalink,
        "timestamp": _parse_ts(_first(raw, "timestamp", "taken_at", "takenAt", "createdAt", "created_at", "date")),
        "is_verified": bool(_first(raw, "is_verified", "isVerified", default=False) or author.get("is_verified")
                            or user.get("is_verified")),
        "link": link,
        "topic_tag": raw.get("topic_tag"),
        "likes": _to_int(_first(raw, "like_count", "likeCount", "likes", "likesCount")),
        "replies": _to_int(_first(raw, "reply_count", "replyCount", "replies", "direct_reply_count")),
        "reposts": _to_int(_first(raw, "repost_count", "repostCount", "reposts")),
        "rank": rank,
        "query": query,
    }


def analyze_post(post: dict[str, Any], fx: dict[str, float] | None = None) -> dict[str, Any]:
    text = post["text"] or ""
    market, lang = detect_market(text)
    money = extract_money(text, fx)
    blob = " ".join(filter(None, [text, post.get("link") or "", post.get("topic_tag") or ""])).lower()
    domains = sorted({d for d in SALES_DOMAINS if d in blob})
    for url in _URL.findall(blob):
        host = urlparse(url if "://" in url else "https://" + url).netloc.removeprefix("www.")
        if host and any(host.endswith(d) or d in host for d in SALES_DOMAINS):
            domains.append(host)
    niche_text = " ".join(filter(None, [text, post.get("topic_tag") or ""]))
    return {
        **post,
        "market": market,
        "lang": lang,
        "money": money,
        "income_claims": [m for m in money if m["kind"] == "income"],
        "price_points": [m for m in money if m["kind"] == "price"],
        "sales_domains": sorted(set(domains)),
        "niches": tag_niches(niche_text),
    }


# ---------------------------------------------------------------- niche aggregation + scoring

def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _snippet(text: str, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def evidence_row(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": p.get("username"),
        "permalink": p.get("permalink"),
        "date": datetime.fromtimestamp(p["timestamp"], timezone.utc).date().isoformat() if p.get("timestamp") else None,
        "claims": [m["raw"] + (f" ({m['period']})" if m["period"] != "unspecified" else "") for m in p["income_claims"]],
        "likes": p.get("likes"),
        "text": _snippet(p.get("text") or ""),
    }


def _popularity(p: dict[str, Any]) -> float:
    if p.get("likes") is not None:
        return _clip01(math.log10(p["likes"] + 1) / 4)  # 10k likes -> 1.0
    if p.get("rank") is not None:
        return 1 / (1 + p["rank"] / 10)  # position in TOP search results
    return 0.3


def rank_niches(
    posts: list[dict[str, Any]],
    market: str = "both",
    top_n: int = 10,
    now: float | None = None,
    evidence_per_niche: int = 3,
) -> dict[str, Any]:
    """Aggregate analyzed posts into a ranked niche list.

    Score (0-100) = 35% proof breadth (distinct authors with income claims, log-scaled, 15 = max)
                  + 25% money size (median claimed monthly USD, log-scaled, $50k = max)
                  + 15% selling infrastructure (share of posts with sales links/domains)
                  + 15% freshness (share of posts from the last 30 days)
                  + 10% popularity (likes if known, else rank in TOP search)
    """
    now = now or time.time()
    seen: set[str] = set()
    uniq = []
    for p in posts:
        key = p.get("id") or p.get("permalink") or (p.get("username"), p.get("text"))
        key = str(key)
        if key in seen:
            continue
        seen.add(key)
        if market in ("us", "cis") and p["market"] != market:
            continue
        uniq.append(p)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unclassified = []
    for p in uniq:
        if not p["niches"]:
            if p["income_claims"]:
                unclassified.append(p)
            continue
        for n in p["niches"]:
            groups[n].append(p)

    rows = []
    for key, ps in groups.items():
        income_posts = [p for p in ps if p["income_claims"]]
        authors_income = {p["username"] for p in income_posts if p.get("username")}
        monthly = [c["monthly_usd"] for p in income_posts for c in p["income_claims"] if c["monthly_usd"]]
        oneoff = [c["usd"] for p in income_posts for c in p["income_claims"] if not c["monthly_usd"]]
        prices = [c["usd"] for p in ps for c in p["price_points"]]
        median_monthly = statistics.median(monthly) if monthly else None
        # one-off totals ("made $12k from my course") count at a discount when no monthly figure exists
        money_basis = median_monthly or (statistics.median(oneoff) / 6 if oneoff else 0.0)
        fresh = sum(1 for p in ps if p.get("timestamp") and now - p["timestamp"] <= 30 * 86400)
        sales = sum(1 for p in ps if p["sales_domains"])
        markets = Counter(p["market"] for p in ps)
        components = {
            "proof": _clip01(math.log1p(len(authors_income)) / math.log1p(15)),
            "money": _clip01(math.log10(money_basis + 1) / math.log10(50_001)),
            "selling": sales / len(ps),
            "fresh": fresh / len(ps) if any(p.get("timestamp") for p in ps) else 0.5,
            "popularity": statistics.mean(_popularity(p) for p in ps),
        }
        score = 100 * (0.35 * components["proof"] + 0.25 * components["money"] + 0.15 * components["selling"]
                       + 0.15 * components["fresh"] + 0.10 * components["popularity"])
        evidence = sorted(income_posts, key=lambda p: (-(max((c["usd"] for c in p["income_claims"]), default=0)),))
        domains = Counter(d for p in ps for d in p["sales_domains"])
        rows.append({
            "niche": key,
            "label": NICHES[key]["label"],
            "score": round(score, 1),
            "components": {k: round(v, 2) for k, v in components.items()},
            "posts": len(ps),
            "authors": len({p["username"] for p in ps if p.get("username")}),
            "authors_with_income_claims": len(authors_income),
            "income_posts": len(income_posts),
            "median_monthly_usd": round(median_monthly) if median_monthly else None,
            "max_claim_usd": round(max(monthly + oneoff)) if monthly + oneoff else None,
            "median_price_usd": round(statistics.median(prices)) if prices else None,
            "markets": dict(markets),
            "top_sales_channels": [d for d, _ in domains.most_common(5)],
            "evidence": [evidence_row(p) for p in evidence[:evidence_per_niche]],
        })

    rows.sort(key=lambda r: (-r["score"], -r["authors_with_income_claims"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {
        "market": market,
        "posts_analyzed": len(uniq),
        "posts_with_income_claims": sum(1 for p in uniq if p["income_claims"]),
        "niches": rows[:top_n],
        "other_niches": [{"niche": r["niche"], "score": r["score"]} for r in rows[top_n:]],
        "unclassified_income_posts": [evidence_row(p) for p in unclassified[:10]],
        "scoring": textwrap.dedent(rank_niches.__doc__.split("\n\n", 1)[1]).strip(),
        "caveats": [
            "Income figures are self-reported claims from posts, not verified revenue.",
            "Currency conversion uses approximate FX (override with THREADS_FX_JSON).",
            "Niche tags are keyword heuristics; read evidence posts and unclassified_income_posts before deciding.",
        ],
    }
