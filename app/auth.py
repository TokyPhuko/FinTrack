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


def create_session_token(user_id: int, back_to: int | None = None) -> str:
    payload: dict = {"uid": user_id}
    if back_to is not None:
        payload["back_to"] = back_to
    return serializer.dumps(payload)


def parse_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


def parse_session_back(token: str) -> int | None:
    """Return original admin uid if this session is an impersonation."""
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        v = data.get("back_to")
        return int(v) if v is not None else None
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("session")
    if token:
        uid = parse_session_token(token)
        if uid is not None:
            user = await db.get(User, uid)
            if user is not None and user.is_active:
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
            if user is not None and user.is_active:
                return user

    from app.services.tma import get_user_from_init_data

    return await get_user_from_init_data(request, db)


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Только для админа")
    return user


async def ensure_admin_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            is_admin=True,
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

    # Promote the configured admin username (seamless for existing DBs)
    admin = (
        await db.execute(select(User).where(User.username == settings.admin_username))
    ).scalar_one_or_none()
    if admin is not None and not admin.is_admin:
        admin.is_admin = True
        await db.commit()

    # Always ensure a second test user exists (for shared accounts / pairs testing)
    test = (
        await db.execute(select(User).where(User.username == "test"))
    ).scalar_one_or_none()
    if test is None:
        test = User(username="test", password_hash=hash_password("test"))
        db.add(test)
        await db.commit()
        await db.refresh(test)
        await seed_user_defaults(db, test)

    return user


async def seed_user_defaults(db: AsyncSession, user: User) -> None:
    """Fresh account: one zero-balance card + base categories."""
    from app.models import Account, AccountType, Category, CategoryType

    existing = (
        await db.execute(select(Account.id).where(Account.user_id == user.id).limit(1))
    ).scalar_one_or_none()
    if existing is None:
        db.add(Account(user_id=user.id, name="Карта", type=AccountType.card, balance=0.0))
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
        cat_exists = (
            await db.execute(
                select(Category.id).where(Category.user_id == user.id, Category.name == name)
            )
        ).scalar_one_or_none()
        if cat_exists is None:
            db.add(Category(user_id=user.id, name=name, type=ctype))
    await db.commit()


async def register_user(db: AsyncSession, username: str, password: str) -> User | str:
    """Create a new user. Returns User on success, error message string on failure."""
    username = username.strip()
    if not username or len(username) > 64:
        return "Логин: 1–64 символа"
    if len(password) < 3:
        return "Пароль: минимум 3 символа"
    if not username.replace("_", "").replace("-", "").isalnum():
        return "Логин: только буквы, цифры, _ и -"
    existing = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        return "Такой логин уже занят"
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await seed_user_defaults(db, user)
    return user
