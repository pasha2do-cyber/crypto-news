import pytest

from threads_niche_mcp.analyzer import analyze_post, detect_market, extract_money, normalize_post, rank_niches, tag_niches

FX = {"USD": 1.0, "EUR": 1.0, "RUB": 0.01, "UAH": 0.025, "KZT": 0.002}


@pytest.mark.parametrize(
    "text,usd,currency,period,kind",
    [
        ("Hit $12k/mo with my n8n automation agency", 12_000, "USD", "month", "income"),
        ("made $1,250 this week from templates", 1_250, "USD", "week", "income"),
        ("Grew to $4.5K MRR", 4_500, "USD", "month", "income"),
        ("вышла на 300к ₽ в месяц на консультациях", 3_000, "RUB", "month", "income"),
        ("заработала 1,5 млн рублей за запуск курса", 15_000, "RUB", "unspecified", "income"),
        ("доход 50 000 грн за месяц", 1_250, "UAH", "month", "income"),
        ("гайд стоит 2 990 ₽", 29.9, "RUB", "unspecified", "price"),
        ("Notion template for only $49", 49, "USD", "unspecified", "price"),
    ],
)
def test_extract_money(text, usd, currency, period, kind):
    m = extract_money(text, FX)
    assert len(m) == 1, m
    assert m[0]["usd"] == pytest.approx(usd)
    assert m[0]["currency"] == currency
    assert m[0]["period"] == period
    assert m[0]["kind"] == kind


def test_extract_money_ignores_noise():
    assert extract_money("I have 10k followers and 3 cats", FX) == []
    assert extract_money("$5 coffee", FX) == []  # below $20 floor


def test_detect_market():
    assert detect_market("Hit $12k/mo with my agency") == ("us", "en")
    assert detect_market("Вышла на доход 300к") == ("cis", "ru")
    assert detect_market("Мій дохід зріс у тричі") == ("cis", "uk")


def test_tag_niches_word_boundaries():
    assert tag_niches("We hit $10k MRR on our micro saas")[0] == "saas_indie"
    assert "saas_indie" not in tag_niches("I will arrive tomorrow with a carry-on")
    assert tag_niches("Делаю сайты на Framer и Тильде")[0] == "design_nocode"
    assert tag_niches("Разбор натальной карты, астролог")[0] == "esoteric_astrology"


def test_normalize_scraper_export():
    p = normalize_post({
        "postText": "Sold 300 Notion templates, $9k this month",
        "author": {"username": "@maker"},
        "url": "https://www.threads.com/@maker/post/abc",
        "likeCount": "1,204",
        "createdAt": "2026-10-01T10:00:00Z",
    })
    assert p["username"] == "maker"
    assert p["likes"] == 1204
    assert p["timestamp"] == pytest.approx(1790848800)


def _post(i, user, text, ts=1_790_000_000, link=None):
    return analyze_post(normalize_post(
        {"id": str(i), "username": user, "text": text, "timestamp": ts, "link_attachment_url": link}, rank=i
    ), FX)


def test_rank_niches_prefers_broad_proof():
    now = 1_790_100_000
    posts = [
        _post(1, "a", "My AI automation agency hit $15k/mo", now - 86400),
        _post(2, "b", "n8n workflows for dentists: $6k a month now", now - 86400),
        _post(3, "c", "Closed $8k/month retainer building ai agents", now - 86400),
        _post(4, "d", "Notion template sales: $2k this month", now - 86400, "https://stan.store/d"),
        _post(5, "d", "Notion template drop, $3k this month", now - 86400),
        _post(6, "e", "заработала 200к ₽ в месяц на консультациях психолога", now - 86400),
        _post(1, "a", "duplicate id must be ignored $99k/mo ai agents", now - 86400),
    ]
    us = rank_niches(posts, market="us", now=now)
    assert us["posts_analyzed"] == 5
    assert us["niches"][0]["niche"] == "ai_automation"
    assert us["niches"][0]["authors_with_income_claims"] == 3
    assert us["niches"][0]["median_monthly_usd"] == 8000
    dp = next(r for r in us["niches"] if r["niche"] == "digital_products")
    assert dp["top_sales_channels"] == ["stan.store"]
    cis = rank_niches(posts, market="cis", now=now)
    assert {r["niche"] for r in cis["niches"]} == {"coaching_consulting", "psychology_relationships"}
    assert 0 <= us["niches"][0]["score"] <= 100
