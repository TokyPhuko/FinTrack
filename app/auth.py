from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
serializer = URLSafeTimedSerializer(settings.app_secret, salt="fintrack-session")
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"uid": user_id})


def parse_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("session")
    if token:
        uid = parse_session_token(token)
        if uid is not None:
            user = await db.get(User, uid)
            if user is not None:
                return user

    # Telegram Mini App initData auth
    from app.services.tma import get_user_from_init_data

    user = await get_user_from_init_data(request, db)
    if user is not None:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def get_optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    token = request.cookies.get("session")
    if token:
        uid = parse_session_token(token)
        if uid is not None:
            user = await db.get(User, uid)
            if user is not None:
                return user

    from app.services.tma import get_user_from_init_data

    return await get_user_from_init_data(request, db)


async def ensure_admin_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Seed a default account so the app is usable out of the box
        from app.models import Account, AccountType, Category, CategoryType

        db.add(Account(user_id=user.id, name="Основная карта", type=AccountType.card, balance=0.0))
        defaults = [
            ("Продукты", CategoryType.expense),
            ("Кафе", CategoryType.expense),
            ("Транспорт", CategoryType.expense),
            ("Жильё", CategoryType.expense),
            ("Связь и интернет", CategoryType.expense),
            ("Подписки", CategoryType.expense),
            ("Здоровье", CategoryType.expense),
            ("Одежда", CategoryType.expense),
            ("Развлечения", CategoryType.expense),
            ("Прочее", CategoryType.expense),
            ("Зарплата", CategoryType.income),
            ("Подработка", CategoryType.income),
            ("Проценты по вкладам", CategoryType.income),
        ]
        for name, ctype in defaults:
            db.add(Category(user_id=user.id, name=name, type=ctype))
        await db.commit()
        await db.refresh(user)

    # Always ensure a second test user exists (for shared accounts / pairs testing)
    test = (
        await db.execute(select(User).where(User.username == "test"))
    ).scalar_one_or_none()
    if test is None:
        from app.models import Account, AccountType, Category, CategoryType

        test = User(username="test", password_hash=hash_password("test"))
        db.add(test)
        await db.commit()
        await db.refresh(test)
        db.add(Account(user_id=test.id, name="Карта test", type=AccountType.card, balance=0.0))
        for name, ctype in [
            ("Продукты", CategoryType.expense),
            ("Кафе", CategoryType.expense),
            ("Транспорт", CategoryType.expense),
            ("Прочее", CategoryType.expense),
            ("Зарплата", CategoryType.income),
        ]:
            db.add(Category(user_id=test.id, name=name, type=ctype))
        await db.commit()

    return user
