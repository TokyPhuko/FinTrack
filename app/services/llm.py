"""LLM layer: Gemini primary, OpenRouter fallback, local template fallbacks.

Hard rule: if LLM is unavailable — the app keeps working. Circuit breaker opens
after N failures; every feature has a non-LLM fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Category,
    Deposit,
    LlmNote,
    Transaction,
    TransactionType,
    User,
)

logger = logging.getLogger("fintrack.llm")
settings = get_settings()

MAX_FAILURES = 3
COOLDOWN_SECONDS = 300  # 5 min open circuit


@dataclass
class CircuitBreaker:
    failures: int = 0
    opened_at: float = 0.0
    last_error: str = ""

    @property
    def is_open(self) -> bool:
        if self.failures < MAX_FAILURES:
            return False
        if time.time() - self.opened_at < COOLDOWN_SECONDS:
            return True
        # half-open: allow one try
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.last_error = ""

    def record_failure(self, err: str) -> None:
        self.failures += 1
        self.last_error = err[:200]
        if self.failures >= MAX_FAILURES:
            self.opened_at = time.time()
            logger.warning("LLM circuit OPEN after %d failures: %s", self.failures, err)


breaker = CircuitBreaker()


@dataclass
class LlmStatus:
    available: bool
    provider: str
    reason: str
    fallback_mode: bool

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "provider": self.provider,
            "reason": self.reason,
            "fallback_mode": self.fallback_mode,
        }


def get_llm_status() -> LlmStatus:
    if breaker.is_open:
        return LlmStatus(
            available=False,
            provider="none",
            reason=f"Отключено на {COOLDOWN_SECONDS // 60} мин: {breaker.last_error or 'ошибки'}",
            fallback_mode=True,
        )
    if settings.gemini_api_key:
        return LlmStatus(available=True, provider="gemini", reason="", fallback_mode=False)
    if settings.openrouter_api_key:
        return LlmStatus(available=True, provider="openrouter", reason="", fallback_mode=False)
    return LlmStatus(
        available=False,
        provider="templates",
        reason="Ключи не заданы — работают шаблоны",
        fallback_mode=True,
    )


async def _call_gemini(prompt: str, system: str = "") -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.6-flash:generateContent"
    )
    body: dict = {
        "contents": [
            {
                "parts": [{"text": (system + "\n\n" if system else "") + prompt}],
            }
        ],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 2048},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = next((p["text"].strip() for p in parts if p.get("text")), "")
        if not text:
            raise RuntimeError(f"Gemini empty text: {data}")
        return text
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini bad response: {data}") from e


async def _call_openrouter(prompt: str, system: str = "") -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": "deepseek/deepseek-chat",
        "messages": messages,
        "max_tokens": 800,
        "temperature": 0.8,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"OpenRouter bad response: {data}") from e


async def complete(prompt: str, system: str = "") -> str:
    """Try LLM providers; on failure record breaker and raise RuntimeError."""
    status = get_llm_status()
    if not status.available and status.provider in ("none",):
        raise RuntimeError(status.reason or "LLM unavailable")
    if breaker.is_open:
        raise RuntimeError("Circuit open")

    providers = []
    if settings.gemini_api_key:
        providers.append(("gemini", _call_gemini))
    if settings.openrouter_api_key:
        providers.append(("openrouter", _call_openrouter))
    if not providers:
        raise RuntimeError("No API keys")

    last_err: Exception | None = None
    for name, fn in providers:
        try:
            text = await fn(prompt, system)
            breaker.record_success()
            return text
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM provider %s failed: %s", name, e)
    breaker.record_failure(str(last_err) if last_err else "unknown")
    raise RuntimeError(str(last_err) if last_err else "LLM failed")


# ---------- fallback templates ----------

ROASTS_SARCASM = [
    "О, {amount} ₽ на «{cat}». Экономия — это когда не тратишь, а не когда считаешь потом.",
    "«{cat}» на {amount} ₽. Ну хотя бы записал — уже прогресс, правда смешной.",
    "{amount} ₽ улетело в «{cat}». Бюджет не резиновый, но ты явно не знал.",
]
ROASTS_NORMAL = [
    "Спасибо за запись: {amount} ₽ — «{cat}». Если это в рамках бюджета — всё ок!",
    "Записал {amount} ₽ на «{cat}». Так держать.",
]
ROASTS_HARD = [
    "Опять {amount} ₽ на «{cat}»?! Ты там банк ломаешь? Бюджет в отвале.",
    "{amount} ₽ в «{cat}». Поздравляешь себя или карму?",
    "Снова {amount} ₽… «{cat}». Вклад растёт, а расходы растут быстрее.",
]


async def roast_after_expense(
    db: AsyncSession,
    user: User,
    amount: float,
    category_name: str,
) -> str:
    politeness = user.llm_politeness  # 0=сарказм 1=обычно 2=мягко 3=полный мат
    prompt = (
        f"Пользователь потратил {amount:.0f} ₽ на «{category_name}». "
        f"Уровень вежливости 0-3 (0=сарказм, 3=можно нецензурно, "
        f"текущий={politeness}). "
        "Одна короткая колкая шутка-ругательство (1-2 предложения) на русском. "
        "Без Markdown."
    )
    system = "Ты — колкий финансовый напарник. Говоришь по-русски. Коротко."
    try:
        text = await complete(prompt, system)
        kind = "roast"
    except Exception:  # noqa: BLE001
        if politeness >= 3:
            pool = ROASTS_HARD
        elif politeness == 0:
            pool = ROASTS_SARCASM
        else:
            pool = ROASTS_NORMAL
        text = random.choice(pool).format(amount=f"{amount:.0f}", cat=category_name)
        kind = "roast_fallback"

    note = LlmNote(user_id=user.id, kind=kind, content=text)
    db.add(note)
    await db.commit()
    return text


async def weekly_report(db: AsyncSession, user: User) -> str:
    now = datetime.now()
    week_start = now - timedelta(days=7)

    rows = (
        await db.execute(
            select(
                Category.name,
                func.sum(Transaction.amount),
                func.count(Transaction.id),
            )
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.expense,
                Transaction.occurred_at >= week_start,
            )
            .group_by(Category.name)
            .order_by(func.sum(Transaction.amount).desc())
        )
    ).all()
    income = (
        await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.income,
                Transaction.occurred_at >= week_start,
            )
        )
    ).scalar() or 0
    expense = sum(r[1] for r in rows) if rows else 0

    lines = [f"Расходы за неделю: {expense:.0f} ₽, доходы: {income:.0f} ₽"]
    for name, s, cnt in rows[:7]:
        lines.append(f"- {name}: {s:.0f} ₽ ({cnt} раз)")

    prompt = (
        "Сделай короткий еженедельный разбор личных финансов (3-5 предложений, "
        "по-русски, без Markdown).\n\nДанные:\n" + "\n".join(lines)
    )
    try:
        text = await complete(prompt, "Ты — финансовый аналитик. Говори по-русски, дружелюбно, по делу.")
    except Exception:  # noqa: BLE001
        top = rows[0] if rows else None
        if top:
            text = (
                f"📊 За неделю: −{expense:.0f} ₽, +{income:.0f} ₽.\n"
                f"Больше всего ушло на «{top[0]}» — {top[1]:.0f} ₽ ({top[2]} раз). "
                f"Проверь, можно ли сократить. Держись!"
            )
        else:
            text = "На этой неделе расходов не было — отличный результат! 🎉"

    note = LlmNote(user_id=user.id, kind="weekly", content=text)
    db.add(note)
    await db.commit()
    return text


async def chat_analyze(db: AsyncSession, user: User, question: str) -> str:
    """Answer finance questions. Fallback: SQL-ish canned answers."""
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    income = (
        await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.income,
                Transaction.occurred_at >= month_start,
            )
        )
    ).scalar() or 0
    expense = (
        await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.expense,
                Transaction.occurred_at >= month_start,
            )
        )
    ).scalar() or 0
    by_cat = (
        await db.execute(
            select(Category.name, func.sum(Transaction.amount))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.expense,
                Transaction.occurred_at >= month_start,
            )
            .group_by(Category.name)
            .order_by(func.sum(Transaction.amount).desc())
            .limit(10)
        )
    ).all()
    from app.models import Account

    acc_rows = (
        await db.execute(
            select(Account.name, Account.balance).where(
                Account.user_id == user.id, Account.archived.is_(False)
            )
        )
    ).all()

    context = (
        f"Месяц {now.strftime('%Y-%m')}: доход {income:.0f}, расход {expense:.0f}.\n"
        f"По категориям: " + ", ".join(f"{n}={s:.0f}" for n, s in by_cat) + ".\n"
        f"Счета: " + ", ".join(f"{n}={b:.0f}" for n, b in acc_rows) + ".\n"
        f"Вопрос пользователя: {question}"
    )
    try:
        return await complete(
            context,
            "Отвечай по-русски кратко и по делу. Только текст, без Markdown-разметки.",
        )
    except Exception:  # noqa: BLE001
        # local fallback
        q = question.lower()
        if any(w in q for w in ("сколько", "итог", "сводк", "расход")):
            top = by_cat[0] if by_cat else None
            ans = f"В этом месяце: доход {income:.0f} ₽, расход {expense:.0f} ₽, "
            ans += f"чистыми {income - expense:.0f} ₽."
            if top:
                ans += f" Больше всего на «{top[0]}» — {top[1]:.0f} ₽."
            return ans
        if any(w in q for w in ("баланс", "счет", "сколько денег")):
            return "Балансы: " + "; ".join(f"{n} — {b:.0f} ₽" for n, b in acc_rows) + "."
        if any(w in q for w in ("где", "категор")):
            if by_cat:
                lines = [f"• {n}: {s:.0f} ₽" for n, s in by_cat]
                return "Расходы по категориям:\n" + "\n".join(lines)
            return "Расходов по категориям пока нет."
        return (
            f"LLM сейчас недоступен ({get_llm_status().reason or 'нет ключей'}). "
            f"База: доход {income:.0f} ₽, расход {expense:.0f} ₽ за месяц."
        )


# ---------- deposit rate search (LLM with grounding-like fallback) ----------

# Static fallback: approximate market rates as of late 2025/2026 (user can edit)
FALLBACK_RATES = [
    {"bank": "Т-Банк", "rate": 16.5, "term": "до 3 мес"},
    {"bank": "Сбербанк", "rate": 14.5, "term": "6 мес"},
    {"bank": "ВТБ", "rate": 15.0, "term": "6 мес"},
    {"bank": "Альфа-Банк", "rate": 16.0, "term": "3-6 мес"},
    {"bank": "Газпромбанк", "rate": 15.5, "term": "6 мес"},
    {"bank": "Райффайзен", "rate": 14.0, "term": "1 год"},
]


async def search_deposit_offers(amount: float, term_days: int) -> dict:
    """Find deposit offers. LLM first; static table as fallback."""
    status = get_llm_status()
    prompt = (
        f"Найди выгодные вклады в банках РФ на сумму {amount:.0f} ₽ "
        f"сроком ~{term_days} дней. Верни JSON-массив объектов "
        f'[{{"bank": str, "rate": number, "term": str, "note": str}}] '
        f"максимум 6 штук. Только JSON."
    )
    if status.available and not breaker.is_open:
        try:
            raw = await complete(prompt, "Ты — ассистент по вкладам. Только валидный JSON.")
            # extract JSON
            start = raw.find("[")
            end = raw.rfind("]")
            if start >= 0 and end > start:
                offers = json.loads(raw[start : end + 1])
                return {"source": status.provider, "offers": offers, "stale": False}
        except Exception as e:  # noqa: BLE001
            logger.warning("deposit search LLM failed: %s", e)

    return {
        "source": "static_fallback",
        "offers": FALLBACK_RATES,
        "stale": True,
        "note": "Ориентировочные ставки — обнови вручную или верни LLM",
    }


async def discuss_deposits(db: AsyncSession, user: User, amount: float, term_days: int) -> str:
    """Conversational analysis of real deposit offers: compare, recommend, warn."""
    search = await search_deposit_offers(amount, term_days)
    offers = search.get("offers", [])
    offers_lines = "\n".join(
        f"- {o.get('bank', '?')}: {o.get('rate', '?')}% · {o.get('term', '?')} · {o.get('note', '')}"
        for o in offers[:8]
    )
    # user's existing deposits for context
    from app.models import Deposit

    my_deps = (
        await db.execute(select(Deposit).where(Deposit.user_id == user.id).limit(5))
    ).scalars().all()
    my_lines = (
        "\n".join(f"- {d.title} ({d.bank}): {d.amount:.0f} ₽ @ {d.rate}% до {d.end_date}" for d in my_deps)
        or "пока нет"
    )

    system = (
        "Ты — финсоветник по вкладам в РФ. Говори по-русски, коротко и по делу. "
        "Считай конкретную сумму дохода. Указывай, что ставки ориентировочные и могут отличаться. "
        "Не выдумывай банки, которых нет в списке."
    )
    prompt = (
        f"Клиент думает разместить {amount:.0f} ₽ на {term_days} дней.\n\n"
        f"Актуальные предложения (из поиска, источник: {search.get('source')}):\n{offers_lines}\n\n"
        f"Его текущие вклады:\n{my_lines}\n\n"
        "Проанализируй: какой вариант выгоднее всего и почему, сколько примерно принесёт доход, "
        "какие риски/нюансы (снятие, капализация, АСВ). Дай 1 рекомендацию."
    )
    if get_llm_status().available and not breaker.is_open:
        try:
            text = await complete(prompt, system)
            header = "💰 <b>Разбор вкладов</b>\n\n" if "<" in text else "💰 Разбор вкладов\n\n"
            return header + text
        except Exception as e:  # noqa: BLE001
            logger.warning("deposit discuss LLM failed: %s", e)

    # fallback: short template analysis
    best = max(offers, key=lambda o: o.get("rate", 0), default=None)
    if best is None:
        return "Не смог найти ставки. Попробуй позже."
    income = amount * float(best.get("rate", 0)) / 100 * (term_days / 365)
    return (
        f"💰 Разбор вкладов (шаблон, LLM недоступен)\n\n"
        f"Лучшая из найденных: {best.get('bank')} — {best.get('rate')}% на {best.get('term')}.\n"
        f"Примерный доход с {amount:.0f} ₽ за {term_days} дн.: ~{income:,.0f} ₽.\n"
        f"Проверь актуальные условия на сайте банка — ставки могут отличаться."
    )


async def save_chat_note(db: AsyncSession, user_id: int, content: str) -> None:
    db.add(LlmNote(user_id=user_id, kind="chat", content=content))
    await db.commit()
