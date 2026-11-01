"""Streaks ( Duolingo-style fire ) + pair runs.

A day is "active" if the user recorded any transaction OR did a /check check-in.
Expenses don't happen every day — check-in keeps the fire alive on quiet days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountMember, CheckIn, StreakPair, Transaction, User


async def _active_days(db: AsyncSession, user_id: int, since: date) -> set[date]:
    days: set[date] = set()
    rows = (
        await db.execute(
            select(Transaction.occurred_at).where(
                Transaction.user_id == user_id,
                Transaction.occurred_at >= datetime.combine(since, datetime.min.time()),
            )
        )
    ).scalars().all()
    for dt in rows:
        days.add(dt.date())
    checks = (
        await db.execute(
            select(CheckIn.day).where(CheckIn.user_id == user_id, CheckIn.day >= since)
        )
    ).scalars().all()
    days.update(checks)
    return days


async def current_streak(db: AsyncSession, user_id: int) -> int:
    """Consecutive active days ending today (today not yet done → start from yesterday)."""
    today = date.today()
    since = today - timedelta(days=400)
    days = await _active_days(db, user_id, since)

    cursor = today if today in days else today - timedelta(days=1)
    if cursor not in days:
        return 0
    n = 0
    while cursor in days:
        n += 1
        cursor -= timedelta(days=1)
    return n


async def best_streak(db: AsyncSession, user_id: int) -> int:
    today = date.today()
    since = today - timedelta(days=730)
    days = await _active_days(db, user_id, since)
    if not days:
        return 0
    ordered = sorted(days)
    best = cur = 1
    for a, b in zip(ordered, ordered[1:]):
        if (b - a).days == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


async def checked_today(db: AsyncSession, user_id: int) -> bool:
    row = (
        await db.execute(
            select(CheckIn.id).where(
                CheckIn.user_id == user_id, CheckIn.day == date.today()
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def do_checkin(db: AsyncSession, user_id: int) -> bool:
    """Returns True if new, False if already checked in today."""
    if await checked_today(db, user_id):
        return False
    db.add(CheckIn(user_id=user_id, day=date.today()))
    await db.commit()
    return True


async def streak_info(db: AsyncSession, user: User) -> dict:
    cur = await current_streak(db, user.id)
    best = await best_streak(db, user.id)
    today_done = await checked_today(db, user.id)
    # also active if they logged a transaction today
    if not today_done:
        tx_today = (
            await db.execute(
                select(Transaction.id).where(
                    Transaction.user_id == user.id,
                    Transaction.occurred_at >= datetime.combine(date.today(), datetime.min.time()),
                ).limit(1)
            )
        ).scalar_one_or_none()
        today_done = tx_today is not None
    return {
        "current": cur,
        "best": best,
        "today_done": today_done,
        "username": user.username,
    }


# ---------- pair runs ----------


async def get_active_pair(db: AsyncSession, user_id: int) -> StreakPair | None:
    row = (
        await db.execute(
            select(StreakPair).where(
                StreakPair.status == "active",
                (StreakPair.user1_id == user_id) | (StreakPair.user2_id == user_id),
            )
        )
    ).scalars().first()
    return row


async def get_pending_invites(db: AsyncSession, user_id: int) -> list[StreakPair]:
    return list(
        (
            await db.execute(
                select(StreakPair).where(
                    StreakPair.status == "pending", StreakPair.user2_id == user_id
                )
            )
        ).scalars().all()
    )


async def pair_summary(db: AsyncSession, user: User) -> dict | None:
    """Active pair + both streaks for display."""
    pair = await get_active_pair(db, user.id)
    if pair is None:
        return None
    partner_id = pair.user2_id if pair.user1_id == user.id else pair.user1_id
    partner = await db.get(User, partner_id)
    if partner is None:
        return None
    mine = await streak_info(db, user)
    theirs = await streak_info(db, partner)
    if mine["current"] > theirs["current"]:
        leader = "you"
        diff = mine["current"] - theirs["current"]
    elif theirs["current"] > mine["current"]:
        leader = "partner"
        diff = theirs["current"] - mine["current"]
    else:
        leader = "tie"
        diff = 0
    return {
        "partner": partner.username,
        "you": mine,
        "partner_streak": theirs,
        "leader": leader,
        "diff": diff,
    }


async def visible_account_ids(db: AsyncSession, user_id: int) -> list[int]:
    """Own account ids + ids of shared accounts the user belongs to."""
    from app.models import Account

    own = (await db.execute(select(Account.id).where(Account.user_id == user_id))).scalars().all()
    shared = (
        await db.execute(
            select(AccountMember.account_id).where(AccountMember.user_id == user_id)
        )
    ).scalars().all()
    return list({*own, *shared})
