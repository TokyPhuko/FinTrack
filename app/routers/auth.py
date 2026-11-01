from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_session_token,
    get_current_user,
    get_optional_user,
    verify_password,
)
from app.config import BASE_DIR
from app.database import get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    redirect = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        "session",
        create_session_token(user.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return redirect


@router.post("/logout")
async def logout():
    redirect = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie("session")
    return redirect


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "telegram_id": user.telegram_id,
        "llm_politeness": user.llm_politeness,
    }
