import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TransactionType(str, enum.Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"  # NOT counted as expense/income anywhere


class AccountType(str, enum.Enum):
    cash = "cash"
    card = "card"
    savings = "savings"
    deposit = "deposit"


class CategoryType(str, enum.Enum):
    expense = "expense"
    income = "income"


class PlannedStatus(str, enum.Enum):
    pending = "pending"
    done = "done"
    skipped = "skipped"
    overdue = "overdue"


class Recurrence(str, enum.Enum):
    none = "none"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    telegram_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    llm_politeness: Mapped[int] = mapped_column(Integer, default=1)  # 0=сарказм .. 3=полный мат
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    accounts: Mapped[list["Account"]] = relationship(back_populates="user")
    categories: Mapped[list["Category"]] = relationship(back_populates="user")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[AccountType] = mapped_column(Enum(AccountType), default=AccountType.card)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="accounts")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[CategoryType] = mapped_column(Enum(CategoryType), default=CategoryType.expense)
    monthly_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    icon: Mapped[str] = mapped_column(String(32), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="categories")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    amount: Mapped[float] = mapped_column(Float)  # always positive; sign implied by type
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    transfer_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(16), default="web")  # web | bot | import | system
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship()
    account: Mapped["Account"] = relationship(foreign_keys=[account_id])
    transfer_account: Mapped["Account | None"] = relationship(foreign_keys=[transfer_account_id])


class PlannedTransaction(Base):
    __tablename__ = "planned_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))  # expense | income (no transfer)
    amount: Mapped[float] = mapped_column(Float)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence: Mapped[Recurrence] = mapped_column(Enum(Recurrence), default=Recurrence.none)
    status: Mapped[PlannedStatus] = mapped_column(Enum(PlannedStatus), default=PlannedStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    category: Mapped["Category | None"] = relationship()
    account: Mapped["Account | None"] = relationship()


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    amount: Mapped[float] = mapped_column(Float)


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    bank: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[float] = mapped_column(Float)
    rate: Mapped[float] = mapped_column(Float, default=0.0)  # annual %
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LlmNote(Base):
    __tablename__ = "llm_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # roast | weekly | chat | deposit
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # planned | deposit_end | recurring | budget
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AccountMember(Base):
    """Shared account membership: many users see/use one account."""

    __tablename__ = "account_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # owner | member
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DataShare(Base):
    """Owner grants viewer read access to their finance data."""

    __tablename__ = "data_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CheckIn(Base):
    """Daily check-in: keeps streak alive on no-spend days."""

    __tablename__ = "check_ins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)  # local date of check-in
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StreakPair(Base):
    """Duolingo-style pair run: two users compare streaks."""

    __tablename__ = "streak_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user1_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user2_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | declined
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
