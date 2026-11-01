from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Deposit, User

router = APIRouter(prefix="/api/deposits", tags=["deposits"])


class DepositIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    bank: str = ""
    amount: float = Field(gt=0)
    rate: float = Field(ge=0, le=100)  # annual %
    start_date: date
    end_date: date
    auto_renew: bool = False


class DepositUpdate(BaseModel):
    title: str | None = None
    bank: str | None = None
    amount: float | None = Field(default=None, gt=0)
    rate: float | None = Field(default=None, ge=0, le=100)
    end_date: date | None = None
    auto_renew: bool | None = None
    notified: bool | None = None


def _serialize(d: Deposit) -> dict:
    from datetime import datetime

    today = date.today()
    days_left = (d.end_date - today).days
    # simple interest projection
    days_total = max((d.end_date - d.start_date).days, 1)
    income = d.amount * (d.rate / 100) * (days_total / 365)
    return {
        "id": d.id,
        "title": d.title,
        "bank": d.bank,
        "amount": d.amount,
        "rate": d.rate,
        "start_date": d.start_date.isoformat(),
        "end_date": d.end_date.isoformat(),
        "auto_renew": d.auto_renew,
        "notified": d.notified,
        "days_left": days_left,
        "projected_income": round(income, 2),
        "maturity_amount": round(d.amount + income, 2),
    }


@router.get("")
async def list_deposits(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Deposit)
            .where(Deposit.user_id == user.id)
            .order_by(Deposit.end_date)
        )
    ).scalars().all()
    return [_serialize(d) for d in rows]


@router.post("", status_code=201)
async def create_deposit(
    body: DepositIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.end_date <= body.start_date:
        raise HTTPException(status_code=400, detail="Дата окончания должна быть позже начала")
    d = Deposit(
        user_id=user.id,
        title=body.title.strip(),
        bank=body.bank.strip(),
        amount=body.amount,
        rate=body.rate,
        start_date=body.start_date,
        end_date=body.end_date,
        auto_renew=body.auto_renew,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _serialize(d)


@router.put("/{dep_id}")
async def update_deposit(
    dep_id: int,
    body: DepositUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await db.get(Deposit, dep_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="Вклад не найден")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(d, k, v)
    if d.title:
        d.title = d.title.strip()
    await db.commit()
    await db.refresh(d)
    return _serialize(d)


@router.delete("/{dep_id}", status_code=204)
async def delete_deposit(
    dep_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await db.get(Deposit, dep_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="Вклад не найден")
    await db.delete(d)
    await db.commit()
