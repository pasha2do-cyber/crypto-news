"""Niche taxonomy and seed queries (EN + RU/UA/KZ) used to find income-proof posts."""

from __future__ import annotations

# Each niche: human label + lowercase keyword stems matched as substrings.
# Stems are deliberately short ("нейросет", "автоматизац") so they catch word forms.
NICHES: dict[str, dict] = {
    "ai_automation": {
        "label": "AI-автоматизация и AI-агенты для бизнеса",
        "keywords": [
            "ai agent", "ai automation", "automation agency", "n8n", "make.com", "zapier", "chatbot",
            "voice agent", "ai workflow", "gpt", "claude", "llm", "prompt",
            "ии-агент", "ии агент", "ai-агент", "нейросет", "автоматизац", "чат-бот", "чатбот", "промпт",
        ],
    },
    "faceless_ai_content": {
        "label": "Faceless / AI-контент, AI-инфлюенсеры",
        "keywords": [
            "faceless", "ai influencer", "ai model", "ai ugc", "ai avatar", "ai video", "ai music",
            "ии-инфлюенсер", "нейромодел", "ии-модел", "ai-модел", "ии-аватар", "без лица",
        ],
    },
    "digital_products": {
        "label": "Цифровые продукты: гайды, шаблоны, Notion, e-books",
        "keywords": [
            "digital product", "template", "notion", "ebook", "e-book", "printable", "planner", "gumroad",
            "stan store", "stan.store", "payhip", "lemon squeezy", "canva template", "prompt pack",
            "гайд", "шаблон", "чек-лист", "чеклист", "методичк", "электронн", "планер", "цифров",
        ],
    },
    "design_nocode": {
        "label": "Веб-дизайн и no-code (Framer, Webflow, Figma)",
        "keywords": [
            "framer", "webflow", "figma", "landing page", "web design", "website design", "ui/ux", "ux/ui",
            "no-code", "nocode", "brand identity", "logo design",
            "веб-дизайн", "вебдизайн", "лендинг", "тильда", "tilda", "дизайн сайт", "дизайнер интерфейс",
            "айдентик", "логотип",
        ],
    },
    "saas_indie": {
        "label": "Micro-SaaS, приложения, indie hacking",
        "keywords": [
            "saas", "mrr", "arr", "indie hacker", "indiehacker", "micro saas", "my app", "app store",
            "chrome extension", "subscribers", "churn", "launched my",
            "приложени", "свой сервис", "стартап", "подписчиков сервиса", "расширение для",
        ],
    },
    "coaching_consulting": {
        "label": "Коучинг, консалтинг, менторство 1:1",
        "keywords": [
            "coaching", "coach", "consulting", "consultant", "mentorship", "mentor", "1:1", "high ticket",
            "коуч", "консультац", "наставничеств", "менторств", "ментор", "сопровожден",
        ],
    },
    "courses_infobiz": {
        "label": "Онлайн-курсы, запуски, инфобизнес",
        "keywords": [
            "course", "cohort", "webinar", "masterclass", "community", "skool", "membership",
            "курс", "вебинар", "марафон", "интенсив", "инфобиз", "запуск", "продюсер", "закрытый клуб",
            "клуб", "мастер-класс",
        ],
    },
    "smm_copy_ghostwriting": {
        "label": "SMM, копирайтинг, гострайтинг для экспертов",
        "keywords": [
            "social media manager", "smm", "copywriting", "copywriter", "ghostwriting", "ghostwriter",
            "content strategist", "threads growth", "grow on threads", "personal brand", "agency",
            "смм", "копирайт", "контент-мейкер", "контентмейкер", "рилсмейкер", "маркетолог", "таргет",
            "продвижени", "личный бренд", "агентств", "ведение тредс", "ведение аккаунт", "упаковк блог",
        ],
    },
    "ugc_creators": {
        "label": "UGC-креаторы и бренд-интеграции",
        "keywords": [
            "ugc", "user generated", "brand deal", "brand partnership", "paid partnership", "sponsored",
            "юджиси", "ugc-креатор", "интеграци", "бартер", "рекламодател",
        ],
    },
    "trading_crypto": {
        "label": "Трейдинг, крипта, prediction markets",
        "keywords": [
            "trading", "trader", "forex", "crypto", "bitcoin", "btc", "memecoin", "polymarket",
            "трейдинг", "трейдер", "крипт", "биткоин", "арбитраж", "p2p", "фьючерс",
        ],
    },
    "ecommerce_marketplaces": {
        "label": "E-commerce и маркетплейсы (Shopify, Amazon, Etsy, WB, Ozon)",
        "keywords": [
            "shopify", "amazon fba", "etsy", "dropshipping", "tiktok shop", "print on demand", "printify",
            "ecommerce", "e-commerce", "my store",
            "wildberries", "вайлдберриз", "озон", "ozon", "маркетплейс", "селлер", "товарк", "карточк товар",
            "поставщик",
        ],
    },
    "fitness_nutrition": {
        "label": "Фитнес, питание, нутрициология",
        "keywords": [
            "fitness", "personal trainer", "online coach", "meal plan", "weight loss", "nutrition", "macros",
            "фитнес", "тренер", "нутрициолог", "похуде", "питани", "бады", "марафон стройност",
        ],
    },
    "psychology_relationships": {
        "label": "Психология, отношения, саморазвитие",
        "keywords": [
            "therapist", "psychologist", "relationship", "dating coach", "self-improvement", "mindset",
            "психолог", "психотерапевт", "отношени", "самооценк", "травм", "расстановк",
        ],
    },
    "esoteric_astrology": {
        "label": "Эзотерика: астрология, таро, нумерология, Human Design",
        "keywords": [
            "astrology", "tarot", "human design", "numerology", "birth chart", "spiritual",
            "астролог", "таро", "нумеролог", "дизайн человека", "матрица судьбы", "натальн", "эзотер",
        ],
    },
    "beauty_services": {
        "label": "Бьюти-услуги и бьюти-обучение",
        "keywords": [
            "nails", "nail tech", "lash", "brows", "esthetician", "makeup artist", "hair stylist", "skincare",
            "маникюр", "ногт", "ресниц", "бров", "визаж", "косметолог", "бьюти", "парикмахер", "уход за кож",
        ],
    },
    "real_estate_rentals": {
        "label": "Недвижимость и аренда (Airbnb, посуточно)",
        "keywords": [
            "realtor", "real estate", "airbnb", "short-term rental", "property", "wholesaling",
            "риелтор", "риэлтор", "недвижим", "посуточн", "аренд", "квартир",
        ],
    },
    "newsletter_telegram": {
        "label": "Медиа: newsletter, Substack, Telegram-каналы",
        "keywords": [
            "newsletter", "substack", "beehiiv", "convertkit", "paid subscribers",
            "телеграм-канал", "телеграм канал", "тг-канал", "тг канал", "telegram", "посев", "рассылк",
            "закрытый канал",
        ],
    },
    "video_photo_content": {
        "label": "Видео/фото-продакшн, монтаж, мобилография",
        "keywords": [
            "video editor", "video editing", "videographer", "photographer", "reels editing", "short form",
            "монтаж", "фотограф", "видеограф", "мобилограф", "рилс", "reels",
        ],
    },
    "dev_freelance": {
        "label": "Фриланс-разработка и IT-услуги",
        "keywords": [
            "freelance developer", "web developer", "upwork", "fiverr", "software engineer", "contract work",
            "фриланс", "разработчик", "программист", "kwork", "верстк", "бот для телеграм", "телеграм-бот",
        ],
    },
    "tutoring_languages": {
        "label": "Репетиторство и языки",
        "keywords": [
            "tutor", "tutoring", "english teacher", "language teacher", "esl",
            "репетитор", "преподаватель", "английск", "уроки", "ученик",
        ],
    },
    "handmade_crafts": {
        "label": "Хендмейд и крафт",
        "keywords": [
            "handmade", "crochet", "candles", "jewelry", "pottery", "small batch",
            "хендмейд", "ручной работы", "свечи", "вязан", "украшени", "керамик",
        ],
    },
    "mmo_digital_marketing": {
        "label": "«Как зарабатывать онлайн»: digital marketing, resell rights, faceless-продукты",
        "keywords": [
            "make money online", "digital marketing", "resell rights", "master resell", "plr", "faceless marketing",
            "side hustle", "passive income", "low ticket", "high ticket offer",
            "заработок в интернете", "пассивный доход", "доход онлайн", "удаленный доход", "заробіток онлайн",
        ],
    },
    "affiliate": {
        "label": "Аффилиат-маркетинг (LTK, ShopMy, Amazon, партнёрки)",
        "keywords": [
            "affiliate", "ltk", "shopmy", "amazon storefront", "amazon finds", "commission",
            "партнерк", "партнёрк", "реферальн", "партнерськ",
        ],
    },
    "books_publishing": {
        "label": "Книги и авторы (BookThreads, самиздат)",
        "keywords": [
            "my book", "preorder", "pre-order", "bookthreads", "debut novel", "self-publish", "kdp",
            "моя книга", "книгу", "предзаказ", "самиздат", "писательниц", "моя книжка",
        ],
    },
    "local_services": {
        "label": "Локальный сервисный бизнес",
        "keywords": [
            "cleaning business", "lawn care", "pressure washing", "detailing", "home service", "contractor",
            "клининг", "детейлинг", "ремонт", "химчистк", "доставк",
        ],
    },
}

