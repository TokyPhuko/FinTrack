from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Account, Category, Transaction, TransactionType, User

router = APIRouter(prefix="/api")


@router.get("/health")
async def health():
    return {"ok": True, "app": "fintrack"}


@router.get("/summary")
async def summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Month-to-date summary. Transfers excluded from income/expense."""
    from datetime import datetime

    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def sum_by_type(t: TransactionType) -> float:
        q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
            Transaction.user_id == user.id,
            Transaction.type == t,
            Transaction.occurred_at >= month_start,
        )
        return q

    income = (await db.execute(sum_by_type(TransactionType.income))).scalar() or 0.0
    expense = (await db.execute(sum_by_type(TransactionType.expense))).scalar() or 0.0
    # transfer count only (not in income/expense)
    transfers = (
        await db.execute(
            select(func.count(Transaction.id)).where(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.transfer,
                Transaction.occurred_at >= month_start,
            )
        )
    ).scalar() or 0

    accounts = (
        await db.execute(
            select(Account).where(Account.user_id == user.id, Account.archived.is_(False))
        )
    ).scalars().all()
    total_balance = sum(a.balance for a in accounts)

    return {
        "month": now.strftime("%Y-%m"),
        "income": round(income, 2),
        "expense": round(expense, 2),
        "net": round(income - expense, 2),
        "transfers_count": transfers,
        "total_balance": round(total_balance, 2),
        "accounts": [
            {"id": a.id, "name": a.name, "type": a.type.value, "balance": a.balance}
            for a in accounts
        ],
    }


@router.get("/categories")
async def list_categories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Category)
            .where(Category.user_id == user.id, Category.archived.is_(False))
            .order_by(Category.type, Category.name)
        )
    ).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type.value,
            "monthly_limit": c.monthly_limit,
            "icon": c.icon,
        }
        for c in rows
    ]


@router.get("/accounts")
async def list_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Account)
            .where(Account.user_id == user.id, Account.archived.is_(False))
            .order_by(Account.name)
        )
    ).scalars().all()
    return [
        {"id": a.id, "name": a.name, "type": a.type.value, "currency": a.currency, "balance": a.balance}
        for a in rows
    ]
