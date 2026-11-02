from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.services.excel import build_workbook

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/excel")
async def export_excel(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    month: str = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
):
    month = month or datetime.now().strftime("%Y-%m")
    try:
        data = await build_workbook(db, user, month)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сборки отчёта: {e}") from e
    filename = f"fintrack_{month}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/excel/telegram")
async def export_excel_to_telegram(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    month: str = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
):
    """Generate Excel and send it to the bound Telegram chat."""
    if not user.telegram_id:
        raise HTTPException(
            status_code=400,
            detail="Telegram не привязан — напиши /start боту или /bind логин пароль",
        )
    month = month or datetime.now().strftime("%Y-%m")
    try:
        data = await build_workbook(db, user, month)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сборки отчёта: {e}") from e
    filename = f"fintrack_{month}.xlsx"
    from app.bot import send_document_to_user

    try:
        await send_document_to_user(user.telegram_id, data, filename)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось отправить в Telegram: {e}") from e
    return {"ok": True, "telegram_id": user.telegram_id, "filename": filename}
