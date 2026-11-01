from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Account, AccountType, Transaction, User

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: AccountType = AccountType.card
    currency: str = "RUB"
    balance: float = 0.0


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    currency: str | None = None
    # Manual balance correction (sets absolute balance, not delta)
    balance: float | None = None


def _serialize(a: Account) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "type": a.type.value,
        "currency": a.currency,
        "balance": a.balance,
        "archived": a.archived,
    }


@router.get("")
async def list_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    include_archived: bool = False,
):
    q = select(Account).where(Account.user_id == user.id)
    if not include_archived:
        q = q.where(Account.archived.is_(False))
    q = q.order_by(Account.name)
    return [_serialize(a) for a in (await db.execute(q)).scalars().all()]


@router.post("", status_code=201)
async def create_account(
    body: AccountIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acc = Account(
        user_id=user.id,
        name=body.name.strip(),
        type=body.type,
        currency=body.currency.strip().upper() or "RUB",
        balance=body.balance,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return _serialize(acc)


@router.put("/{acc_id}")
async def update_account(
    acc_id: int,
    body: AccountUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(Account, acc_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    if body.name is not None:
        acc.name = body.name.strip()
    if body.currency is not None:
        acc.currency = body.currency.strip().upper() or acc.currency
    if body.balance is not None:
        acc.balance = body.balance
    await db.commit()
    await db.refresh(acc)
    return _serialize(acc)


@router.post("/{acc_id}/archive", status_code=204)
async def archive_account(
    acc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(Account, acc_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    others = (
        await db.execute(
            select(Account.id).where(
                Account.user_id == user.id,
                Account.id != acc.id,
                Account.archived.is_(False),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if others is None:
        raise HTTPException(status_code=400, detail="Нельзя архивировать последний активный счёт")
    used = (
        await db.execute(
            select(Transaction.id)
            .where(
                (Transaction.account_id == acc.id) | (Transaction.transfer_account_id == acc.id)
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if used is not None:
        acc.archived = True
    else:
        await db.delete(acc)
    await db.commit()
