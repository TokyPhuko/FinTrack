"""Parse free-text like «350 кофе», «+50000 зарплата», «перевод 1000 наличные»."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Category, TransactionType

# Prefer space-grouped thousands (1 000 / 1 000.50), else plain digits (50000, 120.50)
AMOUNT_RE = re.compile(
    r"(?P<sign>[+-])?\s*"
    r"(?P<int>"
    r"\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?"  # 1 000 / 12 500.50
    r"|\d+(?:[.,]\d{1,2})?"  # 50000 / 120.50
    r")"
)

TRANSFER_PREFIX_RE = re.compile(
    r"^(?:перевод|перекид|снять|положить)\b", re.IGNORECASE
)
TRANSFER_PARSE_RE = re.compile(
    r"^(?:перевод|перекид|снять|положить)\s+"
    r"(?P<amt>\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?:на\s+)?(?P<to>.*)$",
    re.IGNORECASE,
)

CURRENCY_RE = re.compile(
    r"(?:rub|р\.|руб\.?|₽|долл|usd|\$|евро|eur|€)", re.IGNORECASE
)

KEYWORDS: dict[str, list[str]] = {
    "кафе": ["кофе", "латте", "капучино", "coffee", "кафе", "ресторан", "столов", "обед", "ланч", "ужин"],
    "продукты": ["продукт", "магазин", "супер", "пятероч", "пятёроч", "магнит", "лента", "ашан", "вкусвилл", "перекрёсток", "дикси"],
    "транспорт": ["метро", "такси", "автобус", "транспорт", "проезд", "бензин", "топливо", "парков", "каршеринг", "яндекс.такси"],
    "связь и интернет": ["интернет", "мобил", "связь", "мтс", "билайн", "теле2", "теле2", "мегафон", "яндекс.связь"],
    "подписки": ["подписк", "netflix", "spotify", "kinopoisk", "кинопоиск", "ivi", "иви", "tvplus", "ютуб"],
    "здоровье": ["аптек", "врач", "клиник", "лекар", "здоров", "стомат"],
    "одежда": ["одежд", "обув", "zara", "h&m", "wildberries", "вайлдберриз", "ozon", "озон"],
    "развлечения": ["кино", "игр", "концерт", "бар", "развлечен", "клуб", "квест"],
    "жильё": ["аренд", "кварт", "жиль", "коммунал", "жкх", "свет", "вода", "отоплен"],
    "прочее": ["прочее", "мелоч"],
}


@dataclass
class ParsedMessage:
    tx_type: TransactionType
    amount: float
    category_hint: str
    note: str
    to_account_hint: str = ""


def _normalize_amount(raw: str) -> float:
    s = raw.replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    return float(s)


def parse_message(text: str, default_category_id: int | None = None) -> ParsedMessage | None:
    text = (text or "").strip()
    if not text:
        return None

    cleaned = CURRENCY_RE.sub(" ", text)

    m = AMOUNT_RE.search(cleaned)
    if not m:
        return None

    amount = _normalize_amount(m.group("int"))
    if amount <= 0:
        return None

    sign = m.group("sign") or ""
    remainder = (cleaned[: m.start()] + " " + cleaned[m.end() :]).strip()
    remainder = re.sub(r"\s+", " ", remainder).strip(" .,-–")

    # type by sign / income keywords
    tx_type = TransactionType.expense
    if sign == "+" or re.search(
        r"\b(?:зарплат|доход|получ|продал|вернул|кэшбек|аванс|подработ|фриланс|стипенд)\w*",
        remainder,
        re.I,
    ):
        tx_type = TransactionType.income

    to_account_hint = ""
    note = remainder

    if TRANSFER_PREFIX_RE.match(cleaned):
        tx_type = TransactionType.transfer
        tm = TRANSFER_PARSE_RE.match(cleaned)
        if tm:
            to_account_hint = (tm.group("to") or "").strip()
            note = ""
        if not to_account_hint and re.match(r"^снять\b", cleaned, re.I):
            to_account_hint = "наличн"
            note = ""
        if not to_account_hint and re.match(r"^положить\b", cleaned, re.I):
            note = "положил"

    return ParsedMessage(
        tx_type=tx_type,
        amount=amount,
        category_hint=note,
        note=note or cleaned,
        to_account_hint=to_account_hint,
    )


def match_category(
    hint: str,
    categories: list[Category],
    tx_type: TransactionType,
) -> Category | None:
    if not hint:
        return None
    hint_l = hint.lower().strip()
    if tx_type == TransactionType.transfer:
        want = "expense"
    else:
        want = "expense" if tx_type == TransactionType.expense else "income"
    candidates = [c for c in categories if c.type.value == want and not c.archived]
    if not candidates:
        return None

    # exact
    for c in candidates:
        if c.name.lower() == hint_l:
            return c
    # substring
    for c in candidates:
        n = c.name.lower()
        if n in hint_l or hint_l in n:
            return c
    # word prefix (min 3 chars)
    if len(hint_l) >= 3:
        for c in candidates:
            if c.name.lower().startswith(hint_l[:4]):
                return c
    # keyword → category name
    for cat_name, words in KEYWORDS.items():
        if any(w in hint_l for w in words):
            for c in candidates:
                if c.name.lower() == cat_name:
                    return c
    # category icon/name appears in hint via emoji already in name — skip
    return None


def match_account(hint: str, accounts) -> object | None:
    if not hint:
        return None
    hint_l = hint.lower().strip()
    active = [a for a in accounts if not a.archived]
    for a in active:
        n = a.name.lower()
        if n == hint_l or n in hint_l or hint_l in n:
            return a
    if len(hint_l) >= 3:
        for a in active:
            if a.name.lower().startswith(hint_l[:4]):
                return a
    if "налич" in hint_l:
        for a in active:
            if a.type.value == "cash" or "налич" in a.name.lower():
                return a
    if "кар" in hint_l:
        for a in active:
            if a.type.value == "card" or "карт" in a.name.lower():
                return a
    return None
