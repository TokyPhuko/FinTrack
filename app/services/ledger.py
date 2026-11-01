"""Balance apply/revert. Transfers move money between accounts and are never expenses."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Transaction, TransactionType


async def _get_account(db: AsyncSession, user_id: int, account_id: int) -> Account:
    account = await db.get(Account, account_id)
    if account is None or account.user_id != user_id or account.archived:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    return account


async def apply_balance(db: AsyncSession, tx: Transaction) -> None:
    account = await _get_account(db, tx.user_id, tx.account_id)
    if tx.type == TransactionType.expense:
        account.balance -= tx.amount
    elif tx.type == TransactionType.income:
        account.balance += tx.amount
    elif tx.type == TransactionType.transfer:
        if tx.transfer_account_id is None:
            raise HTTPException(status_code=400, detail="Для перевода укажите счёт получателя")
        target = await _get_account(db, tx.user_id, tx.transfer_account_id)
        if target.id == account.id:
            raise HTTPException(status_code=400, detail="Счёт отправки и получения должны отличаться")
        account.balance -= tx.amount
        target.balance += tx.amount


async def revert_balance(db: AsyncSession, tx: Transaction) -> None:
    account = await _get_account(db, tx.user_id, tx.account_id)
    if tx.type == TransactionType.expense:
        account.balance += tx.amount
    elif tx.type == TransactionType.income:
        account.balance -= tx.amount
    elif tx.type == TransactionType.transfer:
        if tx.transfer_account_id is None:
            return
        target = await db.get(Account, tx.transfer_account_id)
        if target is None:
            return
        account.balance += tx.amount
        target.balance -= tx.amount


async def validate_transaction(
    db: AsyncSession,
    user_id: int,
    *,
    tx_type: TransactionType,
    amount: float,
    account_id: int,
    transfer_account_id: int | None,
) -> None:
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    await _get_account(db, user_id, account_id)
    if tx_type == TransactionType.transfer:
        if transfer_account_id is None:
            raise HTTPException(status_code=400, detail="Для перевода укажите счёт получателя")
        await _get_account(db, user_id, transfer_account_id)
        if transfer_account_id == account_id:
            raise HTTPException(status_code=400, detail="Счёт отправки и получения должны отличаться")
