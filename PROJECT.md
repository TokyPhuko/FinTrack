# FinTrack — персональный учёт финансов

Self-hosted веб-приложение для учёта личных финансов (аналог CoinKeeper) с веб-дашбордом, Telegram-ботом и Telegram Mini App, Excel-отчётами, бюджетированием, планированием будущих трат и опциональными LLM-функциями.

## Цели и боли

- Главная боль: забывал заносить расходы → ввод должен быть максимально тривиальным (Telegram-бот «350 кофе»).
- Excel — **только формат отчётов** (.xlsx выгрузки), НЕ хранилище данных; данные живут в базе.
- Важно: **переводы между счетами не учитываются как расходы** (ни в бюджете, ни в отчётах, ни в LLM-анализе).
- Полное управление категориями (CRUD): добавление, переименование, лимиты, архивирование.
- Первый прогон — локально на этом ПК, деплой на VPS — последним этапом.

## Стек

| Компонент | Технология |
|---|---|
| Backend | Python + FastAPI |
| БД | SQLite (схема готова к PostgreSQL при масштабировании) |
| Бот | aiogram 3 |
| Фронт | Один: HTML/CSS/JS + HTMX + Chart.js — Telegram Mini App + адаптивный веб |
| Excel | openpyxl |
| Планировщик | APScheduler |
| LLM | Google Gemini free tier (опционально OpenRouter как запасной) |
| Деплой | docker compose: app + caddy |

## Ключевые решения

### Переводы — не расходы
Поле `transactions.type`:
- `expense` — расход (− счёт, в бюджет/отчёты/LLM)
- `income` — доход (+ счёт)
- `transfer` — перевод: − счёт A, + счёт B; **не трогает** ни бюджеты, ни «потрачено за месяц», ни ругательства

### LLM — надстройка, не зависимость
При отказе/закончившихся токенах приложение не умирает:
- Circuit breaker + локальные фоллбэки: шаблонные колкие фразы вместо «ругательств», авто-отчёт по шаблону, шаблонные SQL-сценарии, курсы ЦБ РФ + ручные ставки.
- Все LLM-функции опциональны: ругательства после траты, еженедельный разбор, чат-аналитика, поиск выгодных вкладов.
- Настройка «уровень вежливости»: сарказм → полный мат.

### 4 типа предупреждений (без LLM)
1. Регулярные платежи
2. Сроки вкладов
3. Планы на будущее
4. Прогноз превышения бюджета

### Доступ
- Локально: `http://localhost:8000` (тест на этом ПК).
- VPS: пароль + HTTP; HTTPS через Caddy + nip.io; опционально Tailscale.
- Mini App: тот же фронт внутри Telegram (initData HMAC) и в обычном браузере.
- Бот принимает сообщения только от Telegram ID пользователя.
- Бэкапы (SQLite + xlsx) в Telegram.

## Схема БД (этап 1)

- `users` — id, username, password_hash, telegram_id, llm_politeness, settings
- `accounts` — id, name, type (cash/card/savings/deposit), currency, balance, archived
- `categories` — id, name, type (expense/income), monthly_limit, icon, archived (полный CRUD; не удаляются, а архивируются)
- `transactions` — id, user_id, type (expense|income|transfer), amount, category_id, account_id, transfer_account_id, note, occurred_at, created_at, source (web|bot|import)
- `planned_transactions` — id, type (expense|income), amount, category_id, account_id, title, due_date, is_recurring, recurrence, status
- `budgets` — id, category_id, month, amount
- `deposits` — id, title, bank, amount, rate, start_date, end_date, auto_renew, notified
- `llm_notes` — id, user_id, kind, content, created_at
- `reminders` — id, kind, due_at, payload, sent_at

## Excel-отчёт (7 листов)

1. **Сводка** — доход/расход/чистыми, остаток, % бюджета, топ-категорий
2. **Транзакции** — Date | Type | Category | Account | Amount | Note | Month
3. **Бюджет** — категории × месяцы: Budget / Actual / Variance
4. **Сводная по категориям** — pivot категория × месяц
5. **План vs Факт**
6. **Счета и переводы** — балансы, переводы отдельно (исключены из расходов)
7. **Динамика** — по дням/неделям

Правила: 10–20 категорий, расходы минус / доходы плюс, category — dropdown, аномалии условным форматированием.

## Интерфейс

Страницы: Главная · Траты · Планы · Бюджеты · Счета/Переводы · Вклады · Отчёты · Чат LLM · Настройки.

Бот: текстовый ввод, /stats, undo, напоминания, ругательства.

## Этапы

1. **Каркас** — docker, FastAPI, SQLite, auth, схема БД (с type transfer) ← текущий
2. Траты + переводы + Планы в вебе
3. Бот: ввод, /stats, undo
4. Бюджеты + 4 предупреждения (с исключением переводов)
5. Excel-отчёты (7 листов)
6. LLM-слой с деградацией
7. Вклады + поиск предложений
8. Telegram Mini App: initData, темы, кнопки; полировка
9. Деплой на VPS

## Локальная разработка

```bash
cd fintrack
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# http://localhost:8000
```

Данные: SQLite-файл в `data/` — для сброса тестов просто удалить/скопировать.
Токен бота и ключ Gemini — в `.env` (см. `.env.example`); без них ядро работает.

## Статус

- [x] Этап 1 — каркас: FastAPI + SQLite (9 таблиц, `type` expense|income|transfer), парольная авторизация, seed, базовый дашборд
- [x] Этап 2 — операции, переводы, планы: CRUD, балансы, переводы **не** расходы
- [x] Этап 3 — Telegram-бот @gazebo_bro_bot: парсер, /start /bind /stats /last /undo /help /report, roast после траты
- [x] Этап 4 — бюджеты + 4 предупреждения (планы/регулярка, сроки вкладов, превышение, прогноз темпа) — всё без LLM
- [x] Этап 5 — Excel-отчёт 7 листов + графики `/api/export/excel?month=YYYY-MM`
- [x] Этап 6 — LLM-слой: Gemini/OpenRouter + circuit breaker + шаблонные фоллбэки (roast, weekly, chat, поиск вкладов)
- [x] Этап 7 — вклады CRUD, прогноз дохода, предупреждения о сроках, поиск ставок
- [x] Этап 8 — Mini App: initData HMAC auth, кнопка в боте при `MINI_APP_URL`, тот же фронт
- [x] Админка — `/admin`: список юзеров, профиль (счета/операции/планы/вклады), смена пароля, блокировка, TG unbind, LLM-уровень, «войти как» + выход, nav только для `is_admin`
- [ ] Этап 9 — деплой на VPS (когда будешь готов)
