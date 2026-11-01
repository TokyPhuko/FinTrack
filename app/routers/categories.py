from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Category, CategoryType, Transaction, User

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: CategoryType
    monthly_limit: float | None = Field(default=None, ge=0)
    icon: str = ""


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    monthly_limit: float | None = Field(default=None, ge=0)
    icon: str | None = None


def _serialize(c: Category) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "type": c.type.value,
        "monthly_limit": c.monthly_limit,
        "icon": c.icon,
        "archived": c.archived,
    }


@router.get("")
async def list_categories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    include_archived: bool = False,
):
    q = select(Category).where(Category.user_id == user.id)
    if not include_archived:
        q = q.where(Category.archived.is_(False))
    q = q.order_by(Category.type, Category.name)
    return [_serialize(c) for c in (await db.execute(q)).scalars().all()]


@router.post("", status_code=201)
async def create_category(
    body: CategoryIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    name = body.name.strip()
    existing = (
        await db.execute(
            select(Category).where(
                Category.user_id == user.id,
                Category.name == name,
                Category.type == body.type,
                Category.archived.is_(False),
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Категория с таким именем уже есть")
    cat = Category(
        user_id=user.id,
        name=name,
        type=body.type,
        monthly_limit=body.monthly_limit,
        icon=body.icon.strip(),
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return _serialize(cat)


@router.put("/{cat_id}")
async def update_category(
    cat_id: int,
    body: CategoryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cat = await db.get(Category, cat_id)
    if cat is None or cat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    if body.name is not None:
        cat.name = body.name.strip()
    if "monthly_limit" in body.model_fields_set:
        cat.monthly_limit = body.monthly_limit
    if body.icon is not None:
        cat.icon = body.icon.strip()
    await db.commit()
    await db.refresh(cat)
    return _serialize(cat)


@router.post("/{cat_id}/archive", status_code=204)
async def archive_category(
    cat_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cat = await db.get(Category, cat_id)
    if cat is None or cat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    used = (
        await db.execute(
            select(Transaction.id).where(Transaction.category_id == cat.id).limit(1)
        )
    ).scalar_one_or_none()
    if used is not None:
        cat.archived = True
    else:
        await db.delete(cat)
    await db.commit()


@router.post("/{cat_id}/restore", status_code=204)
async def restore_category(
    cat_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cat = await db.get(Category, cat_id)
    if cat is None or cat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    cat.archived = False
    await db.commit()
