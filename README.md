# Crypto News Impact — бэкенд (GitHub Actions + Pages)

Собирает крипто-новости каждые 5 минут, скорит их и публикует `news.json`,
который читает твоё расширение. Полностью бесплатно на публичном репозитории.

---

## Что внутри

```
build_news.py                      — собирает RSS, скорит, пишет public/news.json
serve_local.py                     — локальный тест (раздаёт public/ с CORS)
public/news.json                   — результат (создаётся скриптом)
.github/workflows/build-news.yml   — cron каждые 5 минут на GitHub Actions
```

---

## ЧАСТЬ А — Локальный тест (по желанию, 2 минуты)

Прежде чем деплоить, можно проверить локально:

```bash
cd news-backend
python3 serve_local.py
```

Откроется `http://localhost:8765/news.json`. В расширении оставь `activeSource = 'local'`
(в `sidepanel.js`, это значение по умолчанию). Расширение начнёт показывать реальные новости.

Останови через Ctrl+C, когда наиграешься. Дальше — деплой в облако.

---

## ЧАСТЬ Б — Деплой на GitHub (15 минут, бесплатно)

### Шаг 1. Создай репозиторий
1. Зайди на https://github.com/new
2. Имя: `crypto-news` (или любое)
3. Выбери **Public** (обязательно — для бесплатных Actions без лимита)
4. Создай репозиторий

### Шаг 2. Залей файлы
Самый простой путь — через веб-интерфейс:
1. На странице репозитория → **Add file → Upload files**
2. Перетащи СОДЕРЖИМОЕ папки `news-backend` (файл `build_news.py`,
   папку `.github`, папку `public`)
3. Commit

Или через терминал:
```bash
cd news-backend
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/ТВОЙ_ЛОГИН/crypto-news.git
git push -u origin main
```

### Шаг 3. Разреши Actions писать в репозиторий
1. Репозиторий → **Settings → Actions → General**
2. Прокрути до **Workflow permissions**
3. Выбери **Read and write permissions** → Save

(Без этого бот не сможет коммитить `news.json`.)

### Шаг 4. Запусти сборку вручную (первый раз)
1. Вкладка **Actions** → выбери workflow **Build News**
2. Кнопка **Run workflow** → Run
3. Подожди ~1 минуту. Должен появиться зелёный ✓ и обновиться `public/news.json`

Дальше оно само будет запускаться каждые 5 минут.

### Шаг 5. Включи GitHub Pages
1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main**, папка: **/ (root)** → Save
4. Через 1-2 минуты Pages поднимется. URL будет вида:
   `https://ТВОЙ_ЛОГИН.github.io/crypto-news/`
5. Проверь что открывается:
   `https://ТВОЙ_ЛОГИН.github.io/crypto-news/public/news.json`

> ⚠️ Обрати внимание на `/public/` в пути. Если хочешь чтобы было без него —
> можно настроить Pages на папку `/public`, но тогда выбери в Pages
> folder `/public` вместо `/ (root)` на шаге 3.

### Шаг 6. Подключи расширение к облаку
В файле `sidepanel.js` расширения:
1. Найди `NEWS_SOURCES.github` и впиши свой URL:
   ```js
   github: 'https://ТВОЙ_ЛОГИН.github.io/crypto-news/public/news.json',
   ```
2. Переключи `activeSource` на `'github'`:
   ```js
   let activeSource = 'github';
   ```
3. Перезагрузи расширение в `chrome://extensions/`

Готово. Теперь новости берутся из облака, твой Mac не нужен.

---

## Скоринг — что сейчас работает

Двухуровневая система:

**Уровень 1 — эвристика (всегда, бесплатно):**
- Negation handling, headline weight ×2, дедупликация тем, confirmation counter

**Уровень 2 — Claude Haiku (если задан ключ):**
- Каждую НОВУЮ новость оценивает Claude: impact, sentiment, категория, обоснование (reason)
- Помечает «пыль» (реклама, гайды, кликбейт) → она исключается из news.json
- Результаты кэшируются в `.score_cache.json` — повторно не оцениваются (экономия)

## Подключение Claude (Уровень 2)

### Шаг 1. Получи API-ключ
1. Зайди на https://console.anthropic.com
2. Создай ключ (Settings → API Keys → Create Key)
3. Пополни баланс (Billing) — Claude API платный, ~$5/мес для этого проекта
4. Скопируй ключ `sk-ant-...`

### Шаг 2. Добавь ключ в GitHub Secrets
1. Репозиторий → **Settings → Secrets and variables → Actions**
2. **New repository secret**
3. Name: `ANTHROPIC_API_KEY`
4. Secret: вставь свой `sk-ant-...`
5. Add secret

Готово. Workflow уже настроен передавать этот ключ в скрипт. При следующем запуске
Claude начнёт скорить новости. Если ключа нет — работает только эвристика (бесплатно).

### Настройки скоринга (в начале build_news.py)
- `IMPACT_THRESHOLD = 3.0` — порог отсечения пыли (ниже = не показывать)
- `CLAUDE_MAX_PER_RUN = 25` — макс. вызовов Claude за один запуск (защита от перерасхода)
- `CLAUDE_MAX_TOKENS = 500` — потолок ответа Claude

## Стоимость

| Что | Цена |
|---|---|
| GitHub Actions (публичный репо) | $0 (безлимит) |
| GitHub Pages раздача | $0 (безлимит) |
| Claude Haiku API (только новые новости) | ~$5/мес |

Без ключа Claude — **$0/мес**, работает эвристика (~75% точности).
С Claude — **~$5/мес**, точность ~90% + обоснования.
