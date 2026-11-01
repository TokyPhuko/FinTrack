from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Budget, Category, User
from app.services.warnings import get_warnings

router = APIRouter(prefix="/api", tags=["budgets-warnings"])


class BudgetIn(BaseModel):
    category_id: int
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: float = Field(ge=0)


@router.get("/warnings")
async def warnings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_warnings(db, user)


@router.get("/budgets")
async def list_budgets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    month: str | None = None,
):
    q = (
        select(Budget, Category)
        .join(Category, Category.id == Budget.category_id)
        .where(Budget.user_id == user.id)
    )
    if month:
        q = q.where(Budget.month == month)
    rows = (await db.execute(q.order_by(Budget.month, Category.name))).all()
    return [
        {
            "id": b.id,
            "category_id": b.category_id,
            "category_name": c.name,
            "month": b.month,
            "amount": b.amount,
        }
        for b, c in rows
    ]


@router.post("/budgets", status_code=201)
async def upsert_budget(
    body: BudgetIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cat = await db.get(Category, body.category_id)
    if cat is None or cat.user_id != user.id or cat.archived:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    existing = (
        await db.execute(
            select(Budget).where(
                Budget.user_id == user.id,
                Budget.category_id == body.category_id,
                Budget.month == body.month,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.amount = body.amount
        budget = existing
        created = False
    else:
        budget = Budget(
            user_id=user.id,
            category_id=body.category_id,
            month=body.month,
            amount=body.amount,
        )
        db.add(budget)
        created = True
    await db.commit()
    await db.refresh(budget)
    return {
        "id": budget.id,
        "category_id": budget.category_id,
        "category_name": cat.name,
        "month": budget.month,
        "amount": budget.amount,
        "created": created,
    }


@router.delete("/budgets/{budget_id}", status_code=204)
async def delete_budget(
    budget_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    b = await db.get(Budget, budget_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status_code=404, detail="Бюджет не найден")
    await db.delete(b)
    await db.commit()


@router.get("/budgets/progress")
async def budget_progress(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    month: str | None = None,
):
    """Budget vs actual for a month (defaults to current)."""
    from datetime import datetime

    from sqlalchemy import func

    from app.models import Transaction, TransactionType

    month = month or datetime.now().strftime("%Y-%m")
    month_start = datetime.strptime(month + "-01", "%Y-%m-%d")
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    budget_rows = (
        await db.execute(
            select(Budget, Category)
            .join(Category, Category.id == Budget.category_id)
            .where(Budget.user_id == user.id, Budget.month == month)
        )
    ).all()

    # also include categories with monthly_limit
    cat_rows = (
        await db.execute(
            select(Category).where(
                Category.user_id == user.id,
                Category.archived.is_(False),
                Category.type == "expense",
            )
        )
    ).scalars().all()

    limits: dict[int, tuple[str, float | None]] = {}
    for b, c in budget_rows:
        limits[c.id] = (c.name, b.amount)
    for c in cat_rows:
        if c.id not in limits:
            limits[c.id] = (c.name, c.monthly_limit)

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
                Transaction.occurred_at < month_end,
                Transaction.category_id.is_not(None),
            )
            .group_by(Transaction.category_id)
        )
    ).all()
    spent = dict(spent_rows)

    items = []
    total_limit = 0.0
    total_spent = 0.0
    for cat_id, (name, limit) in sorted(limits.items(), key=lambda x: x[1][0]):
        used = spent.get(cat_id, 0.0)
        if limit is None and used == 0:
            continue
        total_limit += limit or 0
        total_spent += used
        items.append(
            {
                "category_id": cat_id,
                "category_name": name,
                "limit": limit,
                "spent": round(used, 2),
                "remaining": round((limit or 0) - used, 2) if limit is not None else None,
                "pct": round(used / limit * 100, 1) if limit else None,
            }
        )
    items.sort(key=lambda x: -(x["pct"] or 0))
    return {
        "month": month,
        "items": items,
        "total_limit": round(total_limit, 2),
        "total_spent": round(total_spent, 2),
        "total_remaining": round(total_limit - total_spent, 2),
    }
