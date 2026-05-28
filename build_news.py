#!/usr/bin/env python3
"""
build_news.py — Crypto News Impact backend (cloud edition)
==========================================================
Runs once per invocation (designed for GitHub Actions cron, every 5 min):
  1. Fetches top-100 assets from CoinGecko (cached daily)
  2. Pulls 15 RSS feeds
  3. Deduplicates, maps news -> assets, scores with enhanced heuristics
     (negation handling, headline weight, theme dedup, confirmation counter)
  4. Optionally re-scores top items with Claude Haiku (if ANTHROPIC_API_KEY set)
  5. Writes public/news.json  (served via GitHub Pages / any static host)

Pure stdlib except optional Claude call (urllib). No pip installs needed.
"""

import json
import re
import time
import html
import os
import sys
import hashlib
import urllib.request
import urllib.parse
import urllib.error
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

OUT_DIR = Path(__file__).parent / 'public'
NEWS_OUT = OUT_DIR / 'news.json'
ASSETS_CACHE = Path(__file__).parent / '.assets_cache.json'
SCORE_CACHE = Path(__file__).parent / '.score_cache.json'  # persistent LLM scores
FETCH_TIMEOUT = 20
FETCH_RETRIES = 2              # retry failed feeds N times (#6)
MAX_NEWS_OUTPUT = 300          # cap items in news.json
ASSETS_REFRESH_SEC = 86400     # refresh top-100 once a day
NEWS_MAX_AGE_HOURS = 72        # drop news older than this

# --- Claude / scoring config ---
CLAUDE_MODEL = 'claude-haiku-4-5-20251001'
CLAUDE_MAX_TOKENS = 600        # ceiling for the JSON response
CLAUDE_MAX_PER_RUN = 30        # safety cap: don't score more than N new items per run
IMPACT_THRESHOLD = 3.0         # items below this are "dust" -> excluded from news.json
SCORE_CACHE_MAX_AGE = 72 * 3600  # forget cached scores older than this

# Source weighting (#17): low-quality / SEO-heavy feeds get penalized in candidate ranking
LOW_QUALITY_SOURCES = {'99Bitcoins', 'CoinCodex', 'Bitcoinist'}

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')

RSS_FEEDS = [
    ('Cointelegraph',  'https://cointelegraph.com/rss'),
    ('CoinDesk',       'https://www.coindesk.com/arc/outboundfeeds/rss/'),
    ('Decrypt',        'https://decrypt.co/feed'),
    ('CryptoSlate',    'https://cryptoslate.com/feed/'),
    ('Bitcoin.com',    'https://news.bitcoin.com/feed/'),
    ('CryptoBriefing', 'https://cryptobriefing.com/feed/'),
    ('BeInCrypto',     'https://beincrypto.com/feed/'),
    ('NewsBTC',        'https://www.newsbtc.com/feed/'),
    ('CryptoPotato',   'https://cryptopotato.com/feed/'),
    ('U.Today',        'https://u.today/rss'),
    ('AMBCrypto',      'https://ambcrypto.com/feed/'),
    ('CoinGape',       'https://coingape.com/feed/'),
    # Additional sources (more coverage)
    ('The Defiant',    'https://thedefiant.io/api/feed'),
    ('CryptoNews',     'https://cryptonews.com/news/feed/'),
    ('Crypto.news',    'https://crypto.news/feed/'),
    ('Bitcoinist',     'https://bitcoinist.com/feed/'),
    ('99Bitcoins',     'https://99bitcoins.com/feed/'),
    ('CoinJournal',    'https://coinjournal.net/feed/'),
    ('DLNews',         'https://www.dlnews.com/arc/outboundfeeds/rss/'),
    ('Blockworks',     'https://blockworks.co/feed'),
    ('CoinCodex',      'https://coincodex.com/en/resources/feed/news/'),
    ('FinanceMagnates','https://www.financemagnates.com/cryptocurrency/feed/'),
    # Google News by topic — generated, very stable
    ('Google: Crypto', 'https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+OR+ethereum&hl=en-US&gl=US&ceid=US:en'),
    ('Google: DeFi',   'https://news.google.com/rss/search?q=defi+OR+%22smart+contract%22+crypto&hl=en-US&gl=US&ceid=US:en'),
    ('Google: Reg',    'https://news.google.com/rss/search?q=SEC+OR+regulation+crypto&hl=en-US&gl=US&ceid=US:en'),
    ('Google: ETF',    'https://news.google.com/rss/search?q=crypto+ETF+OR+bitcoin+ETF&hl=en-US&gl=US&ceid=US:en'),
]

