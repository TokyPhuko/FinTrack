import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ensure_admin_user, get_optional_user
from app.bot import start_bot_task
from app.config import BASE_DIR, get_settings
from app.database import async_session_factory, get_db, init_db
from app.models import User
from app.routers import (
    accounts,
    admin,
    api,
    auth,
    budgets,
    categories,
    deposits,
    export,
    llm,
    plans,
    social,
    transactions,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with async_session_factory() as db:
        await ensure_admin_user(db)
    bot_task = start_bot_task()
    yield
    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="FinTrack", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app.include_router(auth.router)
app.include_router(api.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(accounts.router)
app.include_router(plans.router)
app.include_router(budgets.router)
app.include_router(deposits.router)
app.include_router(export.router)
app.include_router(llm.router)
app.include_router(social.router)
app.include_router(admin.router)
app.include_router(admin.meta_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    if not user:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"user": user})


def _require_page_user(user: User | None):
    if user is None:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/login", status_code=303)
    return None


@app.get("/transactions", response_class=HTMLResponse)
async def page_transactions(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "transactions.html", {"user": user})


@app.get("/plans", response_class=HTMLResponse)
async def page_plans(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "plans.html", {"user": user})


@app.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "accounts.html", {"user": user})


@app.get("/categories", response_class=HTMLResponse)
async def page_categories(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "categories.html", {"user": user})


@app.get("/budgets", response_class=HTMLResponse)
async def page_budgets(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "budgets.html", {"user": user})


@app.get("/deposits", response_class=HTMLResponse)
async def page_deposits(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "deposits.html", {"user": user})


@app.get("/reports", response_class=HTMLResponse)
async def page_reports(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "reports.html", {"user": user})


@app.get("/chat", response_class=HTMLResponse)
async def page_chat(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "chat.html", {"user": user})


@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _require_page_user(user)
    if redirect:
        return redirect
    if not user.is_admin:
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "admin.html", {"user": user})
