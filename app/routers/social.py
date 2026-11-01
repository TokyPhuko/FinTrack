"""Social: shared accounts, data shares, streaks, pair runs, check-ins."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Account,
    AccountMember,
    DataShare,
    User,
)
from app.services.streaks import (
    do_checkin,
    get_active_pair,
    get_pending_invites,
    pair_summary,
    streak_info,
    visible_account_ids,
)

router = APIRouter(prefix="/api/social", tags=["social"])


class ShareAccountIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ShareDataIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class PairInviteIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


# ---------- shared accounts ----------


@router.get("/accounts/visible")
async def visible_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Own accounts + shared-with-me accounts."""
    ids = await visible_account_ids(db, user.id)
    if not ids:
        return []
    rows = (
        await db.execute(
            select(Account, AccountMember.role, User.username)
            .join(AccountMember, AccountMember.account_id == Account.id, isouter=True)
            .join(User, User.id == AccountMember.user_id, isouter=True)
            .where(Account.id.in_(ids), Account.archived.is_(False))
            .order_by(Account.name)
        )
    ).all()
    # dedupe by account id: prefer owner row
    seen: dict[int, dict] = {}
    for acc, role, member_uname in rows:
        entry = seen.get(acc.id)
        if entry is None:
            seen[acc.id] = {
                "id": acc.id,
                "name": acc.name,
                "type": acc.type.value,
                "currency": acc.currency,
                "balance": acc.balance,
                "owner_id": acc.user_id,
                "is_mine": acc.user_id == user.id,
                "members": [],
            }
        if role and member_uname:
            seen[acc.id]["members"].append({"username": member_uname, "role": role})
    return list(seen.values())


@router.post("/accounts/{acc_id}/share", status_code=201)
async def share_account(
    acc_id: int,
    body: ShareAccountIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(Account, acc_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Счёт не найден (только владелец может шарить)")
    target = (
        await db.execute(select(User).where(User.username == body.username.strip()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"Пользователь «{body.username}» не найден")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя шарить самому себе")
    existing = (
        await db.execute(
            select(AccountMember.id).where(
                AccountMember.account_id == acc_id, AccountMember.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail="Уже участник")
    db.add(AccountMember(account_id=acc_id, user_id=target.id, role="member"))
    await db.commit()
    return {"ok": True, "account": acc.name, "shared_with": target.username}


@router.delete("/accounts/{acc_id}/share/{username}", status_code=204)
async def unshare_account(
    acc_id: int,
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(Account, acc_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    target = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(AccountMember).where(
            AccountMember.account_id == acc_id, AccountMember.user_id == target.id
        )
    )
    await db.commit()


# ---------- data shares (read-only view of another user) ----------


@router.get("/shares")
async def list_shares(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Both directions: whom I share with, who shares with me."""
    mine = (
        await db.execute(
            select(DataShare, User.username)
            .join(User, User.id == DataShare.viewer_id)
            .where(DataShare.owner_id == user.id)
        )
    ).all()
    theirs = (
        await db.execute(
            select(DataShare, User.username)
            .join(User, User.id == DataShare.owner_id)
            .where(DataShare.viewer_id == user.id)
        )
    ).all()
    return {
        "i_share_with": [{"username": u, "created_at": s.created_at.isoformat()} for s, u in mine],
        "they_share_with_me": [{"username": u, "created_at": s.created_at.isoformat()} for s, u in theirs],
    }


@router.post("/shares", status_code=201)
async def create_share(
    body: ShareDataIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = (
        await db.execute(select(User).where(User.username == body.username.strip()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"Пользователь «{body.username}» не найден")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя шарить себе")
    existing = (
        await db.execute(
            select(DataShare.id).where(
                DataShare.owner_id == user.id, DataShare.viewer_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail="Уже шаришь свои данные этому пользователю")
    db.add(DataShare(owner_id=user.id, viewer_id=target.id))
    await db.commit()
    return {"ok": True, "shared_with": target.username}


@router.delete("/shares/{username}", status_code=204)
async def delete_share(
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(DataShare).where(
            DataShare.owner_id == user.id, DataShare.viewer_id == target.id
        )
    )
    await db.commit()


# ---------- streaks / check-in / pairs ----------


@router.get("/streak")
async def get_streak(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    info = await streak_info(db, user)
    pair = await pair_summary(db, user)
    invites = await get_pending_invites(db, user.id)
    inviter_names = []
    for inv in invites:
        u = await db.get(User, inv.user1_id)
        if u:
            inviter_names.append(u.username)
    return {**info, "pair": pair, "pending_invites": inviter_names}


@router.post("/checkin")
async def checkin(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    is_new = await do_checkin(db, user.id)
    info = await streak_info(db, user)
    return {"new": is_new, **info}


@router.post("/pair/invite", status_code=201)
async def invite_pair(
    body: PairInviteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = (
        await db.execute(select(User).where(User.username == body.username.strip()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"Пользователь «{body.username}» не найден")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя пригласить себя")
    existing = await get_active_pair(db, user.id)
    if existing is not None:
        raise HTTPException(status_code=400, detail="У тебя уже есть активная пара")
    # check pending either direction
    from app.models import StreakPair

    pend = (
        await db.execute(
            select(StreakPair.id).where(
                StreakPair.status == "pending",
                or_(
                    (StreakPair.user1_id == user.id) & (StreakPair.user2_id == target.id),
                    (StreakPair.user1_id == target.id) & (StreakPair.user2_id == user.id),
                ),
            )
        )
    ).scalar_one_or_none()
    if pend is not None:
        raise HTTPException(status_code=400, detail="Приглашение уже отправлено")
    pair = StreakPair(user1_id=user.id, user2_id=target.id, status="pending")
    db.add(pair)
    await db.commit()
    return {"ok": True, "invited": target.username}


@router.post("/pair/respond")
async def respond_pair(
    accept: bool,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invites = await get_pending_invites(db, user.id)
    if not invites:
        raise HTTPException(status_code=404, detail="Нет входящих приглашений")
    pair = invites[0]
    from datetime import datetime

    if accept:
        pair.status = "active"
    else:
        pair.status = "declined"
    pair.responded_at = datetime.now()
    await db.commit()
    return {"ok": True, "accepted": accept}


@router.delete("/pair", status_code=204)
async def leave_pair(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pair = await get_active_pair(db, user.id)
    if pair is None:
        raise HTTPException(status_code=404, detail="Активной пары нет")
    pair.status = "ended"
    from datetime import datetime

    pair.responded_at = datetime.now()
    await db.commit()
