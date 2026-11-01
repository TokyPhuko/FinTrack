from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Category, LlmNote, Transaction, TransactionType, User
from app.services import llm as llm_service

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class RoastIn(BaseModel):
    amount: float = Field(gt=0)
    category_id: int


@router.get("/status")
async def status(user: User = Depends(get_current_user)):
    return llm_service.get_llm_status().as_dict()


@router.post("/chat")
async def chat(
    body: ChatIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    answer = await llm_service.chat_analyze(db, user, body.message)
    await llm_service.save_chat_note(db, user.id, f"Q: {body.message}\nA: {answer}")
    return {"answer": answer, "status": llm_service.get_llm_status().as_dict()}


@router.post("/roast")
async def roast(
    body: RoastIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cat = await db.get(Category, body.category_id)
    if cat is None or cat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    text = await llm_service.roast_after_expense(db, user, body.amount, cat.name)
    return {"text": text, "status": llm_service.get_llm_status().as_dict()}


@router.post("/weekly")
async def weekly(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    text = await llm_service.weekly_report(db, user)
    return {"text": text, "status": llm_service.get_llm_status().as_dict()}


class DepositSearchIn(BaseModel):
    amount: float = Field(gt=0)
    term_days: int = Field(default=90, gt=0, le=3650)


@router.post("/deposits/search")
async def deposit_search(
    body: DepositSearchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await llm_service.search_deposit_offers(body.amount, body.term_days)


class DepositDiscussIn(BaseModel):
    amount: float = Field(gt=0)
    term_days: int = Field(default=90, gt=0, le=3650)


@router.post("/deposits/discuss")
async def deposit_discuss(
    body: DepositDiscussIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Conversational analysis of actual bank offers with a recommendation."""
    text = await llm_service.discuss_deposits(db, user, body.amount, body.term_days)
    await llm_service.save_chat_note(db, user.id, f"Вклады {body.amount:g} ₽ / {body.term_days} дн.\n{text}")
    return {"text": text, "status": llm_service.get_llm_status().as_dict()}


@router.get("/notes")
async def notes(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    kind: str | None = None,
    limit: int = 20,
):
    q = select(LlmNote).where(LlmNote.user_id == user.id)
    if kind:
        q = q.where(LlmNote.kind == kind)
    q = q.order_by(LlmNote.id.desc()).limit(min(limit, 100))
    rows = (await db.execute(q)).scalars().all()
    return [
        {"id": n.id, "kind": n.kind, "content": n.content, "created_at": n.created_at.isoformat()}
        for n in rows
    ]


@router.post("/politeness")
async def set_politeness(
    level: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if level < 0 or level > 3:
        raise HTTPException(status_code=400, detail="Уровень 0..3")
    user.llm_politeness = level
    await db.commit()
    return {"llm_politeness": user.llm_politeness}
