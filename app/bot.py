"""Telegram bot: quick entry, undo, stats. Runs in-process with FastAPI when token set."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy import delete, func, select

from app.auth import hash_password
from app.config import get_settings
from app.database import async_session_factory
from app.models import Account, Category, Transaction, TransactionType, User
from app.services.ledger import apply_balance, revert_balance
from app.services.parser import match_account, match_category, parse_message

logger = logging.getLogger("fintrack.bot")
router = Router(name="fintrack")
settings = get_settings()

HELP_TEXT = (
    "Привет! Я FinTrack — учёт транзакций и финансовый помощник.\n\n"
    "Траты и доходы:\n"
    "• <b>350 кофе</b>\n"
    "• <b>+50000 зарплата</b>\n"
    "• <b>перевод 1000 наличные</b> (не расход)\n\n"
    "Просто спроси что угодно — отвечу на основе твоих данных:\n"
    "• <b>сколько потратил?</b>\n"
    "• <b>где больше всего ушло?</b>\n"
    "• <b>баланс?</b>\n\n"
    "Счета и категории:\n"
    "/accounts — список счетов\n"
    "/account add Название [card|cash|savings] [сумма]\n"
    "/account share Название username — совместный счёт\n"
    "/cats — список категорий\n"
    "/cat add Название [expense|income] [иконка]\n"
    "/cat limit Название 5000 — лимит категории\n"
    "/budget Название 5000 — бюджет на месяц\n\n"
    "Стрики и пары (как в Дуолинго):\n"
    "/check — чек-ин дня (держит огонёк)\n"
    "/streak — твой огонёк и пара\n"
    "/pair username — пригласить в парный забег\n"
    "/pair accept|decline — ответить\n\n"
    "Отчёты:\n"
    "/stats — сводка за месяц\n"
    "/report — разбор за неделю\n"
    "/xlsx — Excel-отчёт в чат\n"
    "/last — последние операции\n"
    "/undo — отменить последнюю\n"
    "/help — эта справка"
)

QUESTION_RE = re.compile(
    r"(?:\?|\bсколько\b|\bпочему\b|\bзачем\b|\bчто\b|\bгде\b|\bкогда\b|\bкак\b|\bпроанализируй\b|\bразбери\b|\bсравни\b|\bсовет\b|\bпосоветуй\b|\bбаланс\b|\bитог\b|\bсводк\w*\b|\bрасход\w*\b|\bдоход\w*\b)",
    re.IGNORECASE,
)

# Global bot instance for sending files from API (report → Telegram)
_bot_instance: Bot | None = None


async def send_document_to_user(telegram_id: int, data: bytes, filename: str) -> None:
    """Send a file to a Telegram chat via the running bot."""
    from aiogram.types import BufferedInputFile

    if _bot_instance is None:
        raise RuntimeError("Бот не запущен")
    await _bot_instance.send_document(
        telegram_id,
        BufferedInputFile(data, filename=filename),
        caption=f"📊 Отчёт {filename}",
    )


async def _ensure_user_by_telegram(tg_id: int, username: str | None) -> User | None:
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        # First linked Telegram user becomes the main account (single-user mode)
        result = await db.execute(select(User).limit(1))
        main = result.scalar_one_or_none()
        if main is None:
            main = User(
                username=username or str(tg_id),
                password_hash=hash_password(settings.admin_password),
                telegram_id=tg_id,
            )
            db.add(main)
            await db.commit()
            await db.refresh(main)
            return main

        # Do not auto-claim an existing web user — require explicit bind
        return None


async def _get_user(db, tg_id: int) -> User | None:
    result = await db.execute(select(User).where(User.telegram_id == tg_id))
    return result.scalar_one_or_none()


async def _default_account(db, user: User) -> Account | None:
    from app.services.streaks import visible_account_ids

    ids = await visible_account_ids(db, user.id)
    if not ids:
        return None
    result = await db.execute(
        select(Account)
        .where(Account.id.in_(ids), Account.archived.is_(False))
        .order_by(Account.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    assert message.from_user is not None
    tg_id = message.from_user.id

    # Mini App button — from settings (pydantic reads .env)
    app_url = settings.mini_app_url.strip()
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Открыть FinTrack", web_app=WebAppInfo(url=app_url))]
            ]
        )

    async with async_session_factory() as db:
        user = await _get_user(db, tg_id)
        if user is None:
            # auto-bind if single user without telegram yet
            result = await db.execute(select(User).limit(1))
            main = result.scalar_one_or_none()
            if main is not None and main.telegram_id is None:
                main.telegram_id = tg_id
                await db.commit()
                await message.answer(
                    f"Привет! Аккаунт <b>{main.username}</b> привязан к Telegram.\n\n{HELP_TEXT}",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                return
            await message.answer(
                "Этот Telegram ещё не привязан. Открой веб-приложение и привяжи аккаунт "
                "в настройках, или напиши /bind с паролем: <code>/bind пароль</code>",
                parse_mode="HTML",
            )
            return
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("bind"))
async def cmd_bind(message: Message) -> None:
    assert message.from_user is not None and message.text
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/bind пароль</code>", parse_mode="HTML")
        return
    from app.auth import verify_password

    async with async_session_factory() as db:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(parts[1], user.password_hash):
            await message.answer("Неверный пароль.")
            return
        user.telegram_id = message.from_user.id
        await db.commit()
    await message.answer(f"Готово! Аккаунт <b>{user.username}</b> привязан.\n\n{HELP_TEXT}", parse_mode="HTML")


@router.message(Command("xlsx"))
async def cmd_xlsx(message: Message) -> None:
    """Send Excel report to this chat."""
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from datetime import datetime as _dt

        from app.services.excel import build_workbook

        month = _dt.now().strftime("%Y-%m")
        try:
            data = await build_workbook(db, user, month)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Ошибка сборки отчёта: {e}")
            return
    from aiogram.types import BufferedInputFile

    await message.answer_document(
        BufferedInputFile(data, filename=f"fintrack_{month}.xlsx"),
        caption=f"📊 Excel-отчёт за {month}",
    )


@router.message(Command("accounts"))
async def cmd_accounts(message: Message) -> None:
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.streaks import visible_account_ids

        ids = await visible_account_ids(db, user.id)
        if not ids:
            await message.answer("Счетов нет. /account add Название")
            return
        rows = (
            await db.execute(select(Account).where(Account.id.in_(ids), Account.archived.is_(False)).order_by(Account.name))
        ).scalars().all()
        lines = ["💳 <b>Счета:</b>"]
        for a in rows:
            mark = "" if a.user_id == user.id else " · совместный"
            lines.append(f"  • {a.name} ({a.type.value}): {a.balance:g} {a.currency}{mark}")
        lines.append("\n/add: /account add Название [card|cash|savings] [сумма]")
        lines.append("Шаринг: /account share Название username")
        await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("account"))
async def cmd_account(message: Message) -> None:
    assert message.from_user is not None and message.text
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование:\n"
            "<code>/account add Название [card|cash|savings] [сумма]</code>\n"
            "<code>/account share Название username</code>",
            parse_mode="HTML",
        )
        return
    sub = parts[1].strip().split(maxsplit=1)
    action = sub[0].lower()
    rest = sub[1] if len(sub) > 1 else ""

    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return

        if action == "add":
            bits = rest.split()
            if not bits:
                await message.answer("Укажи название: /account add Наличные cash 5000")
                return
            # last token may be amount, second-to-last may be type
            balance = 0.0
            atype = "card"
            name_parts = list(bits)
            if len(bits) >= 1:
                try:
                    balance = float(bits[-1].replace(",", "."))
                    name_parts = bits[:-1]
                except ValueError:
                    pass
            if name_parts and name_parts[-1].lower() in ("card", "cash", "savings", "deposit"):
                atype = name_parts[-1].lower()
                name_parts = name_parts[:-1]
            name = " ".join(name_parts).strip()
            if not name:
                await message.answer("Пустое название.")
                return
            from app.models import AccountType

            acc = Account(
                user_id=user.id,
                name=name,
                type=AccountType(atype),
                balance=balance,
            )
            db.add(acc)
            await db.commit()
            await message.answer(f"✅ Счёт «{name}» создан ({atype}, {balance:g}).")
            return

        if action == "share":
            bits = rest.split()
            if len(bits) < 2:
                await message.answer("Использование: /account share Название username")
                return
            # last is username, rest is account name
            target_uname = bits[-1].lstrip("@")
            acc_name = " ".join(bits[:-1])
            acc = (
                await db.execute(
                    select(Account).where(
                        Account.user_id == user.id,
                        Account.name.ilike(f"%{acc_name}%"),
                        Account.archived.is_(False),
                    )
                )
            ).scalars().first()
            if acc is None:
                await message.answer(f"Счёт «{acc_name}» не найден среди твоих.")
                return
            target = (
                await db.execute(select(User).where(User.username == target_uname))
            ).scalar_one_or_none()
            if target is None:
                await message.answer(f"Пользователь «{target_uname}» не найден.")
                return
            if target.id == user.id:
                await message.answer("Нельзя шарить самому себе.")
                return
            from app.models import AccountMember

            exists = (
                await db.execute(
                    select(AccountMember.id).where(
                        AccountMember.account_id == acc.id,
                        AccountMember.user_id == target.id,
                    )
                )
            ).scalar_one_or_none()
            if exists:
                await message.answer("Уже участник.")
                return
            db.add(AccountMember(account_id=acc.id, user_id=target.id, role="member"))
            await db.commit()
            await message.answer(f"🤝 Счёт «{acc.name}» открыт для {target.username}.")
            return

        await message.answer("Неизвестное действие. /account add|share …")


@router.message(Command("cats"))
async def cmd_cats(message: Message) -> None:
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        rows = (
            await db.execute(
                select(Category)
                .where(Category.user_id == user.id, Category.archived.is_(False))
                .order_by(Category.type, Category.name)
            )
        ).scalars().all()
        if not rows:
            await message.answer("Категорий нет. /cat add Продукты expense 🛒")
            return
        lines = ["🏷 <b>Категории:</b>"]
        for c in rows:
            lim = f" · лимит {c.monthly_limit:g}" if c.monthly_limit else ""
            ico = f" {c.icon}" if c.icon else ""
            lines.append(f"  [{c.type.value}] {c.name}{ico}{lim}")
        lines.append("\n/add: /cat add Название [expense|income] [иконка]")
        lines.append("Лимит: /cat limit Название 5000")
        await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("cat"))
async def cmd_cat(message: Message) -> None:
    assert message.from_user is not None and message.text
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование:\n"
            "<code>/cat add Название [expense|income] [иконка]</code>\n"
            "<code>/cat limit Название 5000</code>",
            parse_mode="HTML",
        )
        return
    sub = parts[1].strip().split(maxsplit=1)
    action = sub[0].lower()
    rest = sub[1] if len(sub) > 1 else ""

    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return

        if action == "add":
            bits = rest.split()
            if not bits:
                await message.answer("Укажи название: /cat add Занятия expense 🏀")
                return
            ctype = "expense"
            icon = ""
            name_parts = list(bits)
            if bits[-1].lower() in ("expense", "income"):
                ctype = bits[-1].lower()
                name_parts = bits[:-1]
            # icon: last non-type token starting with emoji-ish or short
            if name_parts and len(name_parts[-1]) <= 4 and not name_parts[-1].isascii():
                icon = name_parts[-1]
                name_parts = name_parts[:-1]
            name = " ".join(name_parts).strip()
            if not name:
                await message.answer("Пустое название.")
                return
            from app.models import CategoryType

            cat = Category(user_id=user.id, name=name, type=CategoryType(ctype), icon=icon)
            db.add(cat)
            await db.commit()
            await message.answer(f"✅ Категория «{name}» ({ctype}) создана.")
            return

        if action == "limit":
            bits = rest.rsplit(maxsplit=1)
            if len(bits) < 2:
                await message.answer("Использование: /cat limit Название 5000")
                return
            try:
                limit = float(bits[1].replace(",", "."))
            except ValueError:
                await message.answer("Сумма должна быть числом.")
                return
            cat = (
                await db.execute(
                    select(Category).where(
                        Category.user_id == user.id,
                        Category.name.ilike(f"%{bits[0]}%"),
                        Category.archived.is_(False),
                    )
                )
            ).scalars().first()
            if cat is None:
                await message.answer(f"Категория «{bits[0]}» не найдена.")
                return
            cat.monthly_limit = limit
            await db.commit()
            await message.answer(f"✅ Лимит «{cat.name}» = {limit:g} ₽/мес.")
            return

        await message.answer("Неизвестное действие. /cat add|limit …")


@router.message(Command("budget"))
async def cmd_budget(message: Message) -> None:
    assert message.from_user is not None and message.text
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/budget Название 5000</code>", parse_mode="HTML")
        return
    bits = parts[1].rsplit(maxsplit=1)
    if len(bits) < 2:
        await message.answer("Использование: /budget Название 5000")
        return
    try:
        amount = float(bits[1].replace(",", "."))
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return

    from datetime import datetime as _dt

    from app.models import Budget

    month = _dt.now().strftime("%Y-%m")
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        cat = (
            await db.execute(
                select(Category).where(
                    Category.user_id == user.id,
                    Category.name.ilike(f"%{bits[0]}%"),
                    Category.archived.is_(False),
                )
            )
        ).scalars().first()
        if cat is None:
            await message.answer(f"Категория «{bits[0]}» не найдена. /cats")
            return
        existing = (
            await db.execute(
                select(Budget).where(
                    Budget.user_id == user.id,
                    Budget.category_id == cat.id,
                    Budget.month == month,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.amount = amount
        else:
            db.add(Budget(user_id=user.id, category_id=cat.id, month=month, amount=amount))
        await db.commit()
        await message.answer(f"✅ Бюджет «{cat.name}» на {month}: {amount:g} ₽")


@router.message(Command("check"))
async def cmd_check(message: Message) -> None:
    """Daily check-in — keeps the streak fire alive."""
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.streaks import do_checkin, streak_info

        is_new = await do_checkin(db, user.id)
        info = await streak_info(db, user)
    if is_new:
        fire = "🔥" * min(info["current"], 10)
        await message.answer(
            f"✅ Чек-ин принят!\n"
            f"{fire} Серия: <b>{info['current']}</b> дн. (рекорд {info['best']})",
            parse_mode="HTML",
        )
    else:
        await message.answer("Уже чек-инился сегодня. Записи трат тоже засчитываются.")


@router.message(Command("streak"))
async def cmd_streak(message: Message) -> None:
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.streaks import pair_summary, streak_info

        info = await streak_info(db, user)
        pair = await pair_summary(db, user)

    fire = "🔥" * min(info["current"], 10) or "—"
    lines = [
        f"🔥 <b>Серия: {info['current']} дн.</b>",
        f"Рекорд: {info['best']}",
        f"Сегодня: {'✅' if info['today_done'] else '❌ (напиши /check)'}",
    ]
    if pair:
        you = pair["you"]["current"]
        them = pair["partner_streak"]["current"]
        if pair["leader"] == "you":
            head = f"🏆 Ты впереди на {pair['diff']} дн."
        elif pair["leader"] == "partner":
            head = f"😤 {pair['partner']} впереди на {pair['diff']} дн."
        else:
            head = "🤝 Ничья!"
        lines.append(f"\n👥 Пара с {pair['partner']}:")
        lines.append(f"  Ты: {you} · {pair['partner']}: {them}")
        lines.append(f"  {head}")
    else:
        lines.append("\nПары нет — пригласи: /pair username")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("pair"))
async def cmd_pair(message: Message) -> None:
    assert message.from_user is not None and message.text
    parts = message.text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.models import StreakPair
        from app.services.streaks import get_active_pair, get_pending_invites

        if not arg:
            pair = await get_active_pair(db, user.id)
            if pair:
                partner_id = pair.user2_id if pair.user1_id == user.id else pair.user1_id
                partner = await db.get(User, partner_id)
                await message.answer(f"👥 Активная пара: {partner.username if partner else '?'}\n/endpair — расторгнуть")
            else:
                invites = await get_pending_invites(db, user.id)
                if invites:
                    inv = await db.get(User, invites[0].user1_id)
                    await message.answer(
                        f"Входящее приглашение от {inv.username if inv else '?'}\n"
                        "/pair accept или /pair decline"
                    )
                else:
                    await message.answer("Пары нет. Пригласи: /pair username")
            return

        if arg.lower() in ("accept", "decline"):
            invites = await get_pending_invites(db, user.id)
            if not invites:
                await message.answer("Нет входящих приглашений.")
                return
            p = invites[0]
            from datetime import datetime

            if arg.lower() == "accept":
                p.status = "active"
                await db.commit()
                await message.answer("👥 Парный забег начат! Держи огонёк: /check каждый день.")
            else:
                p.status = "declined"
                await db.commit()
                await message.answer("Отклонено.")
            return

        if arg.lower() in ("leave", "end", "endpair"):
            pair = await get_active_pair(db, user.id)
            if not pair:
                await message.answer("Активной пары нет.")
                return
            pair.status = "ended"
            await db.commit()
            await message.answer("Пара завершена.")
            return

        target_uname = arg.lstrip("@")
        target = (
            await db.execute(select(User).where(User.username == target_uname))
        ).scalar_one_or_none()
        if target is None:
            await message.answer(f"Пользователь «{target_uname}» не найден.")
            return
        if target.id == user.id:
            await message.answer("Нельзя пригласить себя.")
            return
        active = await get_active_pair(db, user.id)
        if active:
            await message.answer("У тебя уже есть активная пара.")
            return
        # pending either way
        pend = (
            await db.execute(
                select(StreakPair.id).where(
                    StreakPair.status == "pending",
                    (StreakPair.user1_id == user.id) & (StreakPair.user2_id == target.id)
                    | (StreakPair.user1_id == target.id) & (StreakPair.user2_id == user.id),
                )
            )
        ).scalar_one_or_none()
        if pend:
            await message.answer("Приглашение уже отправлено.")
            return
        db.add(StreakPair(user1_id=user.id, user2_id=target.id, status="pending"))
        await db.commit()
        await message.answer(f"📨 Приглашение отправлено {target.username}.\nОн ответит: /pair accept")


@router.message(Command("xlsx_report"))
async def _xlsx_alias(message: Message) -> None:
    await cmd_xlsx(message)


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    """Weekly-style report (LLM or template fallback)."""
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.llm import weekly_report

        text = await weekly_report(db, user)
    await message.answer(text)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    assert message.from_user is not None
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.streaks import visible_account_ids

        ids = await visible_account_ids(db, user.id)

        def _sum(t: TransactionType) -> float:
            cond = (Transaction.user_id == user.id) | (
                Transaction.account_id.in_(ids) if ids else False
            )
            q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                cond,
                Transaction.type == t,
                Transaction.occurred_at >= month_start,
            )
            return q

        income = (await db.execute(_sum(TransactionType.income))).scalar() or 0.0
        expense = (await db.execute(_sum(TransactionType.expense))).scalar() or 0.0
        transfers = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    (Transaction.user_id == user.id)
                    | (Transaction.account_id.in_(ids) if ids else False),
                    Transaction.type == TransactionType.transfer,
                    Transaction.occurred_at >= month_start,
                )
            )
        ).scalar() or 0
        accounts = (
            await db.execute(
                select(Account).where(Account.id.in_(ids) if ids else Account.id == -1, Account.archived.is_(False))
            )
        ).scalars().all() if ids else []
        total = sum(a.balance for a in accounts)

        # top expense categories
        top = (
            await db.execute(
                select(Category.name, func.sum(Transaction.amount).label("s"))
                .join(Transaction, Transaction.category_id == Category.id)
                .where(
                    (Transaction.user_id == user.id)
                    | (Transaction.account_id.in_(ids) if ids else False),
                    Transaction.type == TransactionType.expense,
                    Transaction.occurred_at >= month_start,
                )
                .group_by(Category.name)
                .order_by(func.sum(Transaction.amount).desc())
                .limit(5)
            )
        ).all()

    def money(n: float) -> str:
        return f"{n:,.2f}".replace(",", " ")

    lines = [
        f"📊 <b>{now.strftime('%Y-%m')}</b>",
        f"Доход: <b>+{money(income)}</b>",
        f"Расход: <b>−{money(expense)}</b>",
        f"Чистыми: <b>{money(income - expense)}</b>",
        f"Баланс: <b>{money(total)}</b>",
    ]
    if transfers:
        lines.append(f"Переводов: {transfers} (не расходы)")
    if top:
        lines.append("\nТоп расходов:")
        for name, s in top:
            lines.append(f"  • {name}: {money(s)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("last"))
async def cmd_last(message: Message) -> None:
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        from app.services.streaks import visible_account_ids

        ids = await visible_account_ids(db, user.id)
        rows = (
            await db.execute(
                select(Transaction)
                .where(
                    (Transaction.user_id == user.id)
                    | (Transaction.account_id.in_(ids) if ids else False)
                )
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        await message.answer("Пока пусто.")
        return
    lines = ["Последние операции:"]
    for t in rows:
        if t.type == TransactionType.income:
            label = f"+{t.amount:g}"
        elif t.type == TransactionType.expense:
            label = f"−{t.amount:g}"
        else:
            label = f"⇄ {t.amount:g}"
        note = f" · {t.note}" if t.note else ""
        lines.append(f"#{t.id} {t.occurred_at.strftime('%d.%m')} {label}{note}")
    lines.append("\n/undo — отменить последнюю")
    await message.answer("\n".join(lines))


@router.message(Command("undo"))
async def cmd_undo(message: Message) -> None:
    assert message.from_user is not None
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return
        result = await db.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(Transaction.id.desc())
            .limit(1)
        )
        tx = result.scalar_one_or_none()
        if tx is None:
            await message.answer("Отменять нечего.")
            return
        note = tx.note
        amount = tx.amount
        ttype = tx.type
        await revert_balance(db, tx)
        await db.delete(tx)
        await db.commit()

    emoji = {"expense": "🗑", "income": "🗑", "transfer": "🗑"}.get(ttype.value, "🗑")
    await message.answer(f"{emoji} Отменено: {ttype.value} {amount:g} ({note or 'без заметки'})")


@router.message(F.text)
async def on_text(message: Message) -> None:
    assert message.from_user is not None and message.text
    text = message.text.strip()
    if text.startswith("/"):
        await message.answer("Неизвестная команда. /help")
        return

    # Get user first — needed for both chat and transactions
    async with async_session_factory() as db:
        user = await _get_user(db, message.from_user.id)
        if user is None:
            await message.answer("Сначала /start или /bind пароль.")
            return

        parsed = parse_message(text)
        looks_like_question = bool(QUESTION_RE.search(text))

        # Route to AI chat: no amount parsed, or clearly a question
        if parsed is None or looks_like_question:
            from app.services.llm import chat_analyze, get_llm_status

            try:
                answer = await chat_analyze(db, user, text)
                await message.answer(answer)
            except Exception as e:  # noqa: BLE001
                st = get_llm_status()
                await message.answer(
                    f"Не смог ответить: {e}\n"
                    f"Статус LLM: {st.provider} — {st.reason or 'ok'}"
                )
            return

        categories = (
            await db.execute(
                select(Category).where(
                    Category.user_id == user.id,
                    Category.archived.is_(False),
                )
            )
        ).scalars().all()
        from app.services.streaks import visible_account_ids

        acc_ids = await visible_account_ids(db, user.id)
        accounts = (
            await db.execute(
                select(Account).where(
                    Account.id.in_(acc_ids) if acc_ids else Account.id == -1,
                    Account.archived.is_(False),
                )
            )
        ).scalars().all()
        account = await _default_account(db, user)
        if account is None:
            await message.answer("Нет счёта — создай его в вебе (Счета).")
            return

        tx = Transaction(
            user_id=user.id,
            type=parsed.tx_type,
            amount=parsed.amount,
            category_id=None,
            account_id=account.id,
            transfer_account_id=None,
            note=parsed.note,
            occurred_at=datetime.now(),
            source="bot",
        )

        if parsed.tx_type == TransactionType.transfer:
            target = match_account(parsed.to_account_hint, accounts)
            if target is None or target.id == account.id:
                for a in accounts:
                    if a.id != account.id:
                        target = a
                        break
                else:
                    target = None
            if target is None:
                await message.answer("Нет второго счёта для перевода — создай его в вебе.")
                return
            tx.transfer_account_id = target.id
            tx.category_id = None
            await apply_balance(db, tx)
            db.add(tx)
            await db.commit()
            await message.answer(
                f"⇄ Перевод {parsed.amount:g} · {account.name} → {target.name}\n"
                f"<i>не считается расходом</i>\n/undo",
                parse_mode="HTML",
            )
            return

        cat = match_category(parsed.category_hint, categories, parsed.tx_type)
        if cat is None:
            want = "expense" if parsed.tx_type == TransactionType.expense else "income"
            fallback = [c for c in categories if c.type.value == want]
            cat = next((c for c in fallback if c.name.lower() == "прочее"), None) or (
                fallback[0] if fallback else None
            )
        if cat is None:
            await message.answer("Нет категорий — создай их в вебе.")
            return

        tx.category_id = cat.id
        await apply_balance(db, tx)
        db.add(tx)
        await db.commit()
        await db.refresh(tx)
        roast = None
        if tx.type == TransactionType.expense:
            try:
                from app.services.llm import roast_after_expense

                roast = await roast_after_expense(db, user, tx.amount, cat.name)
            except Exception:  # noqa: BLE001
                roast = None

    emoji = "💸" if parsed.tx_type == TransactionType.expense else "💰"
    reply = f"{emoji} {parsed.tx_type.value} {parsed.amount:g} · {cat.name}\n<i>{tx.note}</i>\n/undo"
    if roast:
        reply += f"\n\n{roast}"
    await message.answer(reply, parse_mode="HTML")


async def poll_bot() -> None:
    global _bot_instance
    token = settings.telegram_bot_token.strip()
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    bot = Bot(token=token)
    _bot_instance = bot
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Telegram bot starting (long polling)")
    try:
        await dp.start_polling(bot)
    finally:
        _bot_instance = None
        await bot.session.close()


def start_bot_task() -> "asyncio.Task | None":
    if not settings.telegram_bot_token.strip():
        logger.info("Bot disabled: no TELEGRAM_BOT_TOKEN")
        return None
    return asyncio.create_task(poll_bot(), name="telegram-bot")
