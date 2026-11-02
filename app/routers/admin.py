"""Admin API: list/manage users, view their data."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_admin_user, hash_password
from app.database import get_db
from app.models import (
    Account,
    Budget,
    Category,
    Deposit,
    PlannedTransaction,
    Transaction,
    TransactionType,
    User,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_admin_user)])


class PasswordIn(BaseModel):
    password: str = Field(min_length=3, max_length=128)


class PolitenessIn(BaseModel):
    level: int = Field(ge=0, le=3)


def _user_row(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "telegram_id": u.telegram_id,
        "is_admin": u.is_admin,
        "is_active": u.is_active,
        "llm_politeness": u.llm_politeness,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/users")
async def list_users(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    users = (
        await db.execute(select(User).order_by(User.is_admin.desc(), User.id))
    ).scalars().all()
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = []
    for u in users:
        tx_count = (
            await db.execute(
                select(func.count(Transaction.id)).where(Transaction.user_id == u.id)
            )
        ).scalar() or 0
        acc_count = (
            await db.execute(
                select(func.count(Account.id)).where(Account.user_id == u.id, Account.archived.is_(False))
            )
        ).scalar() or 0
        balance = (
            await db.execute(
                select(func.coalesce(func.sum(Account.balance), 0.0)).where(
                    Account.user_id == u.id, Account.archived.is_(False)
                )
            )
        ).scalar() or 0.0
        expense_m = (
            await db.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.user_id == u.id,
                    Transaction.type == TransactionType.expense,
                    Transaction.occurred_at >= month_start,
                )
            )
        ).scalar() or 0.0
        out.append(
            {
                **_user_row(u),
                "accounts": acc_count,
                "transactions": tx_count,
                "balance": round(balance, 2),
                "expense_month": round(expense_m, 2),
            }
        )
    return out


@router.get("/users/{uid}")
async def user_detail(
    uid: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    accounts = (
        await db.execute(
            select(Account).where(Account.user_id == uid).order_by(Account.name)
        )
    ).scalars().all()
    categories = (
        await db.execute(
            select(Category).where(Category.user_id == uid).order_by(Category.type, Category.name)
        )
    ).scalars().all()
    recent = (
        await db.execute(
            select(Transaction)
            .where(Transaction.user_id == uid)
            .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            .limit(30)
        )
    ).scalars().all()
    budgets = (
        await db.execute(select(Budget).where(Budget.user_id == uid))
    ).scalars().all()
    deposits = (
        await db.execute(select(Deposit).where(Deposit.user_id == uid))
    ).scalars().all()
    plans = (
        await db.execute(
            select(PlannedTransaction)
            .where(PlannedTransaction.user_id == uid)
            .order_by(PlannedTransaction.due_date)
            .limit(20)
        )
    ).scalars().all()

    total_balance = sum(a.balance for a in accounts if not a.archived)

    return {
        "user": _user_row(u),
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type.value,
                "balance": a.balance,
                "currency": a.currency,
                "archived": a.archived,
            }
            for a in accounts
        ],
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "type": c.type.value,
                "icon": c.icon,
                "monthly_limit": c.monthly_limit,
                "archived": c.archived,
            }
            for c in categories
        ],
        "transactions": [
            {
                "id": t.id,
                "type": t.type.value,
                "amount": t.amount,
                "note": t.note,
                "occurred_at": t.occurred_at.isoformat() if t.occurred_at else None,
                "source": t.source,
            }
            for t in recent
        ],
        "budgets": [
            {"category_id": b.category_id, "month": b.month, "amount": b.amount}
            for b in budgets
        ],
        "deposits": [
            {
                "id": d.id,
                "title": d.title,
                "bank": d.bank,
                "amount": d.amount,
                "rate": d.rate,
                "end_date": d.end_date.isoformat(),
            }
            for d in deposits
        ],
        "plans": [
            {
                "id": p.id,
                "title": p.title,
                "amount": p.amount,
                "due_date": p.due_date.isoformat(),
                "status": p.status.value,
            }
            for p in plans
        ],
        "total_balance": round(total_balance, 2),
    }


@router.post("/users/{uid}/password")
async def reset_password(
    uid: int,
    body: PasswordIn,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    u.password_hash = hash_password(body.password)
    await db.commit()
    return {"ok": True, "username": u.username}


@router.post("/users/{uid}/toggle-active")
async def toggle_active(
    uid: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать себя")
    if u.is_admin and u.username == admin.username:
        raise HTTPException(status_code=400, detail="Нельзя")
    u.is_active = not u.is_active
    await db.commit()
    return {"ok": True, "is_active": u.is_active}


@router.post("/users/{uid}/unbind-telegram")
async def unbind_telegram(
    uid: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    u.telegram_id = None
    await db.commit()
    return {"ok": True}


@router.post("/users/{uid}/politeness")
async def set_politeness(
    uid: int,
    body: PolitenessIn,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    u.llm_politeness = body.level
    await db.commit()
    return {"ok": True, "llm_politeness": u.llm_politeness}


@router.post("/users/{uid}/toggle-admin")
async def toggle_admin(
    uid: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя снять админа с себя")
    u.is_admin = not u.is_admin
    await db.commit()
    return {"ok": True, "is_admin": u.is_admin}


@router.post("/users/{uid}/impersonate")
async def impersonate(
    uid: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Login-as: switch session to another user (admin only)."""
    from fastapi import Response

    from app.auth import create_session_token

    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not u.is_active:
        raise HTTPException(status_code=400, detail="Аккаунт деактивирован")
    response = Response(
        content=f'{{"ok":true,"username":"{u.username}"}}',
        media_type="application/json",
    )
    response.set_cookie(
        "session",
        create_session_token(u.id, back_to=admin.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


# --- Impersonation meta endpoints: NO admin dependency (work while acting as target) ---
meta_router = APIRouter(prefix="/api/admin", tags=["admin-meta"])


@meta_router.post("/exit-impersonate")
async def exit_impersonate(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Restore admin session after impersonation (works while acting as target)."""
    from fastapi import Response

    from app.auth import create_session_token, parse_session_back

    token = request.cookies.get("session", "")
    back = parse_session_back(token) if token else None
    if back is None:
        raise HTTPException(status_code=400, detail="Не режим просмотра")
    admin_user = await db.get(User, back)
    if admin_user is None or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Админ-сессия недоступна")
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(
        "session",
        create_session_token(admin_user.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@meta_router.get("/impersonation")
async def impersonation_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Who am I / am I viewing as someone."""
    from app.auth import parse_session_back, parse_session_token

    token = request.cookies.get("session", "")
    uid = parse_session_token(token) if token else None
    back = parse_session_back(token) if token else None
    if uid is None:
        return {"impersonating": False}
    me = await db.get(User, uid)
    orig = await db.get(User, back) if back else None
    return {
        "impersonating": orig is not None,
        "as": me.username if me else None,
        "back_to": orig.username if orig else None,
    }
