"""
CaseFight — Telegram Mini App
Полный backend: FastAPI + PostgreSQL + Telegram Stars + Crash + Arena + Upgrade + WebSocket
""" 

import asyncio
import hashlib
import hmac
import json
import os
import random
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Tuple, List, Set

from fastapi import FastAPI, HTTPException, Request, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Float, Boolean,
    DateTime, ForeignKey, DECIMAL, select, func, and_, or_, delete
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()


# ======================== КОНФИГУРАЦИЯ ========================

class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "CaseFightBot")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/casefight",
    )
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "7092015279"))
    STARS_RATE: float = float(os.getenv("STARS_RATE", "1.0"))
    MIN_DEPOSIT_STARS: int = int(os.getenv("MIN_DEPOSIT_STARS", "50"))
    MIN_WITHDRAW: float = float(os.getenv("MIN_WITHDRAW", "100.0"))
    DEPOSIT_FEE_PERCENT: float = float(os.getenv("DEPOSIT_FEE_PERCENT", "5.0"))
    WITHDRAW_FEE_PERCENT: float = float(os.getenv("WITHDRAW_FEE_PERCENT", "5.0"))
    CRASH_HOUSE_EDGE: float = float(os.getenv("CRASH_HOUSE_EDGE", "0.05"))
    ARENA_PLATFORM_FEE: float = float(os.getenv("ARENA_PLATFORM_FEE", "5.0"))
    ARENA_MAX_PLAYERS: int = int(os.getenv("ARENA_MAX_PLAYERS", "5"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", os.urandom(32).hex())
    APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    CORS_ORIGINS: List[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:8000,https://localhost:8000"
    ).split(",")

    def __init__(self):
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 16:
            raise RuntimeError("SECRET_KEY must be at least 16 characters!")


settings = Settings()


# ======================== БАЗА ДАННЫХ ========================

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif not db_url.startswith("postgresql+asyncpg://"):
    db_url = f"postgresql+asyncpg://{db_url}"

engine = create_async_engine(db_url, echo=False, pool_size=20, max_overflow=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ======================== МОДЕЛИ ========================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255))
    first_name = Column(String(255))
    avatar_url = Column(Text)
    balance = Column(DECIMAL(15, 2), default=Decimal("0.00"))
    total_deposited = Column(DECIMAL(15, 2), default=Decimal("0.00"))
    total_withdrawn = Column(DECIMAL(15, 2), default=Decimal("0.00"))
    is_blocked = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    can_odd_bets = Column(Boolean, default=False)
    case_cooldown_removed = Column(Boolean, default=False)
    case_cooldown_until = Column(DateTime(timezone=True))
    registered_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login = Column(DateTime(timezone=True), server_default=func.now())


class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True)
    name = Column(String(255))
    description = Column(Text)
    price = Column(DECIMAL(15, 2))
    image_url = Column(Text)
    type = Column(String(50), default="stars")
    is_active = Column(Boolean, default=True)
    cooldown_seconds = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaseItem(Base):
    __tablename__ = "case_items"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    name = Column(String(255))
    image_url = Column(Text)
    value = Column(DECIMAL(15, 2))
    drop_chance = Column(DECIMAL(6, 4))
    rarity = Column(String(50), default="common")


class UserInventory(Base):
    __tablename__ = "user_inventory"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_item_id = Column(Integer)
    case_id = Column(Integer)
    case_name = Column(String(255))
    item_name = Column(String(255))
    item_image_url = Column(Text)
    item_value = Column(DECIMAL(15, 2))
    item_rarity = Column(String(50))
    obtained_at = Column(DateTime(timezone=True), server_default=func.now())
    is_upgraded = Column(Boolean, default=False)
    upgraded_from_id = Column(Integer)


class CaseOpenHistory(Base):
    __tablename__ = "case_open_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_id = Column(Integer)
    case_name = Column(String(255))
    item_id = Column(Integer)
    item_name = Column(String(255))
    item_value = Column(DECIMAL(15, 2))
    item_rarity = Column(String(50))
    opened_at = Column(DateTime(timezone=True), server_default=func.now())


class DepositTransaction(Base):
    __tablename__ = "deposit_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    telegram_payment_id = Column(String(255), unique=True)
    amount_stars = Column(Integer)
    amount_received = Column(DECIMAL(15, 2))
    fee = Column(DECIMAL(15, 2))
    status = Column(String(50), default="completed")
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WithdrawRequest(Base):
    __tablename__ = "withdraw_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    amount = Column(DECIMAL(15, 2))
    fee = Column(DECIMAL(15, 2))
    amount_after_fee = Column(DECIMAL(15, 2))
    status = Column(String(50), default="pending", index=True)
    admin_id = Column(Integer)
    admin_comment = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True))


class CrashGame(Base):
    __tablename__ = "crash_games"
    id = Column(Integer, primary_key=True)
    crash_point = Column(DECIMAL(10, 4))
    seed_hash = Column(String(255))
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CrashBet(Base):
    __tablename__ = "crash_bets"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("crash_games.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount = Column(DECIMAL(15, 2))
    auto_cashout = Column(DECIMAL(10, 4))
    cashout_multiplier = Column(DECIMAL(10, 4))
    profit = Column(DECIMAL(15, 2))
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ArenaGame(Base):
    __tablename__ = "arena_games"
    id = Column(Integer, primary_key=True)
    creator_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String(50), default="waiting", index=True)
    total_pot = Column(DECIMAL(15, 2), default=Decimal("0.00"))
    platform_fee = Column(DECIMAL(15, 2), default=Decimal("0.00"))
    winner_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))


class ArenaPlayer(Base):
    __tablename__ = "arena_players"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("arena_games.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer)
    bet_amount = Column(DECIMAL(15, 2))
    win_chance = Column(DECIMAL(6, 4))
    result = Column(String(50))
    joined_at = Column(DateTime(timezone=True), server_default=func.now())


class UpgradeHistory(Base):
    __tablename__ = "upgrade_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    item_from_id = Column(Integer)
    item_to_name = Column(String(255))
    item_to_value = Column(DECIMAL(15, 2))
    success = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    username = Column(String(255))
    message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ActionLog(Base):
    __tablename__ = "action_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    action_type = Column(String(100))
    description = Column(Text)
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RequestIdempotency(Base):
    __tablename__ = "request_idempotency"
    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(255), unique=True)
    user_id = Column(Integer)
    expires_at = Column(DateTime(timezone=True))


# ======================== PYDANTIC СХЕМЫ ========================