# Phrases that tend to appear in "I made money doing X" posts. Used to seed keyword search.
INCOME_QUERIES: dict[str, list[str]] = {
    "us": [
        "first sale", "income report", "monthly revenue", "MRR", "made 10k", "10k month",
        "quit my 9-5", "side hustle income", "passive income", "digital product sales",
        "paying clients", "made money on threads", "from threads", "in sales", "my first $1000",
        "revenue this month", "booked out", "paid subscribers", "100% inbound", "woke up to sales",
    ],
    "cis": [
        "заработала", "заработал", "вышла на доход", "доход в месяц", "первая продажа",
        "продажи с тредс", "клиенты из тредс", "заявки из тредс", "ушла с найма",
        "уволилась и", "пассивный доход", "выручка за месяц", "продажи гайда",
        "запуск курса", "первые оплаты", "из тредс в телеграм", "кейс тредс", "окупила",
        # UA
        "мій дохід", "заробила", "клієнти з тредс", "продажі в тредс",
    ],
}

# Words near an amount that mark it as earned money rather than a price tag.
INCOME_CONTEXT = [
    "made", "make", "making", "earn", "revenue", "mrr", "arr", "profit", "income", "sales", "sold",
    "generated", "hit", "crossed", "cashed", "paid me", "booked", "net", "gross",
    "заработ", "доход", "выручк", "прибыл", "продаж", "продал", "вышл", "вышел", "оборот",
    "окупил", "получил", "получила", "зарабатыва", "на руки", "оплат",
    "заробі", "заробил", "дохід", "продажі", "виручк",
]
PRICE_CONTEXT = [
    "for only", "only $", "price", "costs", "cost", "just $", "per spot", "discount", "% off", "buy",
    "цена", "стоит", "стоимость", "всего за", "скидк", "купить", "тариф",
]

