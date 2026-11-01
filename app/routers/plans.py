from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Account,
    Category,
    CategoryType,
    PlannedStatus,
    PlannedTransaction,
    Recurrence,
    Transaction,
    TransactionType,
    User,
)
from app.services.ledger import apply_balance, validate_transaction

router = APIRouter(prefix="/api/plans", tags=["plans"])


class PlanIn(BaseModel):
    type: TransactionType  # expense | income only
    amount: float = Field(gt=0)
    title: str = Field(min_length=1, max_length=256)
    category_id: int | None = None
    account_id: int | None = None
    due_date: date
    is_recurring: bool = False
    recurrence: Recurrence = Recurrence.none


class PlanUpdate(BaseModel):
    type: TransactionType | None = None
    amount: float | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    category_id: int | None = None
    account_id: int | None = None
    due_date: date | None = None
    is_recurring: bool | None = None
    recurrence: Recurrence | None = None


def _next_due(d: date, rec: Recurrence) -> date:
    if rec == Recurrence.daily:
        return d + timedelta(days=1)
    if rec == Recurrence.weekly:
        return d + timedelta(weeks=1)
    if rec == Recurrence.monthly:
        month = d.month + 1
        year = d.year
        if month > 12:
            month, year = 1, year + 1
        try:
            return d.replace(year=year, month=month)
        except ValueError:
            return date(year, month, 28)
    if rec == Recurrence.yearly:
        try:
            return d.replace(year=d.year + 1)
        except ValueError:
            return date(d.year + 1, 2, 28)
    return d


def _serialize(p: PlannedTransaction) -> dict:
    today = date.today()
    status = p.status.value
    if p.status == PlannedStatus.pending and p.due_date < today:
        status = "overdue"
    return {
        "id": p.id,
        "type": p.type.value,
        "amount": p.amount,
        "title": p.title,
        "category_id": p.category_id,
        "category_name": p.category.name if p.category else None,
        "account_id": p.account_id,
        "account_name": p.account.name if p.account else None,
        "due_date": p.due_date.isoformat(),
        "is_recurring": p.is_recurring,
        "recurrence": p.recurrence.value,
        "status": status,
    }


@router.get("")
async def list_plans(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,  # pending | done | skipped | overdue | all
):
    q = select(PlannedTransaction).where(PlannedTransaction.user_id == user.id)
    if status in ("pending", "done", "skipped"):
        q = q.where(PlannedTransaction.status == PlannedStatus(status))
    q = q.options(
        selectinload(PlannedTransaction.category),
        selectinload(PlannedTransaction.account),
    ).order_by(PlannedTransaction.due_date, PlannedTransaction.id)
    rows = (await db.execute(q)).scalars().all()
    out = [_serialize(p) for p in rows]
    if status == "overdue":
        out = [p for p in out if p["status"] == "overdue"]
    elif status == "pending":
        out = [p for p in out if p["status"] in ("pending", "overdue")]
    return out


@router.post("", status_code=201)
async def create_plan(
    body: PlanIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.type not in (TransactionType.expense, TransactionType.income):
        raise HTTPException(status_code=400, detail="План может быть только расходом или доходом")
    if body.category_id is not None:
        cat = await db.get(Category, body.category_id)
        if cat is None or cat.user_id != user.id or cat.archived:
            raise HTTPException(status_code=404, detail="Категория не найдена")
    if body.account_id is not None:
        acc = await db.get(Account, body.account_id)
        if acc is None or acc.user_id != user.id or acc.archived:
            raise HTTPException(status_code=404, detail="Счёт не найден")
    if body.is_recurring and body.recurrence == Recurrence.none:
        raise HTTPException(status_code=400, detail="Выберите периодичность")
    if not body.is_recurring:
        body.recurrence = Recurrence.none

    plan = PlannedTransaction(
        user_id=user.id,
        type=body.type,
        amount=body.amount,
        category_id=body.category_id,
        account_id=body.account_id,
        title=body.title.strip(),
        due_date=body.due_date,
        is_recurring=body.is_recurring,
        recurrence=body.recurrence,
        status=PlannedStatus.pending,
    )
    db.add(plan)
    await db.commit()
    plan = (
        await db.execute(
            select(PlannedTransaction)
            .where(PlannedTransaction.id == plan.id)
            .options(
                selectinload(PlannedTransaction.category),
                selectinload(PlannedTransaction.account),
            )
        )
    ).scalar_one()
    return _serialize(plan)


@router.put("/{plan_id}")
async def update_plan(
    plan_id: int,
    body: PlanUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(PlannedTransaction, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="План не найден")
    if plan.status != PlannedStatus.pending:
        raise HTTPException(status_code=400, detail="Можно редактировать только ожидающие планы")
    data = body.model_dump(exclude_unset=True)
    if "type" in data and data["type"] not in (TransactionType.expense, TransactionType.income):
        raise HTTPException(status_code=400, detail="План может быть только расходом или доходом")
    for key, value in data.items():
        setattr(plan, key, value)
    if not plan.is_recurring:
        plan.recurrence = Recurrence.none
    await db.commit()
    plan = (
        await db.execute(
            select(PlannedTransaction)
            .where(PlannedTransaction.id == plan.id)
            .options(
                selectinload(PlannedTransaction.category),
                selectinload(PlannedTransaction.account),
            )
        )
    ).scalar_one()
    return _serialize(plan)


@router.post("/{plan_id}/complete")
async def complete_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(PlannedTransaction, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="План не найден")
    if plan.status != PlannedStatus.pending:
        raise HTTPException(status_code=400, detail="План уже обработан")
    if plan.account_id is None:
        raise HTTPException(status_code=400, detail="Укажите счёт, чтобы отметить выполненным")
    if plan.category_id is None and plan.type == TransactionType.expense:
        raise HTTPException(status_code=400, detail="Укажите категорию, чтобы отметить выполненным")

    await validate_transaction(
        db,
        user.id,
        tx_type=plan.type,
        amount=plan.amount,
        account_id=plan.account_id,
        transfer_account_id=None,
    )
    tx = Transaction(
        user_id=user.id,
        type=plan.type,
        amount=plan.amount,
        category_id=plan.category_id,
        account_id=plan.account_id,
        note=plan.title,
        occurred_at=datetime.now(),
        source="plan",
    )
    await apply_balance(db, tx)
    db.add(tx)

    if plan.is_recurring:
        plan.due_date = _next_due(plan.due_date, plan.recurrence)
        # keep pending for the next occurrence
    else:
        plan.status = PlannedStatus.done
    await db.commit()
    plan = (
        await db.execute(
            select(PlannedTransaction)
            .where(PlannedTransaction.id == plan.id)
            .options(
                selectinload(PlannedTransaction.category),
                selectinload(PlannedTransaction.account),
            )
        )
    ).scalar_one()
    return {"plan": _serialize(plan), "transaction_id": tx.id}


@router.post("/{plan_id}/skip", status_code=204)
async def skip_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(PlannedTransaction, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="План не найден")
    if plan.status != PlannedStatus.pending:
        raise HTTPException(status_code=400, detail="План уже обработан")
    plan.status = PlannedStatus.skipped
    await db.commit()


@router.post("/{plan_id}/restore", status_code=204)
async def restore_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(PlannedTransaction, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="План не найден")
    if plan.status == PlannedStatus.pending:
        return
    plan.status = PlannedStatus.pending
    await db.commit()


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(PlannedTransaction, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="План не найден")
    await db.delete(plan)
    await db.commit()
