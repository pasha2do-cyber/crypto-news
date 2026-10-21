# threads-niche-mcp

MCP-сервер, который ищет в **Threads** посты с доказательствами заработка (`$8k/mo`, `вышла на 300к в месяц`,
`мій дохід 40 000 грн`) и ранжирует ниши 1→10 отдельно для **US** и **СНГ (RU/UA/KZ)**.

Работает через **официальный Threads Graph API** (`graph.threads.com`). Без серых скрейперов, бан аккаунта не грозит.

## Что умеет

| Инструмент | Что делает |
|---|---|
| `scan_profitable_niches` | Прогоняет 20 EN и 22 RU/UA запроса-«сигнала дохода», вытаскивает суммы, валюты и периоды, тегирует ниши и выдаёт топ-10 по каждому рынку с пруфами (permalink, автор, сумма) |
| `niche_deep_dive` | Разбирает одну нишу: кто зарабатывает, что продаёт, по какой цене, через какие каналы (Stan, Gumroad, t.me, Boosty, GetCourse…) |
| `check_creator` | Карточка автора: подписчики, охват и лайки за 7 дней, денежные посты, офферы |
| `threads_search` | Сырой поиск по ключевому слову или topic-тегу с извлечёнными сигналами |
| `analyze_posts` | Тот же анализ **без токена**, на выгрузке JSON/JSONL/CSV (Apify, любой скрейпер, свой дамп) |
| `list_niches` | 25 ниш, их ключевые стемы и seed-запросы |
| prompt `niche_report` | Полный прогон: scan → deep dive топ-3 → check_creator → отчёт 1–10 с планом входа |

### Как считается скор (0–100)

```
35% proof      сколько РАЗНЫХ авторов в нише показывают доход (лог-шкала, 15 авторов = максимум)
25% money      медиана заявленного дохода в месяц, USD (лог-шкала, $50k = максимум)
15% selling    доля постов со ссылками на продажи (stan.store, gumroad, t.me, boosty, getcourse…)
15% fresh      доля постов за последние 30 дней
10% popularity лайки (если есть в данных) или позиция в TOP-выдаче
```

Логика: ниша, где **10 разных людей** показывают по $3k, ценнее ниши, где один гуру показывает $100k.
Дневные суммы («$3k за день») в месячные не пересчитываются: обычно это хвастовство лучшим днём.
Цены (`гайд стоит 2 990 ₽`) отделяются от дохода и попадают в `median_price_usd`.

## Установка

Нужен Python 3.10+ и [uv](https://docs.astral.sh/uv/).

**Claude Code:**
```bash
claude mcp add threads-niche -e THREADS_ACCESS_TOKEN=ВАШ_ТОКЕН -- uv run --directory /путь/к/threads-niche-mcp threads-niche-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "threads-niche": {
      "command": "uv",
      "args": ["run", "--directory", "/путь/к/threads-niche-mcp", "threads-niche-mcp"],
      "env": { "THREADS_ACCESS_TOKEN": "ВАШ_ТОКЕН" }
    }
  }
}
```

Дальше просто пишите Claude:
> Найди 10 самых прибыльных ниш в Threads за последние 30 дней, отдельно US и СНГ. Для топ-3 сделай deep dive.

## Доступ к API: честно о главном ограничении

1. Создайте приложение на [developers.facebook.com](https://developers.facebook.com/apps) с use case **Access the Threads API**.
2. Добавьте разрешения `threads_basic`, `threads_keyword_search`, а для `check_creator` ещё `threads_profile_discovery`.
3. Получите токен и обменяйте его на long-lived (60 дней): [гайд Meta](https://developers.facebook.com/docs/threads/get-started/long-lived-tokens).
4. **App Review обязателен.** Пока `threads_keyword_search` не одобрен, Meta ищет **только по вашим собственным постам**.
   Сервер это распознаёт и пишет об этом в `warnings`. С `threads_profile_discovery` то же самое: без advanced access
   доступны только аккаунты Meta.

**Пока ревью не прошло:** выгрузите посты любым скрейпером (например, актор Threads Search на Apify) и отдайте файл в
`analyze_posts(file_path="export.json")`. Лайки из выгрузки сразу пойдут в скор.

Лимиты Meta: 2 200 поисковых запросов за скользящие 24 часа (пустые выдачи не считаются), 1 000 запросов profile lookup.
Один `scan_profitable_niches(market="both")` расходует около 42 запросов. Ответы кешируются на час.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `THREADS_ACCESS_TOKEN` | токен пользователя Threads (обязателен для API-инструментов) |
| `THREADS_FX_JSON` | свои курсы к USD, например `{"RUB":0.012,"UAH":0.024}` |
| `THREADS_POST_FIELDS` | переопределить список полей поста |
| `THREADS_API_BASE` | другой base URL (для прокси или тестов) |

## Ограничения

- Суммы в постах — это **заявления авторов**, а не проверенная выручка. Всегда смотрите `evidence` и permalink.
- Ниши определяются по ключевым стемам. Посты с деньгами без ниши попадают в `unclassified_income_posts`, и Claude
  может разобрать их вручную.
- Официальный API не отдаёт лайки чужих постов. Популярность в этом случае берётся из позиции в TOP-выдаче.

## Разработка

```bash
uv venv && uv pip install -e ".[dev]" anyio
pytest -q
```