PERIODS: list[tuple[float, list[str]]] = [
    # multiplier to convert to monthly, phrases (checked right after the amount)
    (1.0, ["/mo", "/month", "a month", "per month", "monthly", "mrr", "this month", "last month", "in a month",
           "в месяц", "/мес", "за месяц", "ежемесячно", "в мес", "мес.", "за місяць", "на місяць", "/міс"]),
    (1 / 12, ["/yr", "/year", "a year", "per year", "arr", "this year", "annual", "в год", "за год", "за рік", "на рік"]),
    (30.0, ["/day", "a day", "per day", "in a day", "в день", "за день"]),
    (4.33, ["/week", "a week", "per week", "this week", "в неделю", "за неделю"]),
]

# Where monetized traffic usually goes; seeing these in a post/link is a strong "selling" signal.
SALES_DOMAINS = [
    "gumroad", "stan.store", "payhip", "lemonsqueezy", "beacons.ai", "linktr.ee", "kajabi", "skool.com",
    "teachable", "podia", "patreon", "substack", "beehiiv", "calendly", "etsy.com", "shopify",
    "t.me", "boosty", "getcourse", "prodamus", "taplink", "tilda", "tribute", "lava.top",
]

# Approximate FX to USD; override with THREADS_FX_JSON='{"RUB":0.012}'.
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "RUB": 0.0115, "UAH": 0.024, "KZT": 0.002, "BYN": 0.30}
