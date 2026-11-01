from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import Account, Category, CategoryType, Transaction, TransactionType, User
from app.services.ledger import apply_balance, revert_balance, validate_transaction

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


class TransactionIn(BaseModel):
    type: TransactionType
    amount: float = Field(gt=0)
    category_id: int | None = None
    account_id: int
    transfer_account_id: int | None = None
    note: str = ""
    occurred_at: datetime | None = None


class TransactionOut(BaseModel):
    id: int
    type: TransactionType
    amount: float
    category_id: int | None
    category_name: str | None
    account_id: int
    account_name: str
    transfer_account_id: int | None
    transfer_account_name: str | None
    note: str
    occurred_at: datetime
    source: str


def _serialize(tx: Transaction) -> dict:
    return {
        "id": tx.id,
        "type": tx.type.value,
        "amount": tx.amount,
        "category_id": tx.category_id,
        "category_name": tx.category.name if tx.category else None,
        "account_id": tx.account_id,
        "account_name": tx.account.name if tx.account else "?",
        "transfer_account_id": tx.transfer_account_id,
        "transfer_account_name": tx.transfer_account.name if tx.transfer_account else None,
        "note": tx.note,
        "occurred_at": tx.occurred_at.isoformat(),
        "source": tx.source,
    }


@router.get("")
async def list_transactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    month: str | None = None,
    tx_type: TransactionType | None = None,
    account_id: int | None = None,
    limit: int = 100,
):
    q = select(Transaction).where(Transaction.user_id == user.id)
    if month:
        start = datetime.strptime(month + "-01", "%Y-%m-%d")
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        q = q.where(Transaction.occurred_at >= start, Transaction.occurred_at < end)
    if tx_type:
        q = q.where(Transaction.type == tx_type)
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    q = (
        q.options(
            selectinload(Transaction.category),
            selectinload(Transaction.account),
            selectinload(Transaction.transfer_account),
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(min(limit, 500))
    )
    rows = (await db.execute(q)).scalars().all()
    return [_serialize(t) for t in rows]


@router.post("", status_code=201)
async def create_transaction(
    body: TransactionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await validate_transaction(
        db,
        user.id,
        tx_type=body.type,
        amount=body.amount,
        account_id=body.account_id,
        transfer_account_id=body.transfer_account_id,
    )
    if body.type != TransactionType.transfer:
        if body.category_id is None:
            raise HTTPException(status_code=400, detail="Выберите категорию")
        cat = await db.get(Category, body.category_id)
        if cat is None or cat.user_id != user.id or cat.archived:
            raise HTTPException(status_code=404, detail="Категория не найдена")
        expected = CategoryType.expense if body.type == TransactionType.expense else CategoryType.income
        if cat.type != expected:
            raise HTTPException(status_code=400, detail="Тип категории не соответствует типу операции")
    elif body.category_id is not None:
        raise HTTPException(status_code=400, detail="У перевода не бывает категории")

    tx = Transaction(
        user_id=user.id,
        type=body.type,
        amount=body.amount,
        category_id=None if body.type == TransactionType.transfer else body.category_id,
        account_id=body.account_id,
        transfer_account_id=body.transfer_account_id if body.type == TransactionType.transfer else None,
        note=body.note.strip(),
        occurred_at=body.occurred_at or datetime.now(),
        source="web",
    )
    await apply_balance(db, tx)
    db.add(tx)
    await db.commit()
    tx = (
        await db.execute(
            select(Transaction)
            .where(Transaction.id == tx.id)
            .options(
                selectinload(Transaction.category),
                selectinload(Transaction.account),
                selectinload(Transaction.transfer_account),
            )
        )
    ).scalar_one()

    # LLM roast after expense (async, best-effort, never breaks the response)
    roast_text = None
    if tx.type == TransactionType.expense and tx.category:
        try:
            from app.services.llm import roast_after_expense

            roast_text = await roast_after_expense(db, user, tx.amount, tx.category.name)
        except Exception:  # noqa: BLE001
            roast_text = None

    out = _serialize(tx)
    out["roast"] = roast_text
    return out


@router.put("/{tx_id}")
async def update_transaction(
    tx_id: int,
    body: TransactionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tx = await db.get(Transaction, tx_id)
    if tx is None or tx.user_id != user.id:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    await revert_balance(db, tx)
    await validate_transaction(
        db,
        user.id,
        tx_type=body.type,
        amount=body.amount,
        account_id=body.account_id,
        transfer_account_id=body.transfer_account_id,
    )
    if body.type != TransactionType.transfer:
        if body.category_id is None:
            raise HTTPException(status_code=400, detail="Выберите категорию")
        cat = await db.get(Category, body.category_id)
        if cat is None or cat.user_id != user.id or cat.archived:
            raise HTTPException(status_code=404, detail="Категория не найдена")
    tx.type = body.type
    tx.amount = body.amount
    tx.category_id = None if body.type == TransactionType.transfer else body.category_id
    tx.account_id = body.account_id
    tx.transfer_account_id = body.transfer_account_id if body.type == TransactionType.transfer else None
    tx.note = body.note.strip()
    if body.occurred_at:
        tx.occurred_at = body.occurred_at
    await apply_balance(db, tx)
    await db.commit()
    tx = (
        await db.execute(
            select(Transaction)
            .where(Transaction.id == tx.id)
            .options(
                selectinload(Transaction.category),
                selectinload(Transaction.account),
                selectinload(Transaction.transfer_account),
            )
        )
    ).scalar_one()
    return _serialize(tx)


@router.delete("/{tx_id}", status_code=204)
async def delete_transaction(
    tx_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tx = await db.get(Transaction, tx_id)
    if tx is None or tx.user_id != user.id:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    await revert_balance(db, tx)
    await db.delete(tx)
    await db.commit()
