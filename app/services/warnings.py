"""4 warning types — all local, no LLM required."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Budget,
    Category,
    Deposit,
    PlannedStatus,
    PlannedTransaction,
    Recurrence,
    Transaction,
    TransactionType,
    User,
)

# How many days ahead to look
HORIZON_DAYS = 14


async def get_warnings(db: AsyncSession, user: User) -> list[dict]:
    out: list[dict] = []
    out.extend(await _recurring_and_plans(db, user))
    out.extend(await _deposit_deadlines(db, user))
    out.extend(await _budget_pace(db, user))
    # sort: most urgent first
    priority = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda w: (priority.get(w.get("priority", "low"), 3), w.get("days", 99)))
    return out


async def _recurring_and_plans(db: AsyncSession, user: User) -> list[dict]:
    """Overdue + upcoming planned transactions (including recurring)."""
    today = date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    rows = (
        await db.execute(
            select(PlannedTransaction)
            .where(
                PlannedTransaction.user_id == user.id,
                PlannedTransaction.status == PlannedStatus.pending,
                PlannedTransaction.due_date <= horizon,
            )
            .order_by(PlannedTransaction.due_date)
        )
    ).scalars().all()

    out = []
    for p in rows:
        days = (p.due_date - today).days
        if days < 0:
            priority = "high"
            kind = "plan_overdue"
            title = f"Просрочен план: {p.title}"
        elif days == 0:
            priority = "high"
            kind = "plan_today"
            title = f"Сегодня по плану: {p.title}"
        elif days <= 3:
            priority = "medium"
            kind = "plan_soon"
            title = f"Через {days} дн.: {p.title}"
        else:
            priority = "low"
            kind = "plan_upcoming"
            title = f"Через {days} дн.: {p.title}"

        # recurring payments get their own kind
        if p.is_recurring and days >= 0:
            kind = "recurring"
            title = (
                f"Регулярный платёж: {p.title} "
                f"({p.recurrence.value}, {p.due_date.strftime('%d.%m')})"
            )

        out.append(
            {
                "kind": kind,
                "title": title,
                "days": days,
                "priority": priority,
                "amount": p.amount,
                "plan_id": p.id,
                "due_date": p.due_date.isoformat(),
            }
        )
    return out


async def _deposit_deadlines(db: AsyncSession, user: User) -> list[dict]:
    today = date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    rows = (
        await db.execute(
            select(Deposit)
            .where(
                Deposit.user_id == user.id,
                Deposit.end_date <= horizon,
                Deposit.end_date >= today,
            )
            .order_by(Deposit.end_date)
        )
    ).scalars().all()
    out = []
    for d in rows:
        days = (d.end_date - today).days
        priority = "high" if days <= 3 else "medium"
        title = (
            f"Вклад «{d.title}» заканчивается через {days} дн."
            if days > 0
            else f"Вклад «{d.title}» сегодня!"
        )
        out.append(
            {
                "kind": "deposit_end",
                "title": title,
                "days": days,
                "priority": priority,
                "amount": d.amount,
                "deposit_id": d.id,
                "due_date": d.end_date.isoformat(),
            }
        )
    # already ended, no renew
    ended = (
        await db.execute(
            select(Deposit).where(
                Deposit.user_id == user.id,
                Deposit.end_date < today,
                Deposit.auto_renew.is_(False),
            )
        )
    ).scalars().all()
    for d in ended:
        out.append(
            {
                "kind": "deposit_ended",
                "title": f"Вклад «{d.title}» уже закончился ({d.end_date.strftime('%d.%m.%Y')})",
                "days": (d.end_date - today).days,
                "priority": "medium",
                "amount": d.amount,
                "deposit_id": d.id,
                "due_date": d.end_date.isoformat(),
            }
        )
    return out


async def _budget_pace(db: AsyncSession, user: User) -> list[dict]:
    """Forecast budget overrun based on month pace (no LLM)."""
    now = datetime.now()
    month = now.strftime("%Y-%m")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = 30 if now.month == 2 else (31 if now.month in (1, 3, 5, 7, 8, 10, 12) else 30)
    if now.month == 2:
        days_in_month = 29 if now.year % 4 == 0 else 28
    day_of_month = now.day
    pace = day_of_month / days_in_month

    # budgets: explicit for this month OR category.monthly_limit
    budget_rows = (
        await db.execute(
            select(Budget, Category)
            .join(Category, Category.id == Budget.category_id)
            .where(Budget.user_id == user.id, Budget.month == month)
        )
    ).all()
    limits: dict[int, tuple[str, float]] = {}
    for b, cat in budget_rows:
        limits[cat.id] = (cat.name, b.amount)

    # category limits as fallback
    cats = (
        await db.execute(
            select(Category).where(
                Category.user_id == user.id,
                Category.archived.is_(False),
                Category.monthly_limit.is_not(None),
            )
        )
    ).scalars().all()
    for c in cats:
        if c.id not in limits and c.monthly_limit:
            limits[c.id] = (c.name, c.monthly_limit)

    if not limits:
        return []

    spent_rows = (
        await db.execute(
            select(
                Transaction.category_id,
                func.coalesce(func.sum(Transaction.amount), 0.0),
            )
            .where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.expense,
                Transaction.occurred_at >= month_start,
                Transaction.category_id.in_(list(limits.keys())),
            )
            .group_by(Transaction.category_id)
        )
    ).all()
    spent = dict(spent_rows)

    out = []
    for cat_id, (name, limit) in limits.items():
        used = spent.get(cat_id, 0.0)
        if limit <= 0:
            continue
        projected = used / pace if pace > 0 else used
        ratio = used / limit
        proj_ratio = projected / limit

        if used >= limit:
            out.append(
                {
                    "kind": "budget_exceeded",
                    "title": f"Бюджет «{name}» превышен: {used:.0f} из {limit:.0f}",
                    "days": 0,
                    "priority": "high",
                    "amount": round(used - limit, 2),
                    "category_id": cat_id,
                }
            )
        elif proj_ratio >= 1.0 and ratio >= 0.5:
            over = projected - limit
            out.append(
                {
                    "kind": "budget_forecast",
                    "title": (
                        f"«{name}»: по темпу превысишь бюджет "
                        f"на {over:.0f} ₽ к концу месяца"
                    ),
                    "days": days_in_month - day_of_month,
                    "priority": "medium" if proj_ratio < 1.3 else "high",
                    "amount": round(over, 2),
                    "category_id": cat_id,
                }
            )
        elif ratio >= 0.8:
            out.append(
                {
                    "kind": "budget_near",
                    "title": f"«{name}»: израсходовано {ratio:.0%} бюджета ({used:.0f}/{limit:.0f})",
                    "days": days_in_month - day_of_month,
                    "priority": "medium",
                    "amount": round(limit - used, 2),
                    "category_id": cat_id,
                }
            )
    return out
