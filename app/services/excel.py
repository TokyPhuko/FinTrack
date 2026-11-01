"""Excel export — 7 sheets. Downloads only; DB remains the source of truth."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Budget,
    Category,
    Deposit,
    PlannedStatus,
    PlannedTransaction,
    Transaction,
    TransactionType,
    User,
)

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TOTAL_FONT = Font(bold=True)
THIN = Border(
    left=Side(style="thin", color="D0D0D0"),
    right=Side(style="thin", color="D0D0D0"),
    top=Side(style="thin", color="D0D0D0"),
    bottom=Side(style="thin", color="D0D0D0"),
)
RED_FILL = PatternFill("solid", fgColor="FFC7CE")
GREEN_FILL = PatternFill("solid", fgColor="C6EFCE")
TRANSFER_FILL = PatternFill("solid", fgColor="F2F2F2")


def _month_bounds(month: str) -> tuple[datetime, datetime, int, int]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    # days in month
    import calendar

    days = calendar.monthrange(start.year, start.month)[1]
    return start, end, start.month, days


def _style_header(ws, row: int, cols: int) -> None:
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = THIN


def _autosize(ws, max_width: int = 40) -> None:
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        length = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[letter].width = min(max(length + 2, 8), max_width)


async def build_workbook(db: AsyncSession, user: User, month: str) -> bytes:
    start, end, month_num, days_in_month = _month_bounds(month)

    # --- fetch data ---
    txs = (
        await db.execute(
            select(Transaction)
            .where(
                Transaction.user_id == user.id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .order_by(Transaction.occurred_at, Transaction.id)
        )
    ).scalars().all()

    accounts = (
        await db.execute(
            select(Account)
            .where(Account.user_id == user.id, Account.archived.is_(False))
            .order_by(Account.name)
        )
    ).scalars().all()
    account_names = {a.id: a.name for a in accounts}

    categories = (
        await db.execute(
            select(Category).where(Category.user_id == user.id).order_by(Category.type, Category.name)
        )
    ).scalars().all()
    cat_names = {c.id: c.name for c in categories}

    budgets = (
        await db.execute(
            select(Budget, Category)
            .join(Category, Category.id == Budget.category_id)
            .where(Budget.user_id == user.id, Budget.month == month)
        )
    ).all()
    budget_map = {c.id: (c.name, b.amount) for b, c in budgets}

    plans = (
        await db.execute(
            select(PlannedTransaction)
            .where(
                PlannedTransaction.user_id == user.id,
                PlannedTransaction.due_date >= start.date(),
                PlannedTransaction.due_date < end.date(),
            )
            .order_by(PlannedTransaction.due_date)
        )
    ).scalars().all()

    deposits = (
        await db.execute(
            select(Deposit).where(Deposit.user_id == user.id).order_by(Deposit.end_date)
        )
    ).scalars().all()

    wb = Workbook()

    income_total = sum(t.amount for t in txs if t.type == TransactionType.income)
    expense_total = sum(t.amount for t in txs if t.type == TransactionType.expense)
    transfer_total = sum(t.amount for t in txs if t.type == TransactionType.transfer)
    # current balances
    balance_total = sum(a.balance for a in accounts)

    # ============ 1. Сводка ============
    ws = wb.active
    ws.title = "Сводка"
    ws["A1"] = f"FinTrack — сводка за {month}"
    ws["A1"].font = Font(size=14, bold=True)
    rows = [
        ("", ""),
        ("Доход", income_total),
        ("Расход", expense_total),
        ("Чистыми", income_total - expense_total),
        ("Переводы (не расходы)", transfer_total),
        ("Общий баланс счетов", balance_total),
        ("Операций всего", len(txs)),
    ]
    ws.append([])
    ws.append(["Показатель", "Значение"])
    _style_header(ws, 3, 2)
    for label, val in rows[1:]:
        ws.append([label, val])
    for r in range(4, 4 + len(rows) - 1):
        ws.cell(row=r, column=1).border = THIN
        ws.cell(row=r, column=2).border = THIN
        if isinstance(ws.cell(row=r, column=2).value, (int, float)):
            ws.cell(row=r, column=2).number_format = "#,##0.00"

    # top categories this month
    cat_spend: dict[int, float] = {}
    for t in txs:
        if t.type == TransactionType.expense and t.category_id:
            cat_spend[t.category_id] = cat_spend.get(t.category_id, 0) + t.amount
    top = sorted(cat_spend.items(), key=lambda x: -x[1])[:5]
    ws.append([])
    ws.append(["Топ-5 категорий", "Сумма"])
    _style_header(ws, ws.max_row, 2)
    for cid, s in top:
        ws.append([cat_names.get(cid, "?"), s])
        ws.cell(row=ws.max_row, column=2).number_format = "#,##0.00"

    ws.append([])
    ws.append(["Счет", "Баланс"])
    _style_header(ws, ws.max_row, 2)
    for a in accounts:
        ws.append([a.name, a.balance])
        ws.cell(row=ws.max_row, column=2).number_format = "#,##0.00"
    _autosize(ws)

    # ============ 2. Транзакции ============
    ws = wb.create_sheet("Транзакции")
    headers = ["Дата", "Тип", "Категория", "Счёт", "Счёт-получатель", "Сумма", "Заметка", "Месяц", "Источник"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    type_labels = {"expense": "Расход", "income": "Доход", "transfer": "Перевод"}
    for t in txs:
        ws.append(
            [
                t.occurred_at.strftime("%Y-%m-%d"),
                type_labels.get(t.type.value, t.type.value),
                cat_names.get(t.category_id, "") if t.category_id else "",
                account_names.get(t.account_id, ""),
                account_names.get(t.transfer_account_id, "") if t.transfer_account_id else "",
                -t.amount if t.type == TransactionType.expense else (
                    t.amount if t.type == TransactionType.income else t.amount
                ),
                t.note,
                month,
                t.source,
            ]
        )
        row = ws.max_row
        for c in range(1, len(headers) + 1):
            ws.cell(row=row, column=c).border = THIN
        if t.type == TransactionType.transfer:
            for c in range(1, len(headers) + 1):
                ws.cell(row=row, column=c).fill = TRANSFER_FILL
        ws.cell(row=row, column=6).number_format = "#,##0.00"
    # totals
    ws.append([])
    ws.append(["", "", "", "", "", "", "Итого доход:", income_total])
    ws.append(["", "", "", "", "", "", "Итого расход:", expense_total])
    ws.append(["", "", "", "", "", "", "Переводы:", transfer_total])
    for r in range(ws.max_row - 2, ws.max_row + 1):
        ws.cell(row=r, column=7).font = TOTAL_FONT
        ws.cell(row=r, column=8).font = TOTAL_FONT
        ws.cell(row=r, column=8).number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    _autosize(ws)

    # ============ 3. Бюджет ============
    ws = wb.create_sheet("Бюджет")
    ws.append(["Категория", "Бюджет", "Факт", "Отклонение", "%"])
    _style_header(ws, 1, 5)
    if budget_map:
        for cid, (name, limit) in sorted(budget_map.items(), key=lambda x: x[1][0]):
            used = cat_spend.get(cid, 0.0)
            var = used - limit  # positive = overspend
            pct = (used / limit * 100) if limit else 0
            ws.append([name, limit, used, var, round(pct, 1)])
            row = ws.max_row
            for c in range(1, 6):
                ws.cell(row=row, column=c).border = THIN
            for c in (2, 3, 4):
                ws.cell(row=row, column=c).number_format = "#,##0.00"
            ws.cell(row=row, column=5).number_format = "0.0"
    else:
        ws.append(["Бюджеты на месяц не заданы — задай в вебе (Бюджеты)", "", "", "", ""])
    # red for overspend
    if budget_map:
        ws.conditional_formatting.add(
            f"D2:D{ws.max_row}",
            CellIsRule(operator="greaterThan", formula=["0"], fill=RED_FILL),
        )
        ws.conditional_formatting.add(
            f"D2:D{ws.max_row}",
            CellIsRule(operator="lessThanOrEqual", formula=["0"], fill=GREEN_FILL),
        )
    _autosize(ws)

    # ============ 4. Сводная по категориям ============
    ws = wb.create_sheet("Сводная по категориям")
    ws.append(["Категория", "Расход", "% от расходов"])
    _style_header(ws, 1, 3)
    total_exp = expense_total or 1
    for cid, s in sorted(cat_spend.items(), key=lambda x: -x[1]):
        ws.append([cat_names.get(cid, "?"), s, round(s / total_exp * 100, 1)])
        row = ws.max_row
        for c in range(1, 4):
            ws.cell(row=row, column=c).border = THIN
        ws.cell(row=row, column=2).number_format = "#,##0.00"
        ws.cell(row=row, column=3).number_format = "0.0"
    if cat_spend:
        chart = BarChart()
        chart.title = "Расходы по категориям"
        chart.y_axis.title = "₽"
        data = Reference(ws, min_col=2, min_row=1, max_row=ws.max_row)
        cats = Reference(ws, min_col=1, min_row=2, max_row=ws.max_row)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 10
        chart.width = 16
        ws.add_chart(chart, "E2")
    _autosize(ws)

    # ============ 5. План vs Факт ============
    ws = wb.create_sheet("План vs Факт")
    ws.append(["Дата", "План", "Тип", "Сумма плана", "Статус", "Факт (совпадение)"])
    _style_header(ws, 1, 6)
    status_labels = {
        "pending": "Ожидает",
        "done": "Выполнен",
        "skipped": "Пропущен",
        "overdue": "Просрочен",
    }
    # match plans to transactions by note/title + amount
    for p in plans:
        today = date.today()
        st = p.status.value
        if p.status == PlannedStatus.pending and p.due_date < today:
            st = "overdue"
        match = ""
        if p.status == PlannedStatus.done:
            for t in txs:
                if (
                    abs(t.amount - p.amount) < 0.01
                    and t.note == p.title
                    and t.type == p.type
                ):
                    match = t.occurred_at.strftime("%Y-%m-%d")
                    break
        ws.append(
            [
                p.due_date.isoformat(),
                p.title,
                "Расход" if p.type == TransactionType.expense else "Доход",
                p.amount,
                status_labels.get(st, st),
                match,
            ]
        )
        row = ws.max_row
        for c in range(1, 7):
            ws.cell(row=row, column=c).border = THIN
        ws.cell(row=row, column=4).number_format = "#,##0.00"
    if not plans:
        ws.append(["Планов на месяц нет", "", "", "", "", ""])
    _autosize(ws)

    # ============ 6. Счета и переводы ============
    ws = wb.create_sheet("Счета и переводы")
    ws.append(["Счёт", "Тип", "Валюта", "Баланс"])
    _style_header(ws, 1, 4)
    for a in accounts:
        ws.append([a.name, a.type.value, a.currency, a.balance])
        row = ws.max_row
        for c in range(1, 5):
            ws.cell(row=row, column=c).border = THIN
        ws.cell(row=row, column=4).number_format = "#,##0.00"
    ws.append([])
    ws.append(["Итого", "", "", balance_total])
    ws.cell(row=ws.max_row, column=1).font = TOTAL_FONT
    ws.cell(row=ws.max_row, column=4).font = TOTAL_FONT
    ws.cell(row=ws.max_row, column=4).number_format = "#,##0.00"

    transfers = [t for t in txs if t.type == TransactionType.transfer]
    ws.append([])
    ws.append(["Переводы (не входят в расходы)", "", "", ""])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.append(["Дата", "Счёт", "Счёт-получатель", "Сумма"])
    _style_header(ws, ws.max_row, 4)
    for t in transfers:
        ws.append(
            [
                t.occurred_at.strftime("%Y-%m-%d"),
                account_names.get(t.account_id, ""),
                account_names.get(t.transfer_account_id, ""),
                t.amount,
            ]
        )
        row = ws.max_row
        for c in range(1, 5):
            ws.cell(row=row, column=c).border = THIN
            ws.cell(row=row, column=c).fill = TRANSFER_FILL
        ws.cell(row=row, column=4).number_format = "#,##0.00"
    if not transfers:
        ws.append(["Переводов за период нет", "", "", ""])
    _autosize(ws)

    # ============ 7. Динамика ============
    ws = wb.create_sheet("Динамика")
    ws.append(["День", "Расходы", "Доходы", "Накопленный расход"])
    _style_header(ws, 1, 4)
    daily_exp: dict[int, float] = {}
    daily_inc: dict[int, float] = {}
    for t in txs:
        d = t.occurred_at.day
        if t.type == TransactionType.expense:
            daily_exp[d] = daily_exp.get(d, 0) + t.amount
        elif t.type == TransactionType.income:
            daily_inc[d] = daily_inc.get(d, 0) + t.amount
    cum = 0.0
    for day in range(1, days_in_month + 1):
        e = daily_exp.get(day, 0.0)
        i = daily_inc.get(day, 0.0)
        cum += e
        ws.append([f"{month}-{day:02d}", e, i, cum])
        row = ws.max_row
        for c in range(1, 5):
            ws.cell(row=row, column=c).border = THIN
        for c in (2, 3, 4):
            ws.cell(row=row, column=c).number_format = "#,##0.00"
    if days_in_month:
        chart = LineChart()
        chart.title = "Накопленный расход по дням"
        chart.y_axis.title = "₽"
        data = Reference(ws, min_col=4, min_row=1, max_row=ws.max_row)
        chart.add_data(data, titles_from_data=True)
        chart.height = 10
        chart.width = 16
        ws.add_chart(chart, "F2")

        chart2 = BarChart()
        chart2.title = "Расходы по дням"
        data2 = Reference(ws, min_col=2, min_row=1, max_row=ws.max_row)
        cats_ref = Reference(ws, min_col=1, min_row=2, max_row=ws.max_row)
        chart2.add_data(data2, titles_from_data=True)
        chart2.set_categories(cats_ref)
        chart2.height = 10
        chart2.width = 16
        ws.add_chart(chart2, "F22")
    _autosize(ws)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
