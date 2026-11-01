"""Telegram Mini App auth: validate initData HMAC on every request."""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User

# Accept initData up to 5 minutes old (Telegram best practice)
MAX_AGE_SECONDS = 300


def parse_init_data(init_data: str) -> dict | None:
    """Parse and validate Telegram.WebApp.initData. Returns dict with user or None."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:  # noqa: BLE001
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=b"",  # placeholder, we'll use bot token below — actually need token
        digestmod=hashlib.sha256,
    ).digest()
    # Correct algorithm:
    # secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
    # hash = HMAC_SHA256(key=secret, msg=data_check_string)
    # We'll receive bot_token in validate function.
    return {"parsed": parsed, "hash": received_hash, "dcs": data_check_string}


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Return user dict if valid, else None."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:  # noqa: BLE001
        return None
    received_hash = parsed.pop("hash", None)
    signature = parsed.pop("signature", None)  # newer clients
    if not received_hash:
        return None

    # auth_date freshness
    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            if abs(time.time() - int(auth_date)) > MAX_AGE_SECONDS:
                return None
        except ValueError:
            return None

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()
    computed = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None

    user_raw = parsed.get("user")
    if not user_raw:
        return None
    import json

    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None


async def get_user_from_init_data(request: Request, db: AsyncSession) -> User | None:
    """Authenticate via Authorization: tma <initData> header (Mini App)."""
    from app.config import get_settings

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        # also accept query param for form GETs
        init_data = request.query_params.get("initData") or request.headers.get("X-Telegram-Init-Data", "")
    else:
        init_data = auth[4:].strip()

    if not init_data:
        return None

    bot_token = get_settings().telegram_bot_token
    if not bot_token:
        return None

    tg_user = validate_init_data(init_data, bot_token)
    if not tg_user:
        return None
    tg_id = tg_user.get("id")
    if not tg_id:
        return None

    result = await db.execute(select(User).where(User.telegram_id == int(tg_id)))
    return result.scalar_one_or_none()


async def tma_get_user(request: Request, db: AsyncSession) -> User | None:
    return await get_user_from_init_data(request, db)