class UserOut(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    avatar_url: Optional[str]
    balance: Decimal
    total_deposited: Decimal
    total_withdrawn: Decimal
    is_blocked: bool
    is_admin: bool
    can_odd_bets: bool
    case_cooldown_removed: bool
    case_cooldown_until: Optional[datetime]
    registered_at: Optional[datetime]
    last_login: Optional[datetime]
    model_config = {"from_attributes": True}


class ProfileOut(BaseModel):
    user: UserOut
    inventory_count: int
    total_case_opens: int


class ItemOut(BaseModel):
    id: int
    case_id: int
    name: str
    image_url: Optional[str]
    value: Decimal
    drop_chance: Decimal
    rarity: str
    model_config = {"from_attributes": True}


class CaseOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: Decimal
    image_url: Optional[str]
    type: str
    is_active: bool
    cooldown_seconds: int
    items: List[ItemOut] = []
    model_config = {"from_attributes": True}


class OpenCaseReq(BaseModel):
    case_id: int
    idempotency_key: str


class OpenCaseResp(BaseModel):
    success: bool
    item: ItemOut
    balance_after: Decimal


class InventoryOut(BaseModel):
    id: int
    case_id: Optional[int]
    case_name: Optional[str]
    item_name: Optional[str]
    item_image_url: Optional[str]
    item_value: Optional[Decimal]
    item_rarity: Optional[str]
    obtained_at: Optional[datetime]
    is_upgraded: bool
    model_config = {"from_attributes": True}


class SellReq(BaseModel):
    item_id: int


class SellResp(BaseModel):
    success: bool
    sold_for: Decimal
    balance_after: Decimal


class StarsDepositReq(BaseModel):
    amount: int = Field(ge=50, le=10000)


class StarsDepositResp(BaseModel):
    payment_link: str
    amount_stars: int
    amount_received: Decimal
    fee: Decimal


class WithdrawReq(BaseModel):
    amount: Decimal = Field(ge=100, le=1000000)


class WithdrawOut(BaseModel):
    id: int
    amount: Decimal
    fee: Decimal
    amount_after_fee: Decimal
    status: str
    created_at: Optional[datetime]
    processed_at: Optional[datetime]
    model_config = {"from_attributes": True}


class CrashBetReq(BaseModel):
    amount: Decimal = Field(gt=0, le=100000)
    auto_cashout: Optional[Decimal] = Field(default=None, ge=1.01, le=1000000)


class CrashCashoutReq(BaseModel):
    game_id: int


class ArenaJoinReq(BaseModel):
    bet_amount: Decimal = Field(gt=0, le=100000)


class UpgradeReq(BaseModel):
    item_id: int
    target_item_id: int


class UpgradeResp(BaseModel):
    success: bool
    message: str
    new_item: Optional[InventoryOut] = None


class AdminBalanceReq(BaseModel):
    user_id: int
    amount: Decimal
    operation: str


class AdminBlockReq(BaseModel):
    user_id: int
    block: bool


class AdminGiveItemReq(BaseModel):
    user_id: int
    item_name: str
    item_value: Decimal
    item_image_url: Optional[str] = None
    item_rarity: str = "common"


class AdminWithdrawReq(BaseModel):
    request_id: int
    action: str
    comment: Optional[str] = None


# ======================== БЕЗОПАСНОСТЬ ========================

def validate_telegram_init_data(init_data: str) -> Optional[Dict]:
    if not settings.BOT_TOKEN:
        return None
    parsed = {}
    for item in init_data.split("&"):
        if "=" in item:
            k, v = item.split("=", 1)
            parsed[k] = urllib.parse.unquote(v)
    if "hash" not in parsed:
        return None
    received = parsed.pop("hash")
    check_arr = sorted([f"{k}={v}" for k, v in parsed.items()])
    check_str = "\n".join(check_arr)
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    if calc_hash != received:
        return None
    auth_date = int(parsed.get("auth_date", 0))
    if int(time.time()) - auth_date > 86400:
        return None
    return parsed


def extract_user_from_init(parsed: Dict) -> Dict:
    try:
        u = json.loads(parsed.get("user", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return {
        "telegram_id": u.get("id"),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "avatar_url": u.get("photo_url"),
    }


def verify_telegram_payment(payment_data: Dict) -> bool:
    """
    Проверка платежа Telegram Stars.
    В production: вебхук от Telegram с полем successful_payment.
    Здесь — упрощённая проверка для dev + реальная для prod.
    """
    if settings.DEBUG and payment_data.get("telegram_payment_id", "").startswith("demo_"):
        return True

    # Реальная проверка: подпись от Telegram API
    required_fields = ["telegram_payment_id", "amount_stars", "signature", "timestamp"]
    if not all(f in payment_data for f in required_fields):
        return False

    ts = int(payment_data["timestamp"])
    if abs(int(time.time()) - ts) > 300:  # 5 минут на оплату
        return False

    check_str = (
        f"{payment_data['telegram_payment_id']}:"
        f"{payment_data['amount_stars']}:{ts}"
    )
    secret = hmac.new(
        settings.SECRET_KEY.encode(),
        settings.BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    expected_sig = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, payment_data["signature"])


class RateLimiter:
    def __init__(self):
        self._reqs: Dict[str, list] = {}
        self._limits = {
            "case_open": (5, 60),
            "crash_bet": (10, 60),
            "arena_join": (5, 60),
            "upgrade": (10, 60),
            "withdraw": (3, 3600),
            "chat": (30, 60),
            "deposit": (10, 3600),
            "default": (60, 60),
        }
        self._last_cleanup = time.time()

    def check(self, uid: int, action: str) -> bool:
        if time.time() - self._last_cleanup > 300:
            self._cleanup()
            self._last_cleanup = time.time()

        key = f"{uid}:{action}"
        mx, w = self._limits.get(action, self._limits["default"])
        now = time.time()
        self._reqs[key] = [t for t in self._reqs.get(key, []) if now - t < w]
        if len(self._reqs[key]) >= mx:
            return True
        self._reqs[key].append(now)
        return False

    def _cleanup(self):
        now = time.time()
        empty_keys = []
        for key, times in self._reqs.items():
            parts = key.split(":", 1)
            action = parts[1] if len(parts) > 1 else "default"
            w = self._limits.get(action, self._limits["default"])[1]
            self._reqs[key] = [t for t in times if now - t < w]
            if not self._reqs[key]:
                empty_keys.append(key)
        for key in empty_keys:
            del self._reqs[key]


rate_limiter = RateLimiter()


# ======================== АВТОРИЗАЦИЯ ========================

async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    if settings.DEBUG:
        dev_token = request.headers.get("X-Dev-Token")
        dev_id = request.headers.get("X-Telegram-User-Id")
        if dev_token == settings.SECRET_KEY and dev_id:
            r = await db.execute(
                select(User).where(User.telegram_id == int(dev_id))
            )
            u = r.scalar_one_or_none()
            if u and not u.is_blocked:
                return u
            if not u:
                u = User(
                    telegram_id=int(dev_id),
                    username=f"dev_{dev_id}",
                    first_name="DevUser",
                )
                db.add(u)
                await db.flush()
                await db.refresh(u)
                return u
            raise HTTPException(404, "User not found")

    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(401, "Missing Telegram init data")

    parsed = validate_telegram_init_data(init_data)
    if not parsed:
        raise HTTPException(401, "Invalid Telegram init data")

    ud = extract_user_from_init(parsed)
    tid = ud.get("telegram_id")
    if not tid:
        raise HTTPException(401, "Invalid user data")

    r = await db.execute(
        select(User).where(User.telegram_id == tid).with_for_update()
    )
    user = r.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=tid,
            username=ud.get("username"),
            first_name=ud.get("first_name"),
            avatar_url=ud.get("avatar_url"),
            is_admin=(tid == settings.ADMIN_TELEGRAM_ID),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
    else:
        if ud.get("username"):
            user.username = ud["username"]
        if ud.get("first_name"):
            user.first_name = ud["first_name"]
        if tid == settings.ADMIN_TELEGRAM_ID and not user.is_admin:
            user.is_admin = True

    if user.is_blocked:
        raise HTTPException(403, "User is blocked")

    return user


async def get_ws_user(websocket: WebSocket, db: AsyncSession) -> Optional[User]:
    init_data = websocket.headers.get("X-Telegram-Init-Data")
    if not init_data:
        await websocket.close(code=4001, reason="Missing auth")
        return None
    parsed = validate_telegram_init_data(init_data)
    if not parsed:
        await websocket.close(code=4001, reason="Invalid auth")
        return None
    ud = extract_user_from_init(parsed)
    tid = ud.get("telegram_id")
    if not tid:
        await websocket.close(code=4001, reason="Invalid user")
        return None
    r = await db.execute(select(User).where(User.telegram_id == tid))
    user = r.scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=tid,
            username=ud.get("username"),
            first_name=ud.get("first_name"),
        )
        db.add(user)
        await db.flush()
    return user


async def get_admin(u: User = Depends(get_current_user)) -> User:
    if not u.is_admin:
        raise HTTPException(403, "Admin only")
    return u


# ======================== ПРИЛОЖЕНИЕ ========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_db()
    await recover_crash_bets()
    cleanup_task = asyncio.create_task(cleanup_idempotency())
    crash_task = asyncio.create_task(crash_loop())
    arena_cleanup_task = asyncio.create_task(cleanup_empty_arenas())
    yield
    cleanup_task.cancel()
    crash_task.cancel()
    arena_cleanup_task.cancel()


app = FastAPI(title="CaseFight", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================== ФОНОВЫЕ ЗАДАЧИ ========================

async def cleanup_idempotency():
    while True:
        await asyncio.sleep(3600)
        async with async_session() as db:
            await db.execute(
                delete(RequestIdempotency).where(
                    RequestIdempotency.expires_at < datetime.now(timezone.utc)
                )
            )
            await db.commit()


async def cleanup_empty_arenas():
    while True:
        await asyncio.sleep(300)
        async with async_session() as db:
            await db.execute(
                delete(ArenaGame).where(
                    and_(
                        ArenaGame.status == "waiting",
                        ArenaGame.created_at < datetime.now(timezone.utc) - timedelta(minutes=10),
                    )
                )
            )
            await db.commit()


async def recover_crash_bets():
    async with async_session() as db:
        stuck = await db.execute(
            select(CrashBet).where(CrashBet.status == "active")
        )
        for bet in stuck.scalars().all():
            bet.status = "cancelled"
            bet.profit = Decimal("0")
            user_r = await db.execute(
                select(User).where(User.id == bet.user_id).with_for_update()
            )
            u = user_r.scalar_one_or_none()
            if u:
                u.balance += bet.amount
        await db.commit()


# ======================== SEED БД ========================

async def seed_db():
    async with async_session() as db:
        r = await db.execute(select(Case).limit(1))
        if r.scalar_one_or_none():
            return

        cases_data = [
            ("🌟 Starter Stars", "Начальный кейс", 10, "stars", 0, [
                ("Деревянный Меч", 5, 0.25, "common"), ("Кожаный Щит", 6, 0.20, "common"),
                ("Железный Кинжал", 8, 0.18, "common"), ("Кольцо Силы", 10, 0.15, "uncommon"),
                ("Амулет Защиты", 12, 0.10, "uncommon"), ("Стальной Шлем", 15, 0.07, "rare"),
                ("Золотой Браслет", 20, 0.04, "epic"), ("Меч Новичка", 30, 0.01, "legendary"),
            ]),
            ("🥉 Bronze Stars", "Бронзовый кейс", 50, "stars", 5, [
                ("Бронзовый Меч", 25, 0.22, "common"), ("Бронзовый Щит", 28, 0.20, "common"),
                ("Кольцо Ловкости", 35, 0.18, "uncommon"), ("Плащ Теней", 45, 0.15, "uncommon"),
                ("Серебряный Амулет", 60, 0.12, "rare"), ("Топор Гномов", 75, 0.08, "rare"),
                ("Корона Воина", 100, 0.04, "epic"), ("Бронзовый Дракон", 150, 0.01, "legendary"),
            ]),
            ("🥈 Silver Stars", "Серебряный кейс", 200, "stars", 10, [
                ("Серебряный Меч", 100, 0.20, "common"), ("Серебряный Щит", 110, 0.18, "common"),
                ("Кольцо Магии", 140, 0.16, "uncommon"), ("Эльфийский Лук", 180, 0.15, "uncommon"),
                ("Рунический Посох", 240, 0.12, "rare"), ("Мифриловая Кольчуга", 300, 0.10, "rare"),
                ("Сапфировая Тиара", 400, 0.07, "epic"), ("Серебряный Феникс", 600, 0.02, "legendary"),
            ]),
            ("🥇 Gold Stars", "Золотой кейс", 500, "stars", 15, [
                ("Золотой Меч", 250, 0.18, "common"), ("Золотой Щит", 280, 0.17, "common"),
                ("Кольцо Власти", 350, 0.16, "uncommon"), ("Посох Архимага", 450, 0.14, "uncommon"),
                ("Доспехи Паладина", 600, 0.13, "rare"), ("Клинок Титана", 750, 0.10, "rare"),
                ("Корона Короля", 1000, 0.08, "epic"), ("Золотой Дракон", 1500, 0.04, "legendary"),
            ]),
            ("🎁 NFT Starter", "NFT стартовый", 100, "nft", 5, [
                ("Delicious Coffee", 50, 0.22, "common"), ("Lucky Cat", 55, 0.20, "common"),
                ("Magic Potion", 70, 0.18, "uncommon"), ("Crystal Ball", 85, 0.15, "uncommon"),
                ("Golden Key", 110, 0.12, "rare"), ("Diamond Ring", 140, 0.08, "rare"),
                ("Phoenix Feather", 200, 0.04, "epic"), ("Dragon Egg", 300, 0.01, "legendary"),
            ]),
            ("💎 NFT Rare", "NFT редкий", 300, "nft", 8, [
                ("Star Fragment", 150, 0.20, "common"), ("Moon Crystal", 170, 0.18, "common"),
                ("Thunder Bolt", 210, 0.16, "uncommon"), ("Ice Crown", 260, 0.15, "uncommon"),
                ("Shadow Amulet", 330, 0.12, "rare"), ("Ruby Heart", 400, 0.10, "rare"),
                ("Emerald Crown", 550, 0.07, "epic"), ("Celestial Sword", 800, 0.02, "legendary"),
            ]),
            ("🔥 NFT Epic", "NFT эпический", 800, "nft", 12, [
                ("Flame Sword", 400, 0.18, "common"), ("Aqua Trident", 450, 0.17, "common"),
                ("Storm Hammer", 550, 0.16, "uncommon"), ("Earth Shield", 650, 0.15, "uncommon"),
                ("Void Staff", 800, 0.13, "rare"), ("Light Bow", 950, 0.10, "rare"),
                ("Chaos Blade", 1300, 0.08, "epic"), ("Eternal Crown", 2000, 0.03, "legendary"),
            ]),
            ("👑 NFT Legendary", "NFT легендарный", 2000, "nft", 20, [
                ("Cosmic Ring", 1000, 0.17, "common"), ("Time Pendant", 1150, 0.16, "common"),
                ("Soul Gem", 1400, 0.15, "uncommon"), ("Astral Cape", 1700, 0.14, "uncommon"),
                ("Void Armor", 2100, 0.13, "rare"), ("Infinity Gauntlet", 2600, 0.11, "rare"),
                ("Excalibur", 3500, 0.09, "epic"), ("Omnipotent Orb", 5000, 0.05, "legendary"),
            ]),
        ]

        for name, desc, price, ctype, cd, items in cases_data:
            total_ch = sum(ch for _, _, ch, _ in items)
            if abs(total_ch - 1.0) > 0.001:
                items = [(n, v, round(ch / total_ch, 4), r) for n, v, ch, r in items]

            ev = sum(v * ch for _, v, ch, _ in items)
            rtp = (ev / price) * 100

            if rtp > 98:
                print(f"⚠️ RTP too high for {name}: {rtp:.1f}% — adjusting")
                adj = 95.0 / rtp
                items = [(n, v, round(ch * adj, 4), r) for n, v, ch, r in items]
                total_ch = sum(ch for _, _, ch, _ in items)
                items = [(n, v, round(ch / total_ch, 4), r) for n, v, ch, r in items]

            c = Case(
                name=name, description=desc, price=Decimal(str(price)),
                type=ctype, cooldown_seconds=cd,
                image_url=f"https://api.dicebear.com/7.x/shapes/svg?seed={hashlib.md5(name.encode()).hexdigest()[:10]}",
            )
            db.add(c)
            await db.flush()

            for item_name, val, chance, rarity in items:
                db.add(CaseItem(
                    case_id=c.id, name=item_name, value=Decimal(str(val)),
                    drop_chance=Decimal(str(chance)), rarity=rarity,
                    image_url=f"https://api.dicebear.com/7.x/icons/svg?seed={hashlib.md5(item_name.encode()).hexdigest()[:10]}",
                ))

        await db.commit()
        print(f"✅ DB seeded with {len(cases_data)} cases")


# ======================== CRASH ИГРА ========================

current_crash_game: Optional[CrashGame] = None
current_multiplier = Decimal("1.00")
crash_lock = asyncio.Lock()


async def crash_loop():
    global current_crash_game, current_multiplier

    while True:
        async with crash_lock:
            async with async_session() as db:
                seed = secrets.token_hex(32)
                seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
                rng = random.Random(seed_int)
                rand_val = rng.random()

                crash_pt = Decimal("1.00") if rand_val < 0.01 else Decimal(
                    str(round(max(
                        (0.99 / (1.0 - rand_val)) * (1.0 - settings.CRASH_HOUSE_EDGE),
                        1.01,
                    ), 2))
                )

                game = CrashGame(
                    crash_point=crash_pt,
                    seed_hash=hashlib.sha256(seed.encode()).hexdigest(),
                    status="active",
                )
                db.add(game)
                await db.commit()

                current_crash_game = game
                current_multiplier = Decimal("1.00")

                start_time = time.monotonic()
                round_duration = min(15.0, max(3.0, float(crash_pt) * 0.3))

                while current_multiplier < crash_pt:
                    elapsed = time.monotonic() - start_time
                    progress = min(elapsed / round_duration, 1.0)
                    target_mult = Decimal("1.00") + (
                        crash_pt - Decimal("1.00")
                    ) * Decimal(str(progress))
                    current_multiplier = target_mult.quantize(Decimal("0.01"))

                    await asyncio.sleep(0.1)

                    bets = (await db.execute(
                        select(CrashBet).where(and_(
                            CrashBet.game_id == game.id,
                            CrashBet.status == "active",
                            CrashBet.auto_cashout.isnot(None),
                            CrashBet.auto_cashout <= current_multiplier,
                        ))
                    )).scalars().all()

                    for bet in bets:
                        bet.cashout_multiplier = current_multiplier
                        profit = (bet.amount * current_multiplier) - bet.amount
                        bet.profit = profit
                        bet.status = "cashed_out"
                        user_r = await db.execute(
                            select(User).where(User.id == bet.user_id).with_for_update()
                        )
                        uu = user_r.scalar_one_or_none()
                        if uu:
                            uu.balance += bet.amount + profit
                    if bets:
                        await db.commit()

                remaining = (await db.execute(
                    select(CrashBet).where(and_(
                        CrashBet.game_id == game.id,
                        CrashBet.status == "active",
                    ))
                )).scalars().all()
                for bet in remaining:
                    bet.status = "lost"
                    bet.profit = -bet.amount
                game.status = "finished"
                await db.commit()

                current_crash_game = None
                current_multiplier = Decimal("1.00")

        await asyncio.sleep(5)


# ======================== API РОУТЕРЫ ========================

@app.get("/health")
async def health():
    return {"status": "ok", "app": "CaseFight", "version": "1.0.0"}


# Auth
@app.get("/auth/me", response_model=UserOut)
async def me(u: User = Depends(get_current_user)):
    return u


@app.get("/auth/profile", response_model=ProfileOut)
async def profile(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(
        select(func.count(UserInventory.id)).where(UserInventory.user_id == u.id)
    )).scalar() or 0
    ops = (await db.execute(
        select(func.count(CaseOpenHistory.id)).where(CaseOpenHistory.user_id == u.id)
    )).scalar() or 0
    return ProfileOut(user=UserOut.model_validate(u), inventory_count=inv, total_case_opens=ops)


@app.get("/auth/history/cases")
async def case_history(
    limit: int = 20, offset: int = 0,
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(CaseOpenHistory)
        .where(CaseOpenHistory.user_id == u.id)
        .order_by(CaseOpenHistory.opened_at.desc())
        .offset(offset).limit(limit)
    )
    return [
        {
            "id": h.id, "case_name": h.case_name, "item_name": h.item_name,
            "item_value": str(h.item_value), "item_rarity": h.item_rarity,
            "opened_at": h.opened_at.isoformat() if h.opened_at else None,
        }
        for h in r.scalars().all()
    ]


@app.get("/auth/history/deposits")
async def deposit_history(
    limit: int = 20, offset: int = 0,
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(DepositTransaction)
        .where(DepositTransaction.user_id == u.id)
        .order_by(DepositTransaction.created_at.desc())
        .offset(offset).limit(limit)
    )
    return [
        {
            "id": d.id, "amount_stars": d.amount_stars,
            "amount_received": str(d.amount_received), "fee": str(d.fee),
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in r.scalars().all()
    ]


@app.get("/auth/history/withdrawals")
async def withdraw_history(
    limit: int = 20, offset: int = 0,
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(WithdrawRequest)
        .where(WithdrawRequest.user_id == u.id)
        .order_by(WithdrawRequest.created_at.desc())
        .offset(offset).limit(limit)
    )
    return [
        {
            "id": w.id, "amount": str(w.amount), "fee": str(w.fee),
            "amount_after_fee": str(w.amount_after_fee), "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in r.scalars().all()
    ]


# User
@app.get("/user/balance")
async def balance(u: User = Depends(get_current_user)):
    return {
        "balance": str(u.balance),
        "total_deposited": str(u.total_deposited),
        "total_withdrawn": str(u.total_withdrawn),
    }


# Cases
@app.get("/cases/", response_model=List[CaseOut])
async def get_cases(db: AsyncSession = Depends(get_db)):
    cases = (await db.execute(
        select(Case).where(Case.is_active == True).order_by(Case.price)
    )).scalars().all()
    if not cases:
        return []

    case_ids = [c.id for c in cases]
    all_items = (await db.execute(
        select(CaseItem).where(CaseItem.case_id.in_(case_ids))
    )).scalars().all()

    items_by_case: Dict[int, list] = {c.id: [] for c in cases}
    for item in all_items:
        items_by_case[item.case_id].append(ItemOut.model_validate(item))

    return [
        CaseOut(
            id=c.id, name=c.name, description=c.description,
            price=c.price, image_url=c.image_url, type=c.type,
            is_active=c.is_active, cooldown_seconds=c.cooldown_seconds,
            items=items_by_case.get(c.id, []),
        )
        for c in cases
    ]


@app.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id: int, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(
        select(Case).where(Case.id == case_id, Case.is_active == True)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Not found")

    items = (await db.execute(
        select(CaseItem).where(CaseItem.case_id == case_id)
    )).scalars().all()

    return CaseOut(
        id=c.id, name=c.name, description=c.description,
        price=c.price, image_url=c.image_url, type=c.type,
        is_active=c.is_active, cooldown_seconds=c.cooldown_seconds,
        items=[ItemOut.model_validate(i) for i in items],
    )


@app.post("/cases/open", response_model=OpenCaseResp)
async def open_case(
    req: OpenCaseReq, db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    if rate_limiter.check(u.id, "case_open"):
        raise HTTPException(429, "Rate limit")

    ex = (await db.execute(
        select(RequestIdempotency).where(
            RequestIdempotency.idempotency_key == req.idempotency_key
        )
    )).scalar_one_or_none()
    if ex:
        raise HTTPException(409, "Already processed")

    db.add(RequestIdempotency(
        idempotency_key=req.idempotency_key, user_id=u.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    await db.flush()

    c = (await db.execute(
        select(Case).where(Case.id == req.case_id, Case.is_active == True)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Case not found")

    if c.cooldown_seconds > 0 and not u.case_cooldown_removed:
        if u.case_cooldown_until:
            cu = (
                u.case_cooldown_until.replace(tzinfo=timezone.utc)
                if u.case_cooldown_until.tzinfo is None
                else u.case_cooldown_until
            )
            if datetime.now(timezone.utc) < cu:
                remaining = (cu - datetime.now(timezone.utc)).seconds
                raise HTTPException(429, f"Cooldown active. Wait {remaining}s")

    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()

    if u_refresh.balance < c.price:
        raise HTTPException(400, "Insufficient balance")

    u_refresh.balance -= c.price

    items = (await db.execute(
        select(CaseItem).where(CaseItem.case_id == c.id)
    )).scalars().all()

    total = sum(float(i.drop_chance) for i in items)
    rand = random.uniform(0, total)
    cum = 0.0
    selected = items[-1]
    for i in items:
        cum += float(i.drop_chance)
        if rand <= cum:
            selected = i
            break

    db.add(UserInventory(
        user_id=u.id, case_item_id=selected.id, case_id=c.id,
        case_name=c.name, item_name=selected.name,
        item_image_url=selected.image_url, item_value=selected.value,
        item_rarity=selected.rarity,
    ))
    db.add(CaseOpenHistory(
        user_id=u.id, case_id=c.id, case_name=c.name,
        item_id=selected.id, item_name=selected.name,
        item_value=selected.value, item_rarity=selected.rarity,
    ))

    if c.cooldown_seconds > 0 and not u_refresh.case_cooldown_removed:
        u_refresh.case_cooldown_until = (
            datetime.now(timezone.utc) + timedelta(seconds=c.cooldown_seconds)
        )

    await db.flush()
    return OpenCaseResp(
        success=True,
        item=ItemOut.model_validate(selected),
        balance_after=u_refresh.balance,
    )


# Inventory
@app.get("/inventory/", response_model=List[InventoryOut])
async def get_inventory(
    rarity: str = Query(None),
    sort_by: str = Query("obtained_at"),
    sort_order: str = Query("desc"),
    limit: int = 50, offset: int = 0,
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    q = select(UserInventory).where(UserInventory.user_id == u.id)
    if rarity:
        q = q.where(UserInventory.item_rarity == rarity)

    col = {
        "obtained_at": UserInventory.obtained_at,
        "value": UserInventory.item_value,
        "rarity": UserInventory.item_rarity,
    }.get(sort_by, UserInventory.obtained_at)

    q = q.order_by(col.desc() if sort_order == "desc" else col.asc())
    q = q.offset(offset).limit(limit)

    items = (await db.execute(q)).scalars().all()
    return [InventoryOut.model_validate(i) for i in items]


@app.get("/inventory/count")
async def inv_count(
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(
            UserInventory.item_rarity,
            func.count(UserInventory.id),
            func.sum(UserInventory.item_value),
        )
        .where(UserInventory.user_id == u.id)
        .group_by(UserInventory.item_rarity)
    )

    counts = {}
    total_i = 0
    total_v = Decimal("0")
    for rarity, cnt, val in r:
        counts[rarity or "unknown"] = {"count": cnt, "total_value": str(val or 0)}
        total_i += cnt
        total_v += Decimal(str(val or 0))

    return {
        "total_items": total_i,
        "total_value": str(total_v),
        "by_rarity": counts,
    }


@app.post("/inventory/sell", response_model=SellResp)
async def sell_item(
    req: SellReq, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = (await db.execute(
        select(UserInventory).where(
            and_(UserInventory.id == req.item_id, UserInventory.user_id == u.id)
        ).with_for_update()
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Not found")

    price = (Decimal(str(item.item_value or 0)) * Decimal("0.8")).quantize(
        Decimal("0.01")
    )
    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()
    u_refresh.balance += price

    await db.delete(item)
    await db.flush()
    return SellResp(success=True, sold_for=price, balance_after=u_refresh.balance)


# Stars (реальное пополнение)
@app.post("/stars/deposit", response_model=StarsDepositResp)
async def stars_deposit(
    req: StarsDepositReq, u: User = Depends(get_current_user),
):
    if rate_limiter.check(u.id, "deposit"):
        raise HTTPException(429, "Rate limit")
    if req.amount < settings.MIN_DEPOSIT_STARS:
        raise HTTPException(400, f"Minimum {settings.MIN_DEPOSIT_STARS} Stars")

    gross = Decimal(str(req.amount)) * Decimal(str(settings.STARS_RATE))
    fee = (gross * Decimal(str(settings.DEPOSIT_FEE_PERCENT)) / 100).quantize(
        Decimal("0.01")
    )
    received = gross - fee

    payment_id = f"stars_{u.id}_{int(time.time())}_{secrets.token_hex(4)}"
    payment_link = f"https://t.me/{settings.BOT_USERNAME}?start=pay_{payment_id}"

    return StarsDepositResp(
        payment_link=payment_link,
        amount_stars=req.amount,
        amount_received=received,
        fee=fee,
    )


@app.post("/stars/deposit/confirm")
async def confirm_deposit(
    payment_data: dict,
    u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_telegram_payment(payment_data):
        raise HTTPException(400, "Invalid payment signature")

    pid = payment_data.get("telegram_payment_id")
    amt = payment_data.get("amount_stars")
    if not pid or not amt:
        raise HTTPException(400, "Invalid data")

    ex = (await db.execute(
        select(DepositTransaction).where(DepositTransaction.telegram_payment_id == pid)
    )).scalar_one_or_none()
    if ex:
        raise HTTPException(409, "Already processed")

    gross = Decimal(str(amt)) * Decimal(str(settings.STARS_RATE))
    fee = (gross * Decimal(str(settings.DEPOSIT_FEE_PERCENT)) / 100).quantize(
        Decimal("0.01")
    )
    received = gross - fee

    db.add(DepositTransaction(
        user_id=u.id, telegram_payment_id=pid, amount_stars=int(amt),
        amount_received=received, fee=fee, verified=True,
    ))

    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()
    u_refresh.balance += received
    u_refresh.total_deposited += received

    await db.flush()
    return {
        "success": True,
        "balance": str(u_refresh.balance),
        "amount_received": str(received),
    }


@app.get("/stars/rate")
async def stars_rate():
    return {
        "rate": settings.STARS_RATE,
        "min_deposit": settings.MIN_DEPOSIT_STARS,
        "fee_percent": settings.DEPOSIT_FEE_PERCENT,
        "min_withdraw": settings.MIN_WITHDRAW,
        "withdraw_fee_percent": settings.WITHDRAW_FEE_PERCENT,
    }


# Withdraw (исправленный)
@app.post("/withdraw/", response_model=WithdrawOut)
async def create_withdraw(
    req: WithdrawReq, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if rate_limiter.check(u.id, "withdraw"):
        raise HTTPException(429, "Rate limit")

    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()

    if req.amount < settings.MIN_WITHDRAW:
        raise HTTPException(400, f"Minimum {settings.MIN_WITHDRAW}")
    if req.amount > u_refresh.balance:
        raise HTTPException(400, "Insufficient balance")

    fee = (req.amount * Decimal(str(settings.WITHDRAW_FEE_PERCENT)) / 100).quantize(
        Decimal("0.01")
    )
    after = req.amount - fee

    # Списываем баланс сразу
    u_refresh.balance -= req.amount

    wr = WithdrawRequest(
        user_id=u.id, amount=req.amount, fee=fee,
        amount_after_fee=after, status="pending",
    )
    db.add(wr)
    await db.flush()
    return WithdrawOut.model_validate(wr)


@app.get("/withdraw/pending")
async def pending_withdraws(
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(WithdrawRequest).where(
            and_(WithdrawRequest.user_id == u.id, WithdrawRequest.status == "pending")
        )
    )
    return [WithdrawOut.model_validate(w) for w in r.scalars().all()]


# Crash
@app.get("/crash/current")
async def crash_current():
    if current_crash_game:
        return {
            "game_id": current_crash_game.id,
            "multiplier": str(current_multiplier),
            "status": "running",
        }
    return {"status": "waiting", "next_game_in": 5}


@app.post("/crash/bet")
async def crash_bet(
    req: CrashBetReq, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if rate_limiter.check(u.id, "crash_bet"):
        raise HTTPException(429)
    if not current_crash_game:
        raise HTTPException(400, "No active game")
    if not u.can_odd_bets and req.amount % 1 != 0:
        raise HTTPException(400, "Odd bets not allowed — buy upgrade in shop")

    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()
    if u_refresh.balance < req.amount:
        raise HTTPException(400, "Insufficient balance")

    u_refresh.balance -= req.amount
    bet = CrashBet(
        game_id=current_crash_game.id, user_id=u.id,
        amount=req.amount, auto_cashout=req.auto_cashout, status="active",
    )
    db.add(bet)
    await db.flush()
    return {
        "success": True, "bet_id": bet.id,
        "amount": str(bet.amount),
        "auto_cashout": str(bet.auto_cashout) if bet.auto_cashout else None,
        "balance": str(u_refresh.balance),
    }


@app.post("/crash/cashout")
async def crash_cashout(
    req: CrashCashoutReq, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_crash_game:
        raise HTTPException(400, "No active game")

    async with crash_lock:
        bet = (await db.execute(
            select(CrashBet).where(and_(
                CrashBet.user_id == u.id,
                CrashBet.game_id == current_crash_game.id,
                CrashBet.status == "active",
            ))
        )).scalar_one_or_none()

        if not bet:
            raise HTTPException(404, "No active bet")
        if current_multiplier > current_crash_game.crash_point:
            raise HTTPException(400, "Already crashed")

        profit = (bet.amount * current_multiplier) - bet.amount
        bet.cashout_multiplier = current_multiplier
        bet.profit = profit
        bet.status = "cashed_out"

        u_refresh = (await db.execute(
            select(User).where(User.id == u.id).with_for_update()
        )).scalar_one()
        u_refresh.balance += bet.amount + profit
        await db.flush()

    return {
        "success": True, "multiplier": str(current_multiplier),
        "profit": str(profit), "total_return": str(bet.amount + profit),
        "balance": str(u_refresh.balance),
    }


@app.get("/crash/history")
async def crash_history(limit: int = 10, db: AsyncSession = Depends(get_db)):
    games = (await db.execute(
        select(CrashGame).order_by(CrashGame.created_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": g.id, "crash_point": str(g.crash_point),
            "created_at": g.created_at.isoformat() if g.created_at else None,
        }
        for g in games
    ]


# Arena
@app.get("/arena/games")
async def arena_games(db: AsyncSession = Depends(get_db)):
    games = (await db.execute(
        select(ArenaGame).where(ArenaGame.status.in_(["waiting", "in_progress"]))
    )).scalars().all()

    res = []
    for g in games:
        players = (await db.execute(
            select(ArenaPlayer, User.username, User.first_name)
            .join(User, ArenaPlayer.user_id == User.id)
            .where(ArenaPlayer.game_id == g.id)
        )).all()

        pl = [
            {
                "user_id": p.user_id, "username": un, "first_name": fn,
                "bet_amount": str(p.bet_amount), "win_chance": str(p.win_chance),
            }
            for p, un, fn in players
        ]
        res.append({
            "id": g.id, "creator_id": g.creator_id, "status": g.status,
            "total_pot": str(g.total_pot), "players_count": len(pl),
            "max_players": settings.ARENA_MAX_PLAYERS, "players": pl,
        })
    return res


@app.post("/arena/create")
async def arena_create(
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if rate_limiter.check(u.id, "arena_join"):
        raise HTTPException(429)

    # Лимит: 3 активные арены
    active_count = (await db.execute(
        select(func.count(ArenaGame.id)).where(and_(
            ArenaGame.creator_id == u.id, ArenaGame.status == "waiting",
        ))
    )).scalar()
    if active_count >= 3:
        raise HTTPException(400, "Maximum 3 active arenas")

    g = ArenaGame(creator_id=u.id, status="waiting")
    db.add(g)
    await db.flush()
    return {"game_id": g.id, "status": g.status, "max_players": settings.ARENA_MAX_PLAYERS}


@app.post("/arena/join/{game_id}")
async def arena_join(
    game_id: int, req: ArenaJoinReq,
    u: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if rate_limiter.check(u.id, "arena_join"):
        raise HTTPException(429)

    g = (await db.execute(
        select(ArenaGame).where(ArenaGame.id == game_id).with_for_update()
    )).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Game not found")
    if g.status != "waiting":
        raise HTTPException(400, "Already started")

    ex = (await db.execute(
        select(ArenaPlayer).where(and_(
            ArenaPlayer.game_id == game_id, ArenaPlayer.user_id == u.id,
        ))
    )).scalar_one_or_none()
    if ex:
        raise HTTPException(400, "Already joined")

    cnt = (await db.execute(
        select(func.count(ArenaPlayer.id)).where(ArenaPlayer.game_id == game_id)
    )).scalar()
    if cnt >= settings.ARENA_MAX_PLAYERS:
        raise HTTPException(400, "Arena full")

    u_refresh = (await db.execute(
        select(User).where(User.id == u.id).with_for_update()
    )).scalar_one()
    if u_refresh.balance < req.bet_amount:
        raise HTTPException(400, "Insufficient balance")

    u_refresh.balance -= req.bet_amount
    g.total_pot += req.bet_amount
    db.add(ArenaPlayer(game_id=g.id, user_id=u.id, bet_amount=req.bet_amount))
    await db.flush()
    return {
        "success": True, "game_id": g.id,
        "bet_amount": str(req.bet_amount), "total_pot": str(g.total_pot),
        "balance": str(u_refresh.balance),
    }


@app.post("/arena/start/{game_id}")
async def arena_start(
    game_id: int, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    g = (await db.execute(
        select(ArenaGame).where(ArenaGame.id == game_id).with_for_update()
    )).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Game not found")
    if g.creator_id != u.id and not u.is_admin:
        raise HTTPException(403, "Only creator can start")

    players = (await db.execute(
        select(ArenaPlayer).where(ArenaPlayer.game_id == game_id)
    )).scalars().all()
    if len(players) < 2:
        raise HTTPException(400, "Need 2+ players")

    tot = g.total_pot
    for p in players:
        p.win_chance = (p.bet_amount / tot).quantize(Decimal("0.0001"))
    g.status = "in_progress"

    rand_val = random.random()
    cum = Decimal("0")
    winner = players[-1]
    for p in players:
        cum += p.win_chance
        if Decimal(str(rand_val)) <= cum:
            winner = p
            break

    fee_pct = Decimal(str(settings.ARENA_PLATFORM_FEE)) / 100
    pf = (tot * fee_pct).quantize(Decimal("0.01"))
    prize = tot - pf

    g.platform_fee = pf
    g.winner_id = winner.user_id
    g.status = "completed"

    winner.result = "win"
    for p in players:
        if p.id != winner.id:
            p.result = "lose"

    wu = (await db.execute(
        select(User).where(User.id == winner.user_id).with_for_update()
    )).scalar_one()
    wu.balance += prize
    await db.flush()
    return {
        "success": True, "winner_id": wu.id, "winner_username": wu.username,
        "total_pot": str(tot), "platform_fee": str(pf), "winner_prize": str(prize),
    }


# Upgrade (исправленный)
@app.post("/upgrade/", response_model=UpgradeResp)
async def upgrade(
    req: UpgradeReq, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if rate_limiter.check(u.id, "upgrade"):
        raise HTTPException(429)

    frm = (await db.execute(
        select(UserInventory).where(and_(
            UserInventory.id == req.item_id, UserInventory.user_id == u.id,
        ))
    )).scalar_one_or_none()
    to = (await db.execute(
        select(UserInventory).where(and_(
            UserInventory.id == req.target_item_id, UserInventory.user_id == u.id,
        ))
    )).scalar_one_or_none()

    if not frm or not to:
        raise HTTPException(404, "Items not found")
    if frm.id == to.id:
        raise HTTPException(400, "Same item")

    fv = Decimal(str(frm.item_value or 0))
    tv = Decimal(str(to.item_value or 0))

    if tv <= fv:
        raise HTTPException(400, "Target must be more expensive")

    chance = min(max(fv / tv, Decimal("0.01")), Decimal("0.99"))
    success = random.random() < float(chance)

    await db.delete(frm)

    if success:
        new_val = (tv * Decimal("1.05")).quantize(Decimal("0.01"))
        to.item_value = new_val
        to.is_upgraded = True
        to.upgraded_from_id = frm.id
        db.add(UpgradeHistory(
            user_id=u.id, item_from_id=frm.id, item_to_name=to.item_name,
            item_to_value=to.item_value, success=True,
        ))
        await db.flush()
        return UpgradeResp(
            success=True, message=f"Upgrade! New value: {to.item_value}",
            new_item=InventoryOut.model_validate(to),
        )
    else:
        await db.delete(to)
        db.add(UpgradeHistory(
            user_id=u.id, item_from_id=frm.id, item_to_name=to.item_name,
            item_to_value=tv, success=False,
        ))
        await db.flush()
        return UpgradeResp(
            success=False,
            message=f"Failed! Both items destroyed. Chance was {float(chance) * 100:.1f}%",
        )


# Shop
SHOP_ITEMS = [
    {
        "item_type": "cooldown_remove", "name": "🔥 Снятие кулдауна кейсов",
        "desc": "Убирает задержку между открытиями навсегда", "price": Decimal("500"),
    },
    {
        "item_type": "odd_bets", "name": "🎲 Нечётные ставки в Crash",
        "desc": "Разрешает ставить нечётные суммы", "price": Decimal("250"),
    },
]

DIRECT_ITEMS = [
    {
        "item_type": "direct_1", "name": "🌟 Золотой Меч", "desc": "Легендарный предмет",
        "price": Decimal("1000"), "rarity": "legendary",
    },
    {
        "item_type": "direct_2", "name": "💎 Алмазный Щит", "desc": "Эпический предмет",
        "price": Decimal("500"), "rarity": "epic",
    },
    {
        "item_type": "direct_3", "name": "🔥 Огненный Шар", "desc": "Редкий предмет",
        "price": Decimal("200"), "rarity": "rare",
    },
]


@app.get("/shop/")
async def shop():
    return {
        "upgrades": [
            {**i, "price": str(i["price"])} for i in SHOP_ITEMS
        ],
        "direct_items": [
            {**i, "price": str(i["price"])} for i in DIRECT_ITEMS
        ],
    }


@app.post("/shop/buy/{item_type}")
async def shop_buy(
    item_type: str, u: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = next((i for i in SHOP_ITEMS if i["item_type"] == item_type), None)
    ditem = next((i for i in DIRECT_ITEMS if i["item_type"] == item_type), None)

    if item:
        price = item["price"]
        u_refresh = (await db.execute(
            select(User).where(User.id == u.id).with_for_update()
        )).scalar_one()
        if u_refresh.balance < price:
            raise HTTPException(400, "Insufficient balance")

        u_refresh.balance -= price
        if item_type == "cooldown_remove":
            if u_refresh.case_cooldown_removed:
                raise HTTPException(400, "Already purchased")
            u_refresh.case_cooldown_removed = True
        elif item_type == "odd_bets":
            if u_refresh.can_odd_bets:
                raise HTTPException(400, "Already purchased")
            u_refresh.can_odd_bets = True

        await db.flush()
        return {"success": True, "item_type": item_type, "balance": str(u_refresh.balance)}

    if ditem:
        price = ditem["price"]
        u_refresh = (await db.execute(
            select(User).where(User.id == u.id).with_for_update()
        )).scalar_one()
        if u_refresh.balance < price:
            raise HTTPException(400, "Insufficient balance")

        u_refresh.balance -= price
        db.add(UserInventory(
            user_id=u.id, item_name=ditem["name"],
            item_value=price, item_rarity=ditem["rarity"],
        ))
        await db.flush()
        return {"success": True, "item": ditem["name"], "balance": str(u_refresh.balance)}

    raise HTTPException(404, "Item not found")


# WebSocket
class WsManager:
    def __init__(self):
        self.connections: Dict[int, WebSocket] = {}
        self.online: Set[int] = set()
        self.chat_spam: Dict[int, list] = {}

    async def connect(self, ws: WebSocket, user: User):
        await ws.accept()
        self.connections[user.id] = ws
        self.online.add(user.id)
        await self.broadcast_online()

    def disconnect(self, uid: int):
        self.connections.pop(uid, None)
        self.online.discard(uid)
        self.chat_spam.pop(uid, None)

    async def broadcast(self, msg: dict):
        dead = []
        for uid, ws in self.connections.items():
            try:
                await ws.send_text(json.dumps(msg, default=str))
            except Exception:
                dead.append(uid)
        for d in dead:
            self.disconnect(d)

    async def broadcast_online(self):
        await self.broadcast({
            "type": "online_users",
            "count": len(self.online),
            "users": list(self.online),
        })

    def check_spam(self, uid: int) -> bool:
        now = time.time()
        self.chat_spam[uid] = [
            t for t in self.chat_spam.get(uid, []) if now - t < 5
        ]
        if len(self.chat_spam[uid]) >= 3:
            return True
        self.chat_spam[uid].append(now)
        return False


ws_manager = WsManager()


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    async with async_session() as db:
        user = await get_ws_user(websocket, db)
        if not user:
            return

    await ws_manager.connect(websocket, user)

    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data.get("type") == "chat":
                msg = data.get("message", "").strip()
                if not msg or len(msg) > 300:
                    continue

                if ws_manager.check_spam(user.id):
                    await websocket.send_text(json.dumps({
                        "type": "error", "message": "Slow down!",
                    }))
                    continue

                async with async_session() as db:
                    cm = ChatMessage(
                        user_id=user.id,
                        username=user.username or f"user_{user.id}",
                        message=msg,
                    )
                    db.add(cm)
                    await db.commit()
                    await ws_manager.broadcast({
                        "type": "chat_message",
                        "id": cm.id, "user_id": user.id,
                        "username": cm.username, "message": msg,
                        "created_at": cm.created_at.isoformat() if cm.created_at else None,
                    })
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_manager.disconnect(user.id)
        await ws_manager.broadcast_online()


@app.get("/ws/online")
async def online():
    return {"online_count": len(ws_manager.online)}


@app.get("/ws/chat/history")
async def chat_hist(limit: int = 50):
    async with async_session() as db:
        msgs = (await db.execute(
            select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)
        )).scalars().all()
        return [
            {
                "id": m.id, "user_id": m.user_id, "username": m.username,
                "message": m.message,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(msgs)
        ]


# Admin
@app.get("/admin/users")
async def admin_users(
    limit: int = 100, offset: int = 0, search: str = None,
    db: AsyncSession = Depends(get_db), admin: User = Depends(get_admin),
):
    q = select(User)
    if search:
        q = q.where(or_(
            User.username.ilike(f"%{search}%"),
            User.first_name.ilike(f"%{search}%"),
            User.telegram_id.cast(str).ilike(f"%{search}%"),
        ))
    q = q.order_by(User.registered_at.desc()).offset(offset).limit(limit)
    users = (await db.execute(q)).scalars().all()
    return [UserOut.model_validate(u) for u in users]


@app.post("/admin/balance")
async def admin_balance(
    req: AdminBalanceReq, db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin),
):
    u = (await db.execute(
        select(User).where(User.id == req.user_id).with_for_update()
    )).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    amt = Decimal(str(req.amount))
    if req.operation == "add":
        u.balance += amt
    elif req.operation == "subtract":
        u.balance = max(u.balance - amt, Decimal("0"))
    elif req.operation == "set":
        u.balance = amt
    else:
        raise HTTPException(400, "Invalid operation")

    db.add(ActionLog(
        user_id=admin.id, action_type="balance_update",
        description=f"User {u.id}: {req.operation} {amt}",
    ))
    await db.flush()
    return {"success": True, "new_balance": str(u.balance)}


@app.post("/admin/block")
async def admin_block(
    req: AdminBlockReq, db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin),
):
    u = (await db.execute(
        select(User).where(User.id == req.user_id)
    )).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    u.is_blocked = req.block
    db.add(ActionLog(
        user_id=admin.id,
        action_type="block" if req.block else "unblock",
        description=f"User {u.id} {'blocked' if req.block else 'unblocked'}",
    ))
    await db.flush()
    return {"success": True, "is_blocked": u.is_blocked}


@app.post("/admin/give-item")
async def admin_give(
    req: AdminGiveItemReq, db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin),
):
    u = (await db.execute(
        select(User).where(User.id == req.user_id)
    )).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    item = UserInventory(
        user_id=u.id, item_name=req.item_name,
        item_image_url=req.item_image_url, item_value=req.item_value,
        item_rarity=req.item_rarity,
    )
    db.add(item)
    db.add(ActionLog(
        user_id=admin.id, action_type="give_item",
        description=f"Gave '{req.item_name}' to user {u.id}",
    ))
    await db.flush()
    return {"success": True, "item_id": item.id}


@app.get("/admin/withdrawals")
async def admin_withdrawals(
    status: str = "pending", db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin),
):
    reqs = (await db.execute(
        select(WithdrawRequest).where(WithdrawRequest.status == status)
        .order_by(WithdrawRequest.created_at.desc())
    )).scalars().all()
    return [
        {
            "id": r.id, "user_id": r.user_id, "amount": str(r.amount),
            "fee": str(r.fee), "amount_after_fee": str(r.amount_after_fee),
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reqs
    ]


@app.post("/admin/withdrawals/process")
async def admin_process_withdraw(
    req: AdminWithdrawReq, db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin),
):
    wr = (await db.execute(
        select(WithdrawRequest).where(WithdrawRequest.id == req.request_id)
    )).scalar_one_or_none()
    if not wr:
        raise HTTPException(404, "Not found")
    if wr.status != "pending":
        raise HTTPException(400, "Already processed")

    u = (await db.execute(
        select(User).where(User.id == wr.user_id).with_for_update()
    )).scalar_one()

    if req.action == "approve":
        wr.status = "approved"
        u.total_withdrawn += wr.amount_after_fee
    elif req.action == "reject":
        wr.status = "rejected"
        u.balance += wr.amount  # Возврат денег
    else:
        raise HTTPException(400, "Invalid action")

    wr.admin_id = admin.id
    wr.admin_comment = req.comment
    wr.processed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"success": True, "status": wr.status}


# ======================== ФРОНТЕНД ========================

@app.get("/", response_class=HTMLResponse)
async def root():
    return """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CaseFight</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
        body{background:#0a0e14;color:#e6e8ec;min-height:100vh;display:flex;flex-direction:column}
        header{background:#12161e;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;border-bottom:1px solid rgba(255,255,255,0.06)}
        .logo{font-size:20px;font-weight:900;background:linear-gradient(135deg,#f5a623,#f7c948);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .balance{background:#1a1f2b;padding:8px 14px;border-radius:20px;font-weight:700;font-size:14px;cursor:pointer}
        main{padding:16px;flex:1;overflow-y:auto}
        .menu-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
        .menu-item{background:#1e2430;border-radius:12px;padding:24px 16px;text-align:center;cursor:pointer;border:1px solid rgba(255,255,255,0.04);transition:all 0.2s}
        .menu-item:hover{background:#252c38;transform:translateY(-2px)}
        .menu-item:active{transform:scale(0.97)}
        .menu-icon{font-size:36px;margin-bottom:8px}
        .menu-label{font-weight:700;font-size:14px}
        h1{text-align:center;margin:20px 0;font-size:36px;font-weight:900;background:linear-gradient(135deg,#f5a623,#f7c948);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .case-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
        .case-card{background:#1e2430;border-radius:12px;padding:16px;cursor:pointer;text-align:center;border:1px solid rgba(255,255,255,0.04)}
        .case-card:hover{background:#252c38}
        .case-name{font-weight:700;font-size:13px;margin-bottom:4px}
        .case-price{color:#f5a623;font-weight:600;font-size:13px}
        button{background:linear-gradient(135deg,#f5a623,#f7c948);color:#000;border:none;padding:14px 28px;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer;width:100%;margin-top:12px}
        button:active{transform:scale(0.97)}
        input,select{width:100%;background:#1a1f2b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:12px;color:#e6e8ec;font-size:14px;margin-bottom:8px}
        .modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:200;display:flex;align-items:center;justify-content:center}
        .modal-content{background:#12161e;border-radius:16px;padding:24px;width:90%;max-width:380px;max-height:80vh;overflow-y:auto}
        .bottom-nav{position:fixed;bottom:0;left:0;right:0;background:#12161e;display:flex;justify-content:space-around;padding:8px 4px 16px;border-top:1px solid rgba(255,255,255,0.06)}
        .nav-btn{background:none;border:none;color:#5c6370;font-size:11px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:4px}
        .nav-btn.active{color:#f5a623}
        .toast{position:fixed;top:70px;right:16px;background:#1e2430;padding:12px 16px;border-radius:8px;font-size:13px;font-weight:600;z-index:300;border-left:3px solid #f5a623;animation:slideIn 0.3s}
        @keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
        #loading{position:fixed;top:0;left:0;width:100%;height:100%;background:#0a0e14;display:flex;align-items:center;justify-content:center;z-index:9999;flex-direction:column}
        .spinner{width:40px;height:40px;border:3px solid #1a1f2b;border-top-color:#f5a623;border-radius:50%;animation:spin 0.8s linear infinite}
        @keyframes spin{to{transform:rotate(360deg)}}
    </style>
</head>
<body>
    <div id="loading"><div class="spinner"></div><p style="margin-top:16px;color:#8b92a0">Загрузка...</p></div>
    <div id="app" style="display:none">
        <header>
            <div class="logo" onclick="page('home')">⚔️ CaseFight</div>
            <div class="balance" onclick="page('deposit')">⭐ <span id="bal">0</span></div>
        </header>
        <main id="content"></main>
        <nav class="bottom-nav">
            <button class="nav-btn active" data-p="home"><span style="font-size:20px">🏠</span>Главная</button>
            <button class="nav-btn" data-p="cases"><span style="font-size:20px">📦</span>Кейсы</button>
            <button class="nav-btn" data-p="crash"><span style="font-size:20px">🐸</span>Crash</button>
            <button class="nav-btn" data-p="arena"><span style="font-size:20px">⚔️</span>Арена</button>
            <button class="nav-btn" data-p="inv"><span style="font-size:20px">🎒</span>Инвентарь</button>
        </nav>
    </div>
    <div id="modal" class="modal" style="display:none" onclick="if(event.target===this)closeModal()">
        <div class="modal-content" id="modal-body"></div>
    </div>
    <div id="toasts"></div>

<script>
const API = window.location.origin;
let state = {user:null,balance:0,page:'home'};
const h={};
if(window.Telegram?.WebApp){const tg=window.Telegram.WebApp;tg.ready();tg.expand();h['X-Telegram-Init-Data']=tg.initData}
else{h['X-Dev-Token']='${settings.SECRET_KEY}';h['X-Telegram-User-Id']='123456789'}

async function api(method,url,body=null){
    const opts={method,headers:{'Content-Type':'application/json',...h}};
    if(body)opts.body=JSON.stringify(body);
    const r=await fetch(API+url,opts);
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Error');
    return d;
}

async function loadProfile(){
    try{
        const p=await api('GET','/auth/profile');
        state.user=p.user;state.balance=parseFloat(p.user.balance);
        document.getElementById('bal').textContent=fmt(state.balance);
    }catch(e){console.error(e)}
}

function fmt(n){return parseFloat(n).toLocaleString('ru-RU',{maximumFractionDigits:2})}
function toast(msg,err){const t=document.getElementById('toasts');const d=document.createElement('div');d.className='toast';if(err)d.style.borderLeftColor='#f44336';d.textContent=msg;t.appendChild(d);setTimeout(()=>{d.style.opacity='0';setTimeout(()=>d.remove(),300)},3000)}
function showModal(html){document.getElementById('modal-body').innerHTML=html;document.getElementById('modal').style.display='flex'}
function closeModal(){document.getElementById('modal').style.display='none'}

function page(p){
    state.page=p;
    document.querySelectorAll('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.p===p));
    renderPage(p);
}

async function renderPage(p){
    const c=document.getElementById('content');
    switch(p){
        case'home':
            c.innerHTML=`<h1>⚔️ CaseFight</h1><p style="text-align:center;color:#8b92a0;margin-bottom:20px">Открывай кейсы и побеждай!</p><div class="menu-grid"><div class="menu-item"onclick="page('cases')"><div class="menu-icon">📦</div><div class="menu-label">Кейсы</div></div><div class="menu-item"onclick="page('crash')"><div class="menu-icon">🐸</div><div class="menu-label">Crash</div></div><div class="menu-item"onclick="page('arena')"><div class="menu-icon">⚔️</div><div class="menu-label">Арена</div></div><div class="menu-item"onclick="page('inv')"><div class="menu-icon">🎒</div><div class="menu-label">Инвентарь</div></div><div class="menu-item"onclick="page('shop')"><div class="menu-icon">🛒</div><div class="menu-label">Магазин</div></div><div class="menu-item"onclick="page('upgrade')"><div class="menu-icon">⬆️</div><div class="menu-label">Апгрейд</div></div></div>`;
            break;
        case'cases':
            c.innerHTML='<h2>📦 Кейсы</h2><div class="case-grid" id="caseGrid">Загрузка...</div>';
            try{
                const cases=await api('GET','/cases/');
                document.getElementById('caseGrid').innerHTML=cases.map(cs=>`<div class="case-card"onclick="openCaseModal(${cs.id})"><div class="case-name">${cs.name}</div><div class="case-price">⭐ ${fmt(cs.price)}</div></div>`).join('');
            }catch(e){toast('Ошибка',1)}
            break;
        case'crash':
            c.innerHTML=`<div style="text-align:center"><h2>🐸 Crash</h2><div style="font-size:60px;font-weight:900;color:#4caf50"id="crashMult">1.00x</div><div id="crashStatus">Ожидание...</div><input type="number"id="crashBet"placeholder="Ставка"value="10"step="1"><input type="number"id="crashAuto"placeholder="Автокэшаут (x)"value="2.0"step="0.1"><button onclick="crashBet()">🎲 Поставить</button><button onclick="crashCashout()"id="crashCashoutBtn"style="display:none">💰 Забрать</button></div>`;
            setInterval(async()=>{
                try{const g=await api('GET','/crash/current');if(g.status==='running'){document.getElementById('crashMult').textContent=parseFloat(g.multiplier).toFixed(2)+'x';document.getElementById('crashStatus').textContent='Летит! 🚀'}else{document.getElementById('crashStatus').textContent='Ожидание...';document.getElementById('crashMult').textContent='1.00x'}}catch(e){}
            },200);
            break;
        case'arena':
            c.innerHTML='<h2>⚔️ Арена</h2><div id="arenaList">Загрузка...</div><button onclick="arenaCreate()">➕ Создать арену</button>';
            try{
                const games=await api('GET','/arena/games');
                document.getElementById('arenaList').innerHTML=games.length?games.map(g=>`<div class="case-card"><div class="case-name">Арена #${g.id}</div><div class="case-price">Банк: ⭐${fmt(g.total_pot)} | ${g.players_count}/${g.max_players}</div>${g.status==='waiting'?`<button onclick="arenaJoin(${g.id})">🎯 Войти</button>`:''}</div>`).join(''):'<p style="text-align:center;color:#8b92a0;padding:20px">Нет активных арен</p>';
            }catch(e){toast('Ошибка',1)}
            break;
        case'inv':
            c.innerHTML='<h2>🎒 Инвентарь</h2><div class="case-grid"id="invGrid">Загрузка...</div>';
            try{
                const items=await api('GET','/inventory/?limit=50');
                document.getElementById('invGrid').innerHTML=items.length?items.map(i=>`<div class="case-card"onclick="sellItem(${i.id})"><div class="case-name">${i.item_name}</div><div class="case-price">⭐ ${fmt(i.item_value)}</div></div>`).join(''):'<p style="text-align:center;color:#8b92a0;padding:20px">Инвентарь пуст</p>';
            }catch(e){toast('Ошибка',1)}
            break;
        case'shop':
            c.innerHTML='<h2>🛒 Магазин</h2><div id="shopItems">Загрузка...</div>';
            try{
                const s=await api('GET','/shop/');
                document.getElementById('shopItems').innerHTML=[...s.upgrades.map(i=>`<div class="case-card"><div class="case-name">${i.name}</div><div class="case-price">⭐ ${i.price}</div><button onclick="shopBuy('${i.item_type}')">Купить</button></div>`),...s.direct_items.map(i=>`<div class="case-card"><div class="case-name">${i.name}</div><div class="case-price">⭐ ${i.price}</div><button onclick="shopBuy('${i.item_type}')">Купить</button></div>`)].join('');
            }catch(e){toast('Ошибка',1)}
            break;
        case'upgrade':
            c.innerHTML='<h2>⬆️ Апгрейд</h2><p style="text-align:center;color:#8b92a0">Выберите 2 предмета из инвентаря</p><div class="case-grid"id="upgradeGrid">Загрузка...</div>';
            try{
                const items=await api('GET','/inventory/?limit=50');
                let sel=[];
                document.getElementById('upgradeGrid').innerHTML=items.map(i=>`<div class="case-card"id="upg_${i.id}"onclick="selectUpgrade(${i.id},'${i.item_name}')"><div class="case-name">${i.item_name}</div><div class="case-price">⭐ ${fmt(i.item_value)}</div></div>`).join('');
                window._upgSel=[];window._upgItems=items;
            }catch(e){toast('Ошибка',1)}
            break;
        case'deposit':
            c.innerHTML='<h2>⭐ Пополнение</h2><input type="number"id="depAmt"placeholder="Сумма в Stars (мин 50)"value="50"min="50"><button onclick="deposit()">Пополнить</button><div style="margin-top:20px"><h2>💸 Вывод</h2><input type="number"id="witAmt"placeholder="Сумма (мин 100)"value="100"min="100"><button onclick="withdraw()">Создать заявку</button></div>';
            break;
    }
}

window.selectUpgrade=function(id,name){
    if(!window._upgSel)window._upgSel=[];
    if(window._upgSel.length>=2)window._upgSel=[];
    window._upgSel.push(id);
    document.getElementById('upg_'+id).style.border='2px solid #f5a623';
    if(window._upgSel.length===2){
        showModal(`<h3>Апгрейд?</h3><p>Предмет 1: ${window._upgItems.find(i=>i.id===window._upgSel[0])?.item_name}</p><p>Предмет 2: ${name}</p><p style="color:#ff9800;font-size:12px">⚠️ При неудаче оба уничтожаются!</p><button onclick="doUpgrade()">Подтвердить</button>`);
    }
};

window.doUpgrade=async function(){
    try{
        const r=await api('POST','/upgrade/',{item_id:window._upgSel[0],target_item_id:window._upgSel[1]});
        toast(r.message,r.success?0:1);
        closeModal();page('inv');
    }catch(e){toast(e.message,1)}
};

window.sellItem=async function(id){
    showModal(`<h3>Продать предмет?</h3><p>Цена: 80% от стоимости</p><button onclick="doSell(${id})">Продать</button>`);
};

window.doSell=async function(id){
    try{const r=await api('POST','/inventory/sell',{item_id:id});state.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(state.balance);toast('Продано за ⭐'+fmt(r.sold_for));closeModal();page('inv')}catch(e){toast(e.message,1)}
};

window.openCaseModal=async function(id){
    const c=await api('GET','/cases/'+id);
    showModal(`<h3>${c.name}</h3><p>Цена: ⭐${fmt(c.price)}</p><p>RTP: ~${(c.items.reduce((s,i)=>s+parseFloat(i.value)*parseFloat(i.drop_chance),0)/parseFloat(c.price)*100).toFixed(0)}%</p><button onclick="openCase(${id},${c.price})">Открыть</button>`);
};

window.openCase=async function(id,price){
    if(state.balance<price){toast('Недостаточно средств',1);return}
    closeModal();
    try{
        const r=await api('POST','/cases/open',{case_id:id,idempotency_key:'k_'+Date.now()+'_'+Math.random().toString(36).slice(2)});
        state.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(state.balance);
        showModal(`<h3>🎉 ${r.item.name}!</h3><p>Стоимость: ⭐${fmt(r.item.value)}</p><p style="text-transform:uppercase">${r.item.rarity}</p><button onclick="closeModal()">OK</button>`);
    }catch(e){toast(e.message,1)}
};

window.crashBet=async function(){
    const amt=parseFloat(document.getElementById('crashBet').value);
    const auto=document.getElementById('crashAuto').value?parseFloat(document.getElementById('crashAuto').value):null;
    try{
        const r=await api('POST','/crash/bet',{amount:amt,auto_cashout:auto});
        state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);
        document.getElementById('crashCashoutBtn').style.display='block';
        window._crashBetId=r.bet_id;
        toast('Ставка принята!');
    }catch(e){toast(e.message,1)}
};

window.crashCashout=async function(){
    try{
        const r=await api('POST','/crash/cashout',{game_id:0});
        state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);
        document.getElementById('crashCashoutBtn').style.display='none';
        toast('Выигрыш: +'+fmt(r.profit)+' ('+parseFloat(r.multiplier).toFixed(2)+'x)');
    }catch(e){toast(e.message,1)}
};

window.arenaCreate=async function(){
    try{const r=await api('POST','/arena/create');toast('Арена #'+r.game_id+' создана!');page('arena')}catch(e){toast(e.message,1)}
};

window.arenaJoin=async function(id){
    const amt=prompt('Сумма ставки:');
    if(!amt)return;
    try{const r=await api('POST','/arena/join/'+id,{bet_amount:parseFloat(amt)});state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);toast('Вы в игре!');page('arena')}catch(e){toast(e.message,1)}
};

window.shopBuy=async function(type){
    try{const r=await api('POST','/shop/buy/'+type);state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);toast('Куплено!');loadProfile()}catch(e){toast(e.message,1)}
};

window.deposit=async function(){
    const amt=parseInt(document.getElementById('depAmt').value);
    if(amt<50){toast('Минимум 50 Stars',1);return}
    try{
        const r=await api('POST','/stars/deposit',{amount:amt});
        showModal(`<h3>⭐ Пополнение</h3><p>Сумма: ${r.amount_stars} Stars</p><p>К зачислению: ${fmt(r.amount_received)}</p><p style="font-size:12px;color:#8b92a0">Комиссия: ${fmt(r.fee)}</p><button onclick="confirmDeposit(${amt},'${r.payment_link.split('pay_')[1]}')">Подтвердить (демо)</button>`);
    }catch(e){toast(e.message,1)}
};

window.confirmDeposit=async function(amt,pid){
    try{
        const r=await api('POST','/stars/deposit/confirm',{telegram_payment_id:pid,amount_stars:amt,signature:'demo',timestamp:Math.floor(Date.now()/1000)});
        state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);
        closeModal();toast('Баланс пополнен на ⭐'+fmt(r.amount_received));
    }catch(e){toast(e.message,1)}
};

window.withdraw=async function(){
    const amt=parseFloat(document.getElementById('witAmt').value);
    if(amt<100){toast('Минимум 100',1);return}
    try{
        const r=await api('POST','/withdraw/',{amount:amt});
        state.balance=parseFloat((await api('GET','/user/balance')).balance);
        document.getElementById('bal').textContent=fmt(state.balance);
        toast('Заявка #'+r.id+' создана. Ожидайте обработки.');
    }catch(e){toast(e.message,1)}
};

(async function(){
    await loadProfile();
    document.getElementById('loading').style.display='none';
    document.getElementById('app').style.display='flex';
    page('home');
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