# ============================================================
# TOP-100 ASSETS (CoinGecko)
# ============================================================

# Hardcoded fallback if CoinGecko is unreachable — covers majors.
FALLBACK_ASSETS = [
    {'sym': 'BTC', 'name': 'Bitcoin', 'names': ['bitcoin', 'btc'], 'logo': ''},
    {'sym': 'ETH', 'name': 'Ethereum', 'names': ['ethereum', 'ether', 'eth'], 'logo': ''},
    {'sym': 'SOL', 'name': 'Solana', 'names': ['solana', 'sol'], 'logo': ''},
    {'sym': 'XRP', 'name': 'XRP', 'names': ['xrp', 'ripple'], 'logo': ''},
    {'sym': 'NEAR', 'name': 'NEAR Protocol', 'names': ['near protocol', 'near'], 'logo': ''},
]

# Common English words that collide with tickers — require $TICKER or name match for these.
AMBIGUOUS_SYMBOLS = {
    'TON', 'SUI', 'ZK', 'CP', 'ID', 'OP', 'ME', 'GO', 'AI', 'IO', 'ETH', 'ARB',
    'APE', 'GAS', 'SAND', 'MASK', 'JOE', 'WIN', 'ANT', 'FUN', 'GMT', 'RARE', 'POND',
}


# ============================================================
# TRANSLATION (free Google Translate endpoint, no key)
# ============================================================

_translate_cache = {}  # title -> ru translation (in-memory per run)


def translate_to_ru(text: str) -> str:
    """Translate text to Russian via the free Google Translate endpoint.
    Returns original text on failure. Caches within a run."""
    if not text:
        return text
    if text in _translate_cache:
        return _translate_cache[text]
    try:
        params = urllib.parse.urlencode({
            'client': 'gtx',
            'sl': 'auto',
            'tl': 'ru',
            'dt': 't',
            'q': text,
        })
        url = f'https://translate.googleapis.com/translate_a/single?{params}'
        raw = fetch_url(url, timeout=10, retries=1)
        data = json.loads(raw)
        # Response: [[[translated, original, ...], ...], ...]
        translated = ''.join(seg[0] for seg in data[0] if seg and seg[0])
        translated = translated.strip() or text
        _translate_cache[text] = translated
        return translated
    except Exception as e:
        _translate_cache[text] = text  # cache failure as passthrough
        return text


def load_translate_cache_from(score_cache: dict) -> None:
    """Seed in-memory translation cache from persisted titles (avoids re-translating)."""
    for v in score_cache.values():
        if 'title_orig' in v and 'title_ru' in v:
            _translate_cache[v['title_orig']] = v['title_ru']


def fetch_url(url: str, timeout=FETCH_TIMEOUT, retries=FETCH_RETRIES) -> str:
    """Fetch URL with retry. Falls back to a relaxed SSL context if cert verify fails (#5)."""
    import ssl
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/rss+xml, application/xml, text/xml, application/json, */*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except urllib.error.URLError as e:
            last_err = e
            reason = str(getattr(e, 'reason', e))
            # Cert hostname mismatch / verify failure -> retry once with relaxed SSL
            if 'CERTIFICATE_VERIFY_FAILED' in reason or 'certificate' in reason.lower():
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                        return resp.read().decode('utf-8', errors='replace')
                except Exception as e2:
                    last_err = e2
            time.sleep(1)  # brief pause before next attempt
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise last_err if last_err else RuntimeError(f"fetch failed: {url}")


def load_top_assets() -> list:
    """Return list of {sym, name, names[], logo}. Cached daily on disk."""
    # Try cache first
    if ASSETS_CACHE.exists():
        try:
            cached = json.loads(ASSETS_CACHE.read_text())
            if time.time() - cached.get('fetched_at', 0) < ASSETS_REFRESH_SEC:
                print(f"[assets] using cache ({len(cached['assets'])} assets)")
                return cached['assets']
        except Exception as e:
            print(f"[assets] cache read failed: {e}")

    # Fetch fresh from CoinGecko
    url = ('https://api.coingecko.com/api/v3/coins/markets'
           '?vs_currency=usd&order=market_cap_desc&per_page=100&page=1'
           '&sparkline=false')
    try:
        data = json.loads(fetch_url(url))
        assets = []
        for coin in data:
            sym = (coin.get('symbol') or '').upper()
            name = coin.get('name') or sym
            if not sym:
                continue
            names = [name.lower()]
            if sym.lower() not in names:
                names.append(sym.lower())
            assets.append({
                'sym': sym,
                'name': name,
                'names': names,
                'logo': coin.get('image', ''),
            })
        if assets:
            ASSETS_CACHE.write_text(json.dumps({'fetched_at': time.time(), 'assets': assets}))
            print(f"[assets] fetched {len(assets)} from CoinGecko")
            return assets
    except Exception as e:
        print(f"[assets] CoinGecko fetch failed: {e}")

    # Fallback
    if ASSETS_CACHE.exists():
        try:
            return json.loads(ASSETS_CACHE.read_text())['assets']
        except Exception:
            pass
    print("[assets] using hardcoded fallback")
    return FALLBACK_ASSETS


# ============================================================
# ENHANCED HEURISTIC SCORING (Level 1)
# ============================================================

TIER_A = ['reuters', 'bloomberg', 'wsj', 'ft.com', 'financial times', 'coindesk',
          'theblock', 'the block', 'forbes']
TIER_B = ['cointelegraph', 'decrypt', 'cryptoslate', 'beincrypto', 'protos', 'messari',
          'defillama', 'cryptobriefing', 'bitcoin.com']

HIGH_IMPACT_PATTERNS = [
    (re.compile(r'\b(sec|cftc|finra)\b.{0,30}(lawsuit|sue|fine|charge|enforce|investigat|file|motion|crack)', re.I), 4, 'regulation'),
    (re.compile(r'\b(etf|spot etf)\b.{0,30}(approv|launch|list|reject|deni|delay)', re.I), 4, 'etf'),
    (re.compile(r'\b(hack|exploit|stolen|drain|breach|rug pull|rug-pull)\b', re.I), 4, 'security'),
    (re.compile(r'\b(ban|crackdown|prohibit|outlaw)\b', re.I), 3.5, 'regulation'),
    (re.compile(r'\b(approval|approved|greenlight)\b', re.I), 3, 'regulatory_pos'),
    (re.compile(r'\b(listed on|listing|will list)\b.{0,40}(binance|coinbase|kraken|okx)', re.I), 3, 'listing'),
    (re.compile(r'\b(delist|delisted|removed from)\b', re.I), 3.5, 'delisting'),
    (re.compile(r'\b(blackrock|fidelity|vanguard|microstrategy|saylor)\b', re.I), 2.5, 'institutional'),
    (re.compile(r'\b(partnership|integrat|collaboration)\b.{0,40}(google|microsoft|amazon|apple|meta|paypal|visa|mastercard)', re.I), 3, 'partnership'),
    (re.compile(r'\b(upgrade|hardfork|hard fork|mainnet launch|protocol upgrade)\b', re.I), 2.5, 'tech'),
    (re.compile(r'\b(all.?time high|ath|new high|breakout)\b', re.I), 2, 'price'),
    (re.compile(r'\b(crash|plunge|collaps|liquidat|cascade)\b', re.I), 3, 'price_neg'),
    (re.compile(r'\bwhale\b.{0,30}(moved|transfer|deposit|withdraw|accumulat|sold|bought)', re.I), 2, 'whale'),
    (re.compile(r'\b(unlock|token unlock|vesting)\b', re.I), 2.5, 'tokenomics'),
    (re.compile(r'\b(staking|stake|restaking|airdrop)\b', re.I), 1.5, 'tokenomics'),
    (re.compile(r'\b(rate cut|rate hike|fed|fomc|cpi|inflation)\b', re.I), 2, 'macro'),
]

POSITIVE_RE = re.compile(r'\b(surge|rally|breakout|approved|launch|adopt|partnership|breakthrough|milestone|record|gain|jump|soar|bullish|outperform|upgrade|integrat|expand|grow|positive|win|success|raised|raise|investment)\b', re.I)
NEGATIVE_RE = re.compile(r'\b(crash|plunge|hack|exploit|lawsuit|sue|reject|delay|concern|warn|risk|fear|bearish|drop|fall|decline|loss|sold off|sell-off|fraud|scam|collapse|fine|charge|criticism|halt|suspend)\b', re.I)

# NEGATION: words that flip the meaning of a following trigger.
# e.g. "SEC drops lawsuit", "court denies appeal", "ETF rejection overturned"
NEGATION_RE = re.compile(r'\b(drop|drops|dropped|dropping|deny|denies|denied|dismiss|dismissed|dismisses|reject|rejected|overturn|overturned|withdraw|withdrew|withdrawn|cleared|acquit|acquitted|no longer|not guilty|won|wins|defeat|scrap|scrapped|halt|halted|avoid|avoided|resolve|resolved|settle|settled)\b', re.I)

HEADLINE_NEG_RE = re.compile(r'\b(hack|exploit|stolen|drain|lawsuit|sue|reject|delay|crash|plunge|fine|charge|delist|ban)\b', re.I)
HEADLINE_POS_RE = re.compile(r'\b(approved|approval|launch|partnership|all.?time high|breakthrough|record)\b', re.I)


def detect_negation_near(text: str, trigger_pos: int, window: int = 40) -> bool:
    """Check if a negation word appears shortly before/after a trigger position."""
    start = max(0, trigger_pos - window)
    end = min(len(text), trigger_pos + window)
    return bool(NEGATION_RE.search(text[start:end]))


def score_news(title: str, body: str, source: str, published_unix: float) -> dict:
    text = (title + ' ' + body[:500]).lower()
    source_l = source.lower()

    score = 0.0
    categories = set()

    # Source tier -> source quality dimension
    if any(s in source_l for s in TIER_A):
        score += 3; source_quality = 'A'
    elif any(s in source_l for s in TIER_B):
        score += 1.8; source_quality = 'B'
    else:
        score += 0.5; source_quality = 'C'

    # Event patterns (impact)
    max_boost = 0
    for pattern, boost, cat in HIGH_IMPACT_PATTERNS:
        m = pattern.search(text)
        if m:
            max_boost = max(max_boost, boost)
            categories.add(cat)
    score += max_boost

    # Headline weight ×2 (trigger in title matters more than in body)
    headline_boost = 0
    for pattern, _, _ in HIGH_IMPACT_PATTERNS:
        if pattern.search(title):
            headline_boost = max(headline_boost, 2)
    score += headline_boost

    # Specificity (numbers, $ amounts, percentages)
    specificity = 0
    if re.search(r'\$[\d,]+(\.\d+)?[bmk]?\b', text, re.I):
        score += 0.5; specificity += 1
    if re.search(r'\d+%', text):
        score += 0.3; specificity += 1
    specificity_label = 'HIGH' if specificity >= 2 else 'MEDIUM' if specificity == 1 else 'LOW'

    # Recency multiplier
    age_hours = (time.time() - published_unix) / 3600
    if age_hours < 1:
        score *= 1.2
    elif age_hours < 3:
        score *= 1.1
    elif age_hours > 24:
        score *= 0.7
    elif age_hours > 12:
        score *= 0.85

    score = max(0, min(10, score))

    # ---- Sentiment with negation handling ----
    pos_count = len(POSITIVE_RE.findall(text))
    neg_count = len(NEGATIVE_RE.findall(text))

    if pos_count > neg_count + 1:
        sentiment = 1
    elif neg_count > pos_count + 1:
        sentiment = -1
    else:
        sentiment = 0

    # Headline override
    neg_m = HEADLINE_NEG_RE.search(title)
    pos_m = HEADLINE_POS_RE.search(title)
    if neg_m:
        # Check negation: "SEC drops lawsuit" -> the negative trigger is negated -> positive
        if detect_negation_near(title.lower(), neg_m.start()):
            sentiment = 1
            categories.add('negated')
        else:
            sentiment = -1
    if pos_m and not neg_m:
        if detect_negation_near(title.lower(), pos_m.start()):
            sentiment = -1  # "approval rejected"
        else:
            sentiment = 1

    return {
        'impact': round(score, 1),
        'sentiment': sentiment,
        'categories': sorted(categories),
        'specificity': specificity_label,
        'source_quality': source_quality,
    }


# Category -> human phrase for heuristic micro-summary (Russian)
_CAT_PHRASES = {
    'regulation': 'регуляторное событие',
    'regulatory_pos': 'позитивный регуляторный сигнал',
    'etf': 'новость про ETF',
    'security': 'инцидент безопасности (взлом/эксплойт)',
    'delisting': 'делистинг',
    'listing': 'листинг на бирже',
    'institutional': 'движение институционалов',
    'partnership': 'партнёрство',
    'tech': 'техническое обновление',
    'price': 'ценовое движение вверх',
    'price_neg': 'резкое падение цены',
    'whale': 'активность китов',
    'tokenomics': 'изменение токеномики',
    'macro': 'макроэкономический фактор',
    'negated': 'отмена/закрытие негативного события',
}


def heuristic_reason(item: dict, scored: dict) -> str:
    """Generate a simple Russian micro-summary for items not scored by Claude."""
    cats = scored.get('categories', [])
    sent = scored.get('sentiment', 0)
    impact = scored.get('impact', 0)
    conf = item.get('confirmations', 1)

    # Pick the most meaningful category phrase
    phrase = None
    for c in cats:
        if c in _CAT_PHRASES:
            phrase = _CAT_PHRASES[c]
            break

    sent_word = 'позитивная' if sent > 0 else 'негативная' if sent < 0 else 'нейтральная'

    parts = []
    if phrase:
        parts.append(phrase.capitalize())
    else:
        parts.append(f'{sent_word.capitalize()} новость')

    # Impact descriptor
    if impact >= 7:
        parts.append('высокая значимость')
    elif impact >= 5:
        parts.append('средняя значимость')
    else:
        parts.append('низкая значимость')

    if conf >= 3:
        parts.append(f'подтверждено {conf} источниками')

    return ', '.join(parts) + '.'


def map_news_to_assets(title: str, body: str, assets: list) -> list:
    """Find all mentioned assets. Strict matching for ambiguous tickers."""
    text = (title + ' ' + body[:500])
    text_l = text.lower()
    matched = []
    for a in assets:
        sym = a['sym']
        hit = False
        # Full coin name match (e.g. "bitcoin", "near protocol")
        for name in a['names']:
            if len(name) >= 4:
                pattern = r'(?:^|[\s\$#(])' + re.escape(name) + r'(?:$|[\s\.,;:!?\)\'"])'
                if re.search(pattern, text_l, re.I):
                    hit = True
                    break
        # Ticker match
        if not hit:
            if sym in AMBIGUOUS_SYMBOLS:
                # Require $TICKER or #TICKER form to avoid false positives
                if re.search(r'[\$#]' + re.escape(sym) + r'\b', text, re.I):
                    hit = True
            else:
                # Whole-word uppercase-ish match
                if len(sym) >= 3 and re.search(r'(?:^|[\s\$#(])' + re.escape(sym) + r'(?:$|[\s\.,;:!?\)\'"])', text, re.I):
                    hit = True
        if hit:
            matched.append(sym)
    return matched


# ============================================================
# RSS PARSING
# ============================================================

def clean_html(raw: str) -> str:
    raw = re.sub(r'<[^>]+>', '', raw or '')
    raw = html.unescape(raw)
    return re.sub(r'\s+', ' ', raw).strip()


def parse_feed(source_name: str, xml_text: str) -> list:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Some feeds have leading whitespace/junk; try to recover
        try:
            cleaned = xml_text[xml_text.find('<'):]
            root = ET.fromstring(cleaned)
        except Exception:
            return items

    # RSS 2.0: channel/item ; Atom: entry
    channel_items = root.findall('.//item')
    if channel_items:
        for it in channel_items:
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            desc = it.findtext('description') or it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or ''
            pub = it.findtext('pubDate') or it.findtext('{http://purl.org/dc/elements/1.1/}date') or ''
            items.append(_mk_item(source_name, title, link, desc, pub))
    else:
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for it in root.findall('.//atom:entry', ns):
            title = (it.findtext('atom:title', default='', namespaces=ns) or '').strip()
            link_el = it.find('atom:link', ns)
            link = link_el.get('href') if link_el is not None else ''
            desc = it.findtext('atom:summary', default='', namespaces=ns) or it.findtext('atom:content', default='', namespaces=ns) or ''
            pub = it.findtext('atom:updated', default='', namespaces=ns) or it.findtext('atom:published', default='', namespaces=ns) or ''
            items.append(_mk_item(source_name, title, link, desc, pub))
    return [i for i in items if i]


def _mk_item(source, title, link, desc, pub):
    if not title:
        return None
    published_unix = parse_date(pub)
    return {
        'source': source,
        'title': clean_html(title),
        'url': link,
        'body': clean_html(desc)[:600],
        'published_on': published_unix,
    }


def parse_date(s: str) -> float:
    now = time.time()
    if not s:
        return now
    ts = None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
    except Exception:
        for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f%z', '%a, %d %b %Y %H:%M:%S %Z'):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
                break
            except Exception:
                continue
    if ts is None:
        return now
    # Clamp obviously-wrong future dates to now (#16): some feeds publish bad TZ/dates
    if ts > now + 3600:
        return now
    # Clamp absurdly old (>10y) to now as well
    if ts < now - 10 * 365 * 86400:
        return now
    return ts


# ============================================================
# THEME DEDUP + CONFIRMATION COUNTER
# ============================================================

STOPWORDS = set('the a an of to in on for and or as at by is are be with from this that into over after amid '
                'new now says will can could may might has have had been being its their his her'.split())

# Normalize money/number mentions so "$500M" and "half a billion" land closer
def _normalize_tokens(title: str) -> list:
    t = title.lower()
    # collapse money amounts to a generic token
    t = re.sub(r'\$\s?[\d,.]+\s?(b|bn|billion|m|mn|million|k|thousand)?', ' MONEY ', t)
    t = re.sub(r'\b\d+(\.\d+)?\s?%', ' PCT ', t)
    t = re.sub(r'\b\d[\d,.]*\b', ' NUM ', t)
    words = re.findall(r'[a-z0-9]+', t)
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def theme_key(title: str) -> str:
    """Fuzzy theme key from most significant normalized words of a headline."""
    sig = _normalize_tokens(title)
    sig = sorted(set(sig))[:6]
    return hashlib.md5(' '.join(sig).encode()).hexdigest()[:12]


def _token_set(title: str) -> set:
    return set(_normalize_tokens(title))


def dedup_and_confirm(items: list) -> list:
    """Exact-dedup by title hash; group by theme (with token-overlap merging);
    mark follow-ups; count confirmations (#4)."""
    seen_exact = {}
    for it in items:
        h = hashlib.md5(it['title'].lower().strip().encode()).hexdigest()
        if h not in seen_exact:
            seen_exact[h] = it
    unique = list(seen_exact.values())

    # First pass: group by theme key
    themes = {}
    for it in unique:
        tk = theme_key(it['title'])
        themes.setdefault(tk, []).append(it)

    # Second pass: merge theme groups that share high token overlap (#4)
    # so differently-worded headlines about the same event still group.
    theme_list = list(themes.items())
    theme_tokens = {}
    for tk, group in theme_list:
        # representative token set = union of group titles
        toks = set()
        for it in group:
            toks |= _token_set(it['title'])
        theme_tokens[tk] = toks

    merged_into = {}  # tk -> target tk
    keys = [tk for tk, _ in theme_list]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            if a in merged_into or b in merged_into:
                continue
            ta, tb = theme_tokens[a], theme_tokens[b]
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / min(len(ta), len(tb))
            if overlap >= 0.6:  # 60%+ shared significant tokens => same story
                merged_into[b] = a

    final_groups = {}
    for tk, group in theme_list:
        target = merged_into.get(tk, tk)
        final_groups.setdefault(target, []).extend(group)

    result = []
    for tk, group in final_groups.items():
        group.sort(key=lambda x: x['published_on'])
        confirmations = len(group)
        for idx, it in enumerate(group):
            it['confirmations'] = confirmations
            it['is_follow_up'] = (idx > 0)
            result.append(it)
    return result


# ============================================================
# CLAUDE HAIKU RE-SCORING (Step 3)
# ============================================================

def _news_hash(title: str) -> str:
    return hashlib.md5(title.lower().strip().encode()).hexdigest()


def load_score_cache() -> dict:
    if SCORE_CACHE.exists():
        try:
            data = json.loads(SCORE_CACHE.read_text())
            # prune old entries
            cutoff = time.time() - SCORE_CACHE_MAX_AGE
            return {k: v for k, v in data.items() if v.get('cached_at', 0) >= cutoff}
        except Exception:
            return {}
    return {}


def save_score_cache(cache: dict) -> None:
    try:
        SCORE_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    except Exception as e:
        print(f"[claude] cache save failed: {e}")


CLAUDE_SYSTEM = (
    "Ты опытный крипто-аналитик. Оцениваешь влияние новостей на цену активов для трейдеров. "
    "Различай реальные события и мнения/прогнозы аналитиков. "
    "Различай новости, которые ПРО конкретный актив, и где актив лишь упомянут в списке. "
    "Отвечай ТОЛЬКО валидным JSON без markdown и без пояснений вокруг."
)

# Few-shot examples to calibrate scoring (#1)
CLAUDE_FEWSHOT = """Примеры правильной оценки:

Новость: "SEC approves first spot Bitcoin ETF" → {"impact": 9, "sentiment": 1, "horizon": "long", "type": "fact", "reason": "Историческое регуляторное одобрение, мощный приток институционалов"}
Новость: "Analyst predicts XRP could hit $10 by 2027" → {"impact": 2, "sentiment": 0, "horizon": "long", "type": "opinion", "reason": "Спекулятивный прогноз без фактической основы"}
Новость: "Crypto market sheds $1B as BTC, ETH, SOL all drop" → для BTC: {"impact": 5, "sentiment": -1, "relevance": "mentioned", "reason": "Общерыночное падение, BTC не главный герой"}
"""


def build_claude_prompt(item: dict) -> str:
    assets = ', '.join(item.get('assets', []))
    title = item['title'][:300]
    body = item.get('body', '')[:600]
    return f"""{CLAUDE_FEWSHOT}

Теперь оцени эту новость:

Заголовок: {title}
Текст: {body}
Упомянутые активы: {assets}

Верни JSON строго такого формата:
{{
  "impact": <0-10, сила влияния на цену главного актива>,
  "sentiment": <-1 негатив, 0 нейтрально, 1 позитив>,
  "primary_asset": "<главный тикер из списка выше>",
  "relevance": "<main если новость в основном про этот актив, mentioned если упомянут среди прочих>",
  "horizon": "<short краткосрок (часы-дни), long долгосрок (недели+)>",
  "type": "<fact реальное событие, opinion мнение/прогноз>",
  "category": "<institutional, regulatory, technical, hack, listing, partnership, macro, market, other>",
  "is_dust": <true если реклама/гайд/кликбейт/пустой прогноз без значимости>,
  "reason": "<одна короткая фраза по-русски, почему такая оценка>"
}}

Правила:
- Учитывай отрицания: "SEC drops lawsuit" = позитив, не негатив.
- Мнения и прогнозы ("analyst says", "could reach", "might") = type opinion, impact обычно 1-3.
- Если актив лишь в списке пострадавших (общерыночная новость) = relevance mentioned, impact ниже.
- Реклама, гайды "how to buy", общие обзоры без конкретики = is_dust true, impact 0-2.
- Реальные события (листинги, взломы, партнёрства, регуляторика, институционалы) = type fact, impact 5-10."""


def call_claude(api_key: str, item: dict) -> dict | None:
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        # System as a cacheable block (#10): the static instructions get a 90% discount
        # on repeated calls within the cache window.
        "system": [
            {
                "type": "text",
                "text": CLAUDE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": build_claude_prompt(item)}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = ''
        for block in data.get('content', []):
            if block.get('type') == 'text':
                text += block.get('text', '')
        text = text.strip()
        text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
        parsed = json.loads(text)
        return parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')[:200]
        print(f"[claude] HTTP {e.code}: {body}")
        return None
    except Exception as e:
        print(f"[claude] call failed: {e}")
        return None


def claude_rescore(items: list, assets: list) -> None:
    """Re-score NEW items with Claude Haiku. Cached items reuse stored scores.
    Mutates items in place: sets impact, sentiment, reason, category, is_dust."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("[claude] no API key — heuristic only")
        for it in items:
            it['scored_by'] = 'heuristic'
        return

    cache = load_score_cache()
    calls_made = 0
    cache_hits = 0

    # Smarter prioritization (#11): rank candidates by a blend of heuristic impact
    # and source quality, so low-quality SEO sources don't eat Claude budget first.
    def priority(it):
        base = it.get('impact', 0)
        if it.get('source') in LOW_QUALITY_SOURCES:
            base -= 2  # deprioritize SEO-heavy feeds
        if it.get('is_follow_up'):
            base -= 1  # originals before follow-ups
        return base
    items_sorted = sorted(items, key=priority, reverse=True)

    for it in items_sorted:
        h = _news_hash(it['title'])
        if h in cache:
            c = cache[h]
            it['impact'] = c['impact']
            it['sentiment'] = c['sentiment']
            it['reason'] = c.get('reason', '')
            it['category'] = c.get('category', '')
            it['is_dust'] = c.get('is_dust', False)
            it['horizon'] = c.get('horizon', '')
            it['news_type'] = c.get('news_type', '')
            it['relevance'] = c.get('relevance', 'main')
            it['scored_by'] = 'claude-cached'
            cache_hits += 1
            continue

        if calls_made >= CLAUDE_MAX_PER_RUN:
            it['scored_by'] = 'heuristic'
            continue

        result = call_claude(api_key, it)
        calls_made += 1
        if result is None:
            it['scored_by'] = 'heuristic'
            continue

        try:
            impact = max(0, min(10, float(result.get('impact', it['impact']))))
            relevance = str(result.get('relevance', 'main')).lower()
            # Relevance penalty (#3): if asset is only "mentioned", reduce its impact
            if relevance == 'mentioned':
                impact = round(impact * 0.6, 1)

            it['impact'] = impact
            it['sentiment'] = int(result.get('sentiment', it['sentiment']))
            it['reason'] = str(result.get('reason', ''))[:200]
            it['category'] = str(result.get('category', ''))[:30]
            it['is_dust'] = bool(result.get('is_dust', False))
            it['horizon'] = str(result.get('horizon', ''))[:10]
            it['news_type'] = str(result.get('type', ''))[:10]
            it['relevance'] = relevance
            pa = result.get('primary_asset', '')
            if pa and pa in it.get('assets', []):
                it['primary_asset'] = pa
            it['scored_by'] = 'claude'
            cache[h] = {
                'impact': it['impact'], 'sentiment': it['sentiment'],
                'reason': it['reason'], 'category': it['category'],
                'is_dust': it['is_dust'], 'horizon': it['horizon'],
                'news_type': it['news_type'], 'relevance': it['relevance'],
                'cached_at': time.time(),
            }
        except Exception as e:
            print(f"[claude] parse error: {e}")
            it['scored_by'] = 'heuristic'

    save_score_cache(cache)
    print(f"[claude] {calls_made} new calls, {cache_hits} cache hits, "
          f"model={CLAUDE_MODEL}")


# ============================================================
# MAIN
# ============================================================

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    assets = load_top_assets()
    asset_syms = {a['sym'] for a in assets}

    # Fetch feeds
    all_items = []
    feed_status = {}
    for name, url in RSS_FEEDS:
        try:
            xml = fetch_url(url)
            parsed = parse_feed(name, xml)
            all_items.extend(parsed)
            feed_status[name] = len(parsed)
            print(f"[feed] {name}: {len(parsed)} items")
        except Exception as e:
            feed_status[name] = 0
            print(f"[feed] {name}: FAILED ({e})")

    # Drop too-old news
    cutoff = time.time() - NEWS_MAX_AGE_HOURS * 3600
    all_items = [it for it in all_items if it['published_on'] >= cutoff]

    # Dedup + confirmation counting
    all_items = dedup_and_confirm(all_items)

    # Map to assets + score; keep only items mentioning a tracked asset
    scored = []
    for it in all_items:
        matched = map_news_to_assets(it['title'], it['body'], assets)
        if not matched:
            continue
        s = score_news(it['title'], it['body'], it['source'], it['published_on'])
        # Confirmation bonus: 3+ independent sources => boost
        if it.get('confirmations', 1) >= 3:
            s['impact'] = min(10, s['impact'] * 1.3)
        # Follow-up penalty
        if it.get('is_follow_up'):
            s['impact'] = round(s['impact'] * 0.5, 1)

        it.update(s)
        it['_score'] = s  # keep for heuristic reason generation
        it['assets'] = matched
        it['primary_asset'] = matched[0]
        it['reason'] = ''  # filled by Claude
        it['category'] = (s.get('categories') or [''])[0]
        it['is_dust'] = False
        it['horizon'] = ''
        it['news_type'] = ''
        it['relevance'] = 'main'
        it['scored_by'] = 'heuristic'
        scored.append(it)

    # Optional LLM rescoring
    claude_rescore(scored, assets)

    # Filter out "dust": low impact or flagged by Claude as dust
    before = len(scored)
    scored = [
        it for it in scored
        if not it.get('is_dust', False) and it.get('impact', 0) >= IMPACT_THRESHOLD
    ]
    print(f"[filter] removed {before - len(scored)} dust items "
          f"(threshold={IMPACT_THRESHOLD}), {len(scored)} remain")

    # Sort newest first, cap
    scored.sort(key=lambda x: x['published_on'], reverse=True)
    scored = scored[:MAX_NEWS_OUTPUT]

    # Fill heuristic micro-summaries for items Claude didn't score
    for it in scored:
        if not it.get('reason'):
            it['reason'] = heuristic_reason(it, it.get('_score', {}))

    # Translate headlines to Russian (free Google endpoint)
    translated = 0
    for it in scored:
        ru = translate_to_ru(it['title'])
        it['title_ru'] = ru
        if ru != it['title']:
            translated += 1
    print(f"[translate] translated {translated}/{len(scored)} headlines to RU")

    # Clean internal field before output
    for it in scored:
        it.pop('_score', None)

    output = {
        'generated_at': int(time.time()),
        'next_update_hint_sec': 300,
        'feed_status': feed_status,
        'assets': assets,           # top-100 with logos for the extension
        'news': scored,
    }
    NEWS_OUT.write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')))

    elapsed = time.time() - t0
    print(f"\n[done] {len(scored)} scored news, {len(assets)} assets, "
          f"{sum(feed_status.values())} raw items in {elapsed:.1f}s")
    print(f"[done] wrote {NEWS_OUT} ({NEWS_OUT.stat().st_size} bytes)")


if __name__ == '__main__':
    main()
