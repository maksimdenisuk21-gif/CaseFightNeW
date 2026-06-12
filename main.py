"""
CaseFight — Telegram Mini App
Full backend + frontend
"""

import asyncio, hashlib, hmac, json, os, random, secrets, time, urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, List, Set

from fastapi import FastAPI, HTTPException, Request, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean,
    DateTime, ForeignKey, DECIMAL, select, func, and_, or_, delete
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()

# ======================== SETTINGS ========================
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "CaseFightBot")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/casefight")
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
    CORS_ORIGINS: List[str] = os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")

settings = Settings()

# ======================== DATABASE ========================
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"): db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with async_session() as s:
        try: yield s; await s.commit()
        except: await s.rollback(); raise

# ======================== MODELS ========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255)); first_name = Column(String(255)); avatar_url = Column(Text)
    balance = Column(DECIMAL(15,2), default=Decimal("0.00"))
    total_deposited = Column(DECIMAL(15,2), default=Decimal("0.00"))
    total_withdrawn = Column(DECIMAL(15,2), default=Decimal("0.00"))
    is_blocked = Column(Boolean, default=False); is_admin = Column(Boolean, default=False)
    can_odd_bets = Column(Boolean, default=False); case_cooldown_removed = Column(Boolean, default=False)
    case_cooldown_until = Column(DateTime(timezone=True))
    registered_at = Column(DateTime(timezone=True), server_default=func.now())

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True); name = Column(String(255)); description = Column(Text)
    price = Column(DECIMAL(15,2)); image_url = Column(Text); type = Column(String(50), default="stars")
    is_active = Column(Boolean, default=True); cooldown_seconds = Column(Integer, default=0)

class CaseItem(Base):
    __tablename__ = "case_items"
    id = Column(Integer, primary_key=True); case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    name = Column(String(255)); image_url = Column(Text); value = Column(DECIMAL(15,2))
    drop_chance = Column(DECIMAL(6,4)); rarity = Column(String(50), default="common")

class UserInventory(Base):
    __tablename__ = "user_inventory"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_item_id = Column(Integer); case_id = Column(Integer); case_name = Column(String(255))
    item_name = Column(String(255)); item_image_url = Column(Text); item_value = Column(DECIMAL(15,2))
    item_rarity = Column(String(50)); obtained_at = Column(DateTime(timezone=True), server_default=func.now())
    is_upgraded = Column(Boolean, default=False); upgraded_from_id = Column(Integer)

class CaseOpenHistory(Base):
    __tablename__ = "case_open_history"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    case_name = Column(String(255)); item_name = Column(String(255))
    item_value = Column(DECIMAL(15,2)); item_rarity = Column(String(50))
    opened_at = Column(DateTime(timezone=True), server_default=func.now())

class DepositTransaction(Base):
    __tablename__ = "deposit_transactions"
    id = Column(Integer, primary_key=True); user_id = Column(Integer)
    telegram_payment_id = Column(String(255), unique=True); amount_stars = Column(Integer)
    amount_received = Column(DECIMAL(15,2)); fee = Column(DECIMAL(15,2)); verified = Column(Boolean, default=False)

class WithdrawRequest(Base):
    __tablename__ = "withdraw_requests"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); amount = Column(DECIMAL(15,2))
    fee = Column(DECIMAL(15,2)); amount_after_fee = Column(DECIMAL(15,2))
    status = Column(String(50), default="pending", index=True); admin_comment = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now()); processed_at = Column(DateTime(timezone=True))

class CrashGame(Base):
    __tablename__ = "crash_games"
    id = Column(Integer, primary_key=True); crash_point = Column(DECIMAL(10,4)); seed_hash = Column(String(255))
    status = Column(String(50), default="active"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class CrashBet(Base):
    __tablename__ = "crash_bets"
    id = Column(Integer, primary_key=True); game_id = Column(Integer, ForeignKey("crash_games.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount = Column(DECIMAL(15,2)); auto_cashout = Column(DECIMAL(10,4))
    cashout_multiplier = Column(DECIMAL(10,4)); profit = Column(DECIMAL(15,2))
    status = Column(String(50), default="active"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class ArenaGame(Base):
    __tablename__ = "arena_games"
    id = Column(Integer, primary_key=True); creator_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String(50), default="waiting", index=True); total_pot = Column(DECIMAL(15,2), default=Decimal("0.00"))
    platform_fee = Column(DECIMAL(15,2), default=Decimal("0.00")); winner_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now()); completed_at = Column(DateTime(timezone=True))

class ArenaPlayer(Base):
    __tablename__ = "arena_players"
    id = Column(Integer, primary_key=True); game_id = Column(Integer, ForeignKey("arena_games.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer); bet_amount = Column(DECIMAL(15,2)); win_chance = Column(DECIMAL(6,4))
    result = Column(String(50)); joined_at = Column(DateTime(timezone=True), server_default=func.now())

class UpgradeHistory(Base):
    __tablename__ = "upgrade_history"
    id = Column(Integer, primary_key=True); user_id = Column(Integer)
    item_from_id = Column(Integer); item_to_name = Column(String(255)); item_to_value = Column(DECIMAL(15,2))
    success = Column(Boolean); created_at = Column(DateTime(timezone=True), server_default=func.now())

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); username = Column(String(255))
    message = Column(Text); created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class ActionLog(Base):
    __tablename__ = "action_logs"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); action_type = Column(String(100))
    description = Column(Text); created_at = Column(DateTime(timezone=True), server_default=func.now())

class RequestIdempotency(Base):
    __tablename__ = "request_idempotency"
    id = Column(Integer, primary_key=True); idempotency_key = Column(String(255), unique=True)
    user_id = Column(Integer); expires_at = Column(DateTime(timezone=True))

# ======================== SCHEMAS ========================
class UserOut(BaseModel):
    id: int; telegram_id: int; username: Optional[str]; first_name: Optional[str]
    avatar_url: Optional[str]; balance: Decimal; total_deposited: Decimal; total_withdrawn: Decimal
    is_blocked: bool; is_admin: bool; can_odd_bets: bool; case_cooldown_removed: bool
    registered_at: Optional[datetime]; model_config = {"from_attributes": True}

class ProfileOut(BaseModel):
    user: UserOut; inventory_count: int; total_case_opens: int

class ItemOut(BaseModel):
    id: int; case_id: int; name: str; image_url: Optional[str]; value: Decimal; drop_chance: Decimal; rarity: str
    model_config = {"from_attributes": True}

class CaseOut(BaseModel):
    id: int; name: str; description: Optional[str]; price: Decimal; image_url: Optional[str]
    type: str; is_active: bool; cooldown_seconds: int; items: List[ItemOut] = []
    model_config = {"from_attributes": True}

class OpenCaseReq(BaseModel): case_id: int; idempotency_key: str
class OpenCaseResp(BaseModel): success: bool; item: ItemOut; balance_after: Decimal
class InventoryOut(BaseModel):
    id: int; case_name: Optional[str]; item_name: Optional[str]; item_value: Optional[Decimal]
    item_rarity: Optional[str]; obtained_at: Optional[datetime]; is_upgraded: bool
    model_config = {"from_attributes": True}
class SellReq(BaseModel): item_id: int
class SellResp(BaseModel): success: bool; sold_for: Decimal; balance_after: Decimal
class StarsDepositReq(BaseModel): amount: int = Field(ge=50, le=10000)
class StarsDepositResp(BaseModel): invoice_link: str; amount_stars: int; amount_received: Decimal; fee: Decimal
class WithdrawReq(BaseModel): amount: Decimal = Field(ge=100, le=1000000)
class WithdrawOut(BaseModel):
    id: int; amount: Decimal; fee: Decimal; amount_after_fee: Decimal; status: str
    created_at: Optional[datetime]; processed_at: Optional[datetime]; model_config = {"from_attributes": True}
class CrashBetReq(BaseModel): amount: Decimal = Field(gt=0, le=100000); auto_cashout: Optional[Decimal] = Field(default=None, ge=1.01)
class CrashCashoutReq(BaseModel): game_id: int
class ArenaJoinReq(BaseModel): bet_amount: Decimal = Field(gt=0, le=100000)
class UpgradeReq(BaseModel): item_id: int; target_item_id: int
class UpgradeResp(BaseModel): success: bool; message: str; new_value: Optional[Decimal] = None
class AdminBalanceReq(BaseModel): user_id: int; amount: Decimal; operation: str
class AdminBlockReq(BaseModel): user_id: int; block: bool
class AdminGiveItemReq(BaseModel): user_id: int; item_name: str; item_value: Decimal; item_rarity: str = "common"
class AdminWithdrawReq(BaseModel): request_id: int; action: str; comment: Optional[str] = None

# ======================== AUTH ========================
def validate_telegram_init(init_data: str) -> Optional[Dict]:
    if not settings.BOT_TOKEN: return None
    parsed = {}
    for item in init_data.split("&"):
        if "=" in item: k, v = item.split("=", 1); parsed[k] = urllib.parse.unquote(v)
    if "hash" not in parsed: return None
    received = parsed.pop("hash")
    check_str = "\n".join(sorted([f"{k}={v}" for k, v in parsed.items()]))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    if hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest() != received: return None
    if int(time.time()) - int(parsed.get("auth_date", 0)) > 86400: return None
    return parsed

def extract_user(parsed: Dict) -> Dict:
    try: u = json.loads(parsed.get("user", "{}"))
    except: return {}
    return {"telegram_id": u.get("id"), "username": u.get("username"), "first_name": u.get("first_name"), "avatar_url": u.get("photo_url")}

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    if settings.DEBUG:
        dev_id = request.headers.get("X-Telegram-User-Id")
        if dev_id and request.headers.get("X-Dev-Token") == settings.SECRET_KEY:
            r = await db.execute(select(User).where(User.telegram_id == int(dev_id)))
            u = r.scalar_one_or_none()
            if u and not u.is_blocked: return u
            if not u:
                u = User(telegram_id=int(dev_id), username=f"dev_{dev_id}", first_name="DevUser", is_admin=(int(dev_id)==settings.ADMIN_TELEGRAM_ID))
                db.add(u); await db.flush(); await db.refresh(u); return u
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data: raise HTTPException(401, "No auth")
    parsed = validate_telegram_init(init_data)
    if not parsed: raise HTTPException(401, "Invalid auth")
    ud = extract_user(parsed); tid = ud.get("telegram_id")
    if not tid: raise HTTPException(401, "No user data")
    r = await db.execute(select(User).where(User.telegram_id == tid))
    u = r.scalar_one_or_none()
    if not u:
        u = User(telegram_id=tid, username=ud.get("username"), first_name=ud.get("first_name"),
                 avatar_url=ud.get("avatar_url"), is_admin=(tid == settings.ADMIN_TELEGRAM_ID))
        db.add(u); await db.flush(); await db.refresh(u)
    else:
        if ud.get("username"): u.username = ud["username"]
        if ud.get("first_name"): u.first_name = ud["first_name"]
        if tid == settings.ADMIN_TELEGRAM_ID and not u.is_admin: u.is_admin = True
    if u.is_blocked: raise HTTPException(403, "Blocked")
    return u

async def get_ws_user(websocket: WebSocket, db: AsyncSession) -> Optional[User]:
    init_data = websocket.headers.get("X-Telegram-Init-Data")
    if not init_data: await websocket.close(code=4001, reason="No auth"); return None
    parsed = validate_telegram_init(init_data)
    if not parsed: await websocket.close(code=4001, reason="Invalid"); return None
    ud = extract_user(parsed); tid = ud.get("telegram_id")
    if not tid: await websocket.close(code=4001); return None
    r = await db.execute(select(User).where(User.telegram_id == tid))
    u = r.scalar_one_or_none()
    if not u:
        u = User(telegram_id=tid, username=ud.get("username"), first_name=ud.get("first_name"))
        db.add(u); await db.flush()
    return u

async def get_admin(u: User = Depends(get_current_user)) -> User:
    if not u.is_admin: raise HTTPException(403, "Admin only")
    return u

# Rate limiter
class RateLimiter:
    def __init__(self):
        self._reqs: Dict[str, list] = {}
        self._limits = {"case_open":(5,60),"crash_bet":(10,60),"arena_join":(5,60),"upgrade":(10,60),"withdraw":(3,3600),"chat":(30,60),"deposit":(10,3600),"default":(60,60)}
        self._last = time.time()
    def check(self, uid, action):
        if time.time()-self._last>300: self._clean(); self._last=time.time()
        k=f"{uid}:{action}"; mx,w=self._limits.get(action,self._limits["default"])
        self._reqs[k]=[t for t in self._reqs.get(k,[]) if time.time()-t<w]
        if len(self._reqs[k])>=mx: return True
        self._reqs[k].append(time.time()); return False
    def _clean(self):
        for k in list(self._reqs.keys()):
            a=k.split(":",1)[1] if ":" in k else "default"; w=self._limits.get(a,self._limits["default"])[1]
            self._reqs[k]=[t for t in self._reqs[k] if time.time()-t<w]
            if not self._reqs[k]: del self._reqs[k]
rate_limiter = RateLimiter()

# ======================== APP ========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    await seed_db(); await recover_crash_bets()
    cleanup_task = asyncio.create_task(cleanup_idempotency())
    crash_task = asyncio.create_task(crash_loop())
    arena_cleanup = asyncio.create_task(cleanup_arenas())
    yield
    cleanup_task.cancel(); crash_task.cancel(); arena_cleanup.cancel()

app = FastAPI(title="CaseFight", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.CORS_ORIGINS], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ======================== BACKGROUND TASKS ========================
async def cleanup_idempotency():
    while True:
        await asyncio.sleep(3600)
        async with async_session() as db:
            await db.execute(delete(RequestIdempotency).where(RequestIdempotency.expires_at < datetime.now(timezone.utc)))
            await db.commit()

async def cleanup_arenas():
    while True:
        await asyncio.sleep(300)
        async with async_session() as db:
            await db.execute(delete(ArenaGame).where(and_(ArenaGame.status=="waiting", ArenaGame.created_at < datetime.now(timezone.utc)-timedelta(minutes=10))))
            await db.commit()

async def recover_crash_bets():
    async with async_session() as db:
        for bet in (await db.execute(select(CrashBet).where(CrashBet.status=="active"))).scalars().all():
            bet.status="cancelled"; bet.profit=Decimal("0")
            u=(await db.execute(select(User).where(User.id==bet.user_id))).scalar_one_or_none()
            if u: u.balance+=bet.amount
        await db.commit()

# ======================== SEED ========================
async def seed_db():
    async with async_session() as db:
        if (await db.execute(select(Case).limit(1))).scalar_one_or_none(): return
        cases_data = [
            ("🌟 Starter Stars","Начальный кейс",10,"stars",0,[("Деревянный Меч",5,0.25,"common"),("Кожаный Щит",6,0.20,"common"),("Железный Кинжал",8,0.18,"common"),("Кольцо Силы",10,0.15,"uncommon"),("Амулет Защиты",12,0.10,"uncommon"),("Стальной Шлем",15,0.07,"rare"),("Золотой Браслет",20,0.04,"epic"),("Меч Новичка",30,0.01,"legendary")]),
            ("🥉 Bronze Stars","Бронзовый кейс",50,"stars",5,[("Бронзовый Меч",25,0.22,"common"),("Бронзовый Щит",28,0.20,"common"),("Кольцо Ловкости",35,0.18,"uncommon"),("Плащ Теней",45,0.15,"uncommon"),("Серебряный Амулет",60,0.12,"rare"),("Топор Гномов",75,0.08,"rare"),("Корона Воина",100,0.04,"epic"),("Бронзовый Дракон",150,0.01,"legendary")]),
            ("🥈 Silver Stars","Серебряный кейс",200,"stars",10,[("Серебряный Меч",100,0.20,"common"),("Серебряный Щит",110,0.18,"common"),("Кольцо Магии",140,0.16,"uncommon"),("Эльфийский Лук",180,0.15,"uncommon"),("Рунический Посох",240,0.12,"rare"),("Мифриловая Кольчуга",300,0.10,"rare"),("Сапфировая Тиара",400,0.07,"epic"),("Серебряный Феникс",600,0.02,"legendary")]),
            ("🥇 Gold Stars","Золотой кейс",500,"stars",15,[("Золотой Меч",250,0.18,"common"),("Золотой Щит",280,0.17,"common"),("Кольцо Власти",350,0.16,"uncommon"),("Посох Архимага",450,0.14,"uncommon"),("Доспехи Паладина",600,0.13,"rare"),("Клинок Титана",750,0.10,"rare"),("Корона Короля",1000,0.08,"epic"),("Золотой Дракон",1500,0.04,"legendary")]),
            ("🎁 NFT Starter","NFT стартовый",100,"nft",5,[("Delicious Coffee",50,0.22,"common"),("Lucky Cat",55,0.20,"common"),("Magic Potion",70,0.18,"uncommon"),("Crystal Ball",85,0.15,"uncommon"),("Golden Key",110,0.12,"rare"),("Diamond Ring",140,0.08,"rare"),("Phoenix Feather",200,0.04,"epic"),("Dragon Egg",300,0.01,"legendary")]),
            ("💎 NFT Rare","NFT редкий",300,"nft",8,[("Star Fragment",150,0.20,"common"),("Moon Crystal",170,0.18,"common"),("Thunder Bolt",210,0.16,"uncommon"),("Ice Crown",260,0.15,"uncommon"),("Shadow Amulet",330,0.12,"rare"),("Ruby Heart",400,0.10,"rare"),("Emerald Crown",550,0.07,"epic"),("Celestial Sword",800,0.02,"legendary")]),
            ("🔥 NFT Epic","NFT эпический",800,"nft",12,[("Flame Sword",400,0.18,"common"),("Aqua Trident",450,0.17,"common"),("Storm Hammer",550,0.16,"uncommon"),("Earth Shield",650,0.15,"uncommon"),("Void Staff",800,0.13,"rare"),("Light Bow",950,0.10,"rare"),("Chaos Blade",1300,0.08,"epic"),("Eternal Crown",2000,0.03,"legendary")]),
            ("👑 NFT Legendary","NFT легендарный",2000,"nft",20,[("Cosmic Ring",1000,0.17,"common"),("Time Pendant",1150,0.16,"common"),("Soul Gem",1400,0.15,"uncommon"),("Astral Cape",1700,0.14,"uncommon"),("Void Armor",2100,0.13,"rare"),("Infinity Gauntlet",2600,0.11,"rare"),("Excalibur",3500,0.09,"epic"),("Omnipotent Orb",5000,0.05,"legendary")]),
        ]
        for name,desc,price,ctype,cd,items in cases_data:
            total_ch=sum(ch for _,_,ch,_ in items)
            if abs(total_ch-1.0)>0.001: items=[(n,v,round(ch/total_ch,4),r) for n,v,ch,r in items]
            ev=sum(v*ch for _,v,ch,_ in items); rtp=(ev/price)*100
            if rtp>98:
                adj=95.0/rtp; items=[(n,v,round(ch*adj,4),r) for n,v,ch,r in items]
                total_ch=sum(ch for _,_,ch,_ in items); items=[(n,v,round(ch/total_ch,4),r) for n,v,ch,r in items]
            c=Case(name=name,description=desc,price=Decimal(price),type=ctype,cooldown_seconds=cd); db.add(c); await db.flush()
            for iname,val,ch,rar in items: db.add(CaseItem(case_id=c.id,name=iname,value=Decimal(val),drop_chance=Decimal(str(ch)),rarity=rar))
        await db.commit(); print(f"✅ Seeded {len(cases_data)} cases")

# ======================== CRASH ========================
current_crash_game=None; current_multiplier=Decimal("1.00"); crash_lock=asyncio.Lock()

async def crash_loop():
    global current_crash_game, current_multiplier
    while True:
        async with crash_lock:
            async with async_session() as db:
                seed=secrets.token_hex(32); seed_int=int(hashlib.sha256(seed.encode()).hexdigest()[:16],16)
                rng=random.Random(seed_int); rand_val=rng.random()
                crash_pt=Decimal("1.00") if rand_val<0.01 else Decimal(str(round(max((0.99/(1.0-rand_val))*(1.0-settings.CRASH_HOUSE_EDGE),1.01),2)))
                game=CrashGame(crash_point=crash_pt,seed_hash=hashlib.sha256(seed.encode()).hexdigest(),status="active")
                db.add(game); await db.commit()
                current_crash_game=game; current_multiplier=Decimal("1.00")
                start=time.monotonic(); dur=min(15.0,max(3.0,float(crash_pt)*0.3))
                while current_multiplier<crash_pt:
                    p=min((time.monotonic()-start)/dur,1.0)
                    current_multiplier=(Decimal("1.00")+(crash_pt-Decimal("1.00"))*Decimal(str(p))).quantize(Decimal("0.01"))
                    await asyncio.sleep(0.1)
                    bets=(await db.execute(select(CrashBet).where(and_(CrashBet.game_id==game.id,CrashBet.status=="active",CrashBet.auto_cashout.isnot(None),CrashBet.auto_cashout<=current_multiplier)))).scalars().all()
                    for b in bets:
                        b.cashout_multiplier=current_multiplier; b.profit=(b.amount*current_multiplier)-b.amount; b.status="cashed_out"
                        u=(await db.execute(select(User).where(User.id==b.user_id))).scalar_one(); u.balance+=b.amount+b.profit
                    if bets: await db.commit()
                for b in (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==game.id,CrashBet.status=="active")))).scalars().all(): b.status="lost"; b.profit=-b.amount
                game.status="finished"; await db.commit()
                current_crash_game=None; current_multiplier=Decimal("1.00")
        await asyncio.sleep(5)

# ======================== API ROUTES ========================
@app.get("/health")
async def health(): return {"status":"ok","app":"CaseFight"}

@app.get("/auth/me", response_model=UserOut)
async def me(u: User=Depends(get_current_user)): return u

@app.get("/auth/profile", response_model=ProfileOut)
async def profile(u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    inv=(await db.execute(select(func.count(UserInventory.id)).where(UserInventory.user_id==u.id))).scalar() or 0
    ops=(await db.execute(select(func.count(CaseOpenHistory.id)).where(CaseOpenHistory.user_id==u.id))).scalar() or 0
    return ProfileOut(user=UserOut.model_validate(u), inventory_count=inv, total_case_opens=ops)

@app.get("/auth/history/cases")
async def case_history(limit:int=20,offset:int=0,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    r=await db.execute(select(CaseOpenHistory).where(CaseOpenHistory.user_id==u.id).order_by(CaseOpenHistory.opened_at.desc()).offset(offset).limit(limit))
    return [{"id":h.id,"case_name":h.case_name,"item_name":h.item_name,"item_value":str(h.item_value),"item_rarity":h.item_rarity,"opened_at":h.opened_at.isoformat() if h.opened_at else None} for h in r.scalars().all()]

@app.get("/auth/history/deposits")
async def deposit_history(limit:int=20,offset:int=0,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    r=await db.execute(select(DepositTransaction).where(DepositTransaction.user_id==u.id).order_by(DepositTransaction.created_at.desc()).offset(offset).limit(limit))
    return [{"id":d.id,"amount_stars":d.amount_stars,"amount_received":str(d.amount_received),"fee":str(d.fee),"created_at":d.created_at.isoformat() if d.created_at else None} for d in r.scalars().all()]

@app.get("/auth/history/withdrawals")
async def withdraw_history(limit:int=20,offset:int=0,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    r=await db.execute(select(WithdrawRequest).where(WithdrawRequest.user_id==u.id).order_by(WithdrawRequest.created_at.desc()).offset(offset).limit(limit))
    return [{"id":w.id,"amount":str(w.amount),"fee":str(w.fee),"amount_after_fee":str(w.amount_after_fee),"status":w.status,"created_at":w.created_at.isoformat() if w.created_at else None} for w in r.scalars().all()]

@app.get("/user/balance")
async def balance(u: User=Depends(get_current_user)): return {"balance":str(u.balance),"total_deposited":str(u.total_deposited),"total_withdrawn":str(u.total_withdrawn)}

@app.get("/cases/", response_model=List[CaseOut])
async def get_cases(db: AsyncSession=Depends(get_db)):
    cases=(await db.execute(select(Case).where(Case.is_active==True).order_by(Case.price))).scalars().all()
    if not cases: return []
    case_ids=[c.id for c in cases]; all_items=(await db.execute(select(CaseItem).where(CaseItem.case_id.in_(case_ids)))).scalars().all()
    items_map={c.id:[] for c in cases}
    for i in all_items: items_map[i.case_id].append(ItemOut.model_validate(i))
    return [CaseOut(id=c.id,name=c.name,description=c.description,price=c.price,image_url=c.image_url,type=c.type,is_active=c.is_active,cooldown_seconds=c.cooldown_seconds,items=items_map[c.id]) for c in cases]

@app.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id:int,db:AsyncSession=Depends(get_db)):
    c=(await db.execute(select(Case).where(Case.id==case_id,Case.is_active==True))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    items=(await db.execute(select(CaseItem).where(CaseItem.case_id==case_id))).scalars().all()
    return CaseOut(id=c.id,name=c.name,description=c.description,price=c.price,image_url=c.image_url,type=c.type,is_active=c.is_active,cooldown_seconds=c.cooldown_seconds,items=[ItemOut.model_validate(i) for i in items])

@app.post("/cases/open", response_model=OpenCaseResp)
async def open_case(req:OpenCaseReq,db:AsyncSession=Depends(get_db),u:User=Depends(get_current_user)):
    if rate_limiter.check(u.id,"case_open"): raise HTTPException(429)
    ex=(await db.execute(select(RequestIdempotency).where(RequestIdempotency.idempotency_key==req.idempotency_key))).scalar_one_or_none()
    if ex: raise HTTPException(409,"Already processed")
    db.add(RequestIdempotency(idempotency_key=req.idempotency_key,user_id=u.id,expires_at=datetime.now(timezone.utc)+timedelta(hours=1)))
    await db.flush()
    c=(await db.execute(select(Case).where(Case.id==req.case_id,Case.is_active==True))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    if c.cooldown_seconds>0 and not u.case_cooldown_removed and u.case_cooldown_until and datetime.now(timezone.utc)<u.case_cooldown_until: raise HTTPException(429,"Cooldown")
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one()
    if u2.balance<c.price: raise HTTPException(400,"No balance")
    u2.balance-=c.price
    items=(await db.execute(select(CaseItem).where(CaseItem.case_id==c.id))).scalars().all()
    total=sum(float(i.drop_chance) for i in items); rand=random.uniform(0,total); cum=0.0; sel=items[-1]
    for i in items:
        cum+=float(i.drop_chance)
        if rand<=cum: sel=i; break
    db.add(UserInventory(user_id=u.id,case_item_id=sel.id,case_id=c.id,case_name=c.name,item_name=sel.name,item_image_url=sel.image_url,item_value=sel.value,item_rarity=sel.rarity))
    db.add(CaseOpenHistory(user_id=u.id,case_id=c.id,case_name=c.name,item_id=sel.id,item_name=sel.name,item_value=sel.value,item_rarity=sel.rarity))
    if c.cooldown_seconds>0 and not u2.case_cooldown_removed: u2.case_cooldown_until=datetime.now(timezone.utc)+timedelta(seconds=c.cooldown_seconds)
    await db.flush()
    return OpenCaseResp(success=True,item=ItemOut.model_validate(sel),balance_after=u2.balance)

@app.get("/inventory/", response_model=List[InventoryOut])
async def get_inventory(rarity:str=Query(None),sort_by:str=Query("obtained_at"),sort_order:str=Query("desc"),limit:int=50,offset:int=0,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    q=select(UserInventory).where(UserInventory.user_id==u.id)
    if rarity: q=q.where(UserInventory.item_rarity==rarity)
    col={"obtained_at":UserInventory.obtained_at,"value":UserInventory.item_value}.get(sort_by,UserInventory.obtained_at)
    q=q.order_by(col.desc() if sort_order=="desc" else col.asc()).offset(offset).limit(limit)
    return [InventoryOut.model_validate(i) for i in (await db.execute(q)).scalars().all()]

@app.get("/inventory/count")
async def inv_count(u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    r=await db.execute(select(UserInventory.item_rarity,func.count(UserInventory.id),func.sum(UserInventory.item_value)).where(UserInventory.user_id==u.id).group_by(UserInventory.item_rarity))
    counts={}; ti=0; tv=Decimal("0")
    for rarity,cnt,val in r: counts[rarity or "unknown"]={"count":cnt,"total_value":str(val or 0)}; ti+=cnt; tv+=Decimal(str(val or 0))
    return {"total_items":ti,"total_value":str(tv),"by_rarity":counts}

@app.post("/inventory/sell", response_model=SellResp)
async def sell_item(req:SellReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    item=(await db.execute(select(UserInventory).where(and_(UserInventory.id==req.item_id,UserInventory.user_id==u.id)))).scalar_one_or_none()
    if not item: raise HTTPException(404)
    price=(Decimal(str(item.item_value or 0))*Decimal("0.8")).quantize(Decimal("0.01"))
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one(); u2.balance+=price
    await db.delete(item); await db.flush()
    return SellResp(success=True,sold_for=price,balance_after=u2.balance)

@app.post("/stars/deposit", response_model=StarsDepositResp)
async def stars_deposit(req:StarsDepositReq,u:User=Depends(get_current_user)):
    if rate_limiter.check(u.id,"deposit"): raise HTTPException(429)
    if req.amount<settings.MIN_DEPOSIT_STARS: raise HTTPException(400,f"Min {settings.MIN_DEPOSIT_STARS}")
    gross=Decimal(req.amount)*Decimal(str(settings.STARS_RATE)); fee=(gross*Decimal(str(settings.DEPOSIT_FEE_PERCENT))/100).quantize(Decimal("0.01"))
    received=gross-fee
    payload=f"stars_{u.id}_{int(time.time())}_{secrets.token_hex(4)}"
    invoice_link=f"https://t.me/{settings.BOT_USERNAME}?start=pay_{payload}"
    return StarsDepositResp(invoice_link=invoice_link,amount_stars=req.amount,amount_received=received,fee=fee)

@app.post("/stars/deposit/confirm")
async def confirm_deposit(data:dict,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    pid=data.get("telegram_payment_id"); amt=data.get("amount_stars")
    if not pid or not amt: raise HTTPException(400,"Invalid data")
    ex=(await db.execute(select(DepositTransaction).where(DepositTransaction.telegram_payment_id==pid))).scalar_one_or_none()
    if ex: raise HTTPException(409,"Already processed")
    gross=Decimal(str(amt))*Decimal(str(settings.STARS_RATE)); fee=(gross*Decimal(str(settings.DEPOSIT_FEE_PERCENT))/100).quantize(Decimal("0.01")); received=gross-fee
    db.add(DepositTransaction(user_id=u.id,telegram_payment_id=pid,amount_stars=int(amt),amount_received=received,fee=fee,verified=True))
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one(); u2.balance+=received; u2.total_deposited+=received
    await db.flush()
    return {"success":True,"balance":str(u2.balance),"amount_received":str(received)}

@app.get("/stars/rate")
async def stars_rate(): return {"rate":settings.STARS_RATE,"min_deposit":settings.MIN_DEPOSIT_STARS,"fee_percent":settings.DEPOSIT_FEE_PERCENT,"min_withdraw":settings.MIN_WITHDRAW,"withdraw_fee":settings.WITHDRAW_FEE_PERCENT}

@app.post("/withdraw/", response_model=WithdrawOut)
async def create_withdraw(req:WithdrawReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if rate_limiter.check(u.id,"withdraw"): raise HTTPException(429)
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one()
    if req.amount<settings.MIN_WITHDRAW or req.amount>u2.balance: raise HTTPException(400,"Invalid amount")
    fee=(req.amount*Decimal(str(settings.WITHDRAW_FEE_PERCENT))/100).quantize(Decimal("0.01")); after=req.amount-fee
    u2.balance-=req.amount
    wr=WithdrawRequest(user_id=u.id,amount=req.amount,fee=fee,amount_after_fee=after,status="pending")
    db.add(wr); await db.flush()
    return WithdrawOut.model_validate(wr)

@app.get("/withdraw/pending")
async def pending_withdraws(u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    r=await db.execute(select(WithdrawRequest).where(and_(WithdrawRequest.user_id==u.id,WithdrawRequest.status=="pending")))
    return [WithdrawOut.model_validate(w) for w in r.scalars().all()]

@app.get("/crash/current")
async def crash_current():
    if current_crash_game: return {"game_id":current_crash_game.id,"multiplier":str(current_multiplier),"status":"running"}
    return {"status":"waiting","next":5}

@app.post("/crash/bet")
async def crash_bet(req:CrashBetReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if rate_limiter.check(u.id,"crash_bet"): raise HTTPException(429)
    if not current_crash_game: raise HTTPException(400,"No game")
    if not u.can_odd_bets and req.amount%1!=0: raise HTTPException(400,"Odd bets disabled")
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one()
    if u2.balance<req.amount: raise HTTPException(400,"No balance")
    u2.balance-=req.amount
    db.add(CrashBet(game_id=current_crash_game.id,user_id=u.id,amount=req.amount,auto_cashout=req.auto_cashout,status="active"))
    await db.flush()
    return {"success":True,"amount":str(req.amount),"balance":str(u2.balance)}

@app.post("/crash/cashout")
async def crash_cashout(req:CrashCashoutReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if not current_crash_game: raise HTTPException(400,"No game")
    async with crash_lock:
        bet=(await db.execute(select(CrashBet).where(and_(CrashBet.user_id==u.id,CrashBet.game_id==current_crash_game.id,CrashBet.status=="active")))).scalar_one_or_none()
        if not bet: raise HTTPException(404,"No bet")
        if current_multiplier>current_crash_game.crash_point: raise HTTPException(400,"Crashed")
        profit=(bet.amount*current_multiplier)-bet.amount; bet.cashout_multiplier=current_multiplier; bet.profit=profit; bet.status="cashed_out"
        u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one(); u2.balance+=bet.amount+profit
        await db.flush()
    return {"success":True,"multiplier":str(current_multiplier),"profit":str(profit),"balance":str(u2.balance)}

@app.get("/crash/history")
async def crash_history(limit:int=10,db:AsyncSession=Depends(get_db)):
    games=(await db.execute(select(CrashGame).order_by(CrashGame.created_at.desc()).limit(limit))).scalars().all()
    return [{"id":g.id,"crash_point":str(g.crash_point),"created_at":g.created_at.isoformat() if g.created_at else None} for g in games]

@app.get("/arena/games")
async def arena_games(db:AsyncSession=Depends(get_db)):
    games=(await db.execute(select(ArenaGame).where(ArenaGame.status.in_(["waiting","in_progress"])))).scalars().all()
    res=[]
    for g in games:
        players=(await db.execute(select(ArenaPlayer,User.username,User.first_name).join(User).where(ArenaPlayer.game_id==g.id))).all()
        pl=[{"user_id":p.user_id,"username":un,"first_name":fn,"bet_amount":str(p.bet_amount)} for p,un,fn in players]
        res.append({"id":g.id,"creator_id":g.creator_id,"status":g.status,"total_pot":str(g.total_pot),"players_count":len(pl),"max_players":settings.ARENA_MAX_PLAYERS,"players":pl})
    return res

@app.post("/arena/create")
async def arena_create(u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if rate_limiter.check(u.id,"arena_join"): raise HTTPException(429)
    active=(await db.execute(select(func.count(ArenaGame.id)).where(and_(ArenaGame.creator_id==u.id,ArenaGame.status=="waiting")))).scalar()
    if active>=3: raise HTTPException(400,"Max 3 active arenas")
    g=ArenaGame(creator_id=u.id,status="waiting"); db.add(g); await db.flush()
    return {"game_id":g.id,"status":g.status,"max_players":settings.ARENA_MAX_PLAYERS}

@app.post("/arena/join/{game_id}")
async def arena_join(game_id:int,req:ArenaJoinReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if rate_limiter.check(u.id,"arena_join"): raise HTTPException(429)
    g=(await db.execute(select(ArenaGame).where(ArenaGame.id==game_id))).scalar_one_or_none()
    if not g or g.status!="waiting": raise HTTPException(400)
    ex=(await db.execute(select(ArenaPlayer).where(and_(ArenaPlayer.game_id==game_id,ArenaPlayer.user_id==u.id)))).scalar_one_or_none()
    if ex: raise HTTPException(400,"Already joined")
    cnt=(await db.execute(select(func.count(ArenaPlayer.id)).where(ArenaPlayer.game_id==game_id))).scalar()
    if cnt>=settings.ARENA_MAX_PLAYERS: raise HTTPException(400,"Full")
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one()
    if u2.balance<req.bet_amount: raise HTTPException(400,"No balance")
    u2.balance-=req.bet_amount; g.total_pot+=req.bet_amount
    db.add(ArenaPlayer(game_id=g.id,user_id=u.id,bet_amount=req.bet_amount)); await db.flush()
    return {"success":True,"balance":str(u2.balance)}

@app.post("/arena/start/{game_id}")
async def arena_start(game_id:int,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    g=(await db.execute(select(ArenaGame).where(ArenaGame.id==game_id))).scalar_one_or_none()
    if not g: raise HTTPException(404)
    if g.creator_id!=u.id and not u.is_admin: raise HTTPException(403,"Not creator")
    players=(await db.execute(select(ArenaPlayer).where(ArenaPlayer.game_id==game_id))).scalars().all()
    if len(players)<2: raise HTTPException(400,"Need 2+")
    tot=g.total_pot
    for p in players: p.win_chance=(p.bet_amount/tot).quantize(Decimal("0.0001"))
    g.status="in_progress"
    rand_val=random.random(); cum=Decimal("0"); winner=players[-1]
    for p in players:
        cum+=p.win_chance
        if Decimal(str(rand_val))<=cum: winner=p; break
    pf=(tot*Decimal(str(settings.ARENA_PLATFORM_FEE))/100).quantize(Decimal("0.01")); prize=tot-pf
    g.platform_fee=pf; g.winner_id=winner.user_id; g.status="completed"; winner.result="win"
    for p in players:
        if p.id!=winner.id: p.result="lose"
    wu=(await db.execute(select(User).where(User.id==winner.user_id))).scalar_one(); wu.balance+=prize
    await db.flush()
    return {"success":True,"winner_id":wu.id,"prize":str(prize)}

@app.post("/upgrade/", response_model=UpgradeResp)
async def upgrade(req:UpgradeReq,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    if rate_limiter.check(u.id,"upgrade"): raise HTTPException(429)
    frm=(await db.execute(select(UserInventory).where(and_(UserInventory.id==req.item_id,UserInventory.user_id==u.id)))).scalar_one_or_none()
    to=(await db.execute(select(UserInventory).where(and_(UserInventory.id==req.target_item_id,UserInventory.user_id==u.id)))).scalar_one_or_none()
    if not frm or not to: raise HTTPException(404)
    if to.item_value<=frm.item_value: raise HTTPException(400,"Target must be more expensive")
    fv=Decimal(str(frm.item_value)); tv=Decimal(str(to.item_value))
    chance=min(max(fv/tv,Decimal("0.01")),Decimal("0.99")); success=random.random()<float(chance)
    await db.delete(frm)
    if success:
        new_val=(tv*Decimal("1.05")).quantize(Decimal("0.01")); to.item_value=new_val; to.is_upgraded=True
        db.add(UpgradeHistory(user_id=u.id,item_from_id=frm.id,item_to_name=to.item_name,item_to_value=to.item_value,success=True))
        await db.flush(); return UpgradeResp(success=True,message=f"Upgraded! New value: {new_val}",new_value=new_val)
    else:
        await db.delete(to)
        db.add(UpgradeHistory(user_id=u.id,item_from_id=frm.id,item_to_name=to.item_name,item_to_value=tv,success=False))
        await db.flush(); return UpgradeResp(success=False,message=f"Failed! Both destroyed. Chance was {float(chance)*100:.1f}%")

# Shop
SHOP_ITEMS=[{"type":"cooldown_remove","name":"🔥 Снятие кулдауна","price":Decimal("500")},{"type":"odd_bets","name":"🎲 Нечётные ставки","price":Decimal("250")}]
DIRECT_ITEMS=[{"type":"direct_1","name":"🌟 Золотой Меч","price":Decimal("1000"),"rarity":"legendary"},{"type":"direct_2","name":"💎 Алмазный Щит","price":Decimal("500"),"rarity":"epic"}]

@app.get("/shop/")
async def shop(): return {"upgrades":[{"type":i["type"],"name":i["name"],"price":str(i["price"])} for i in SHOP_ITEMS],"direct_items":[{"type":i["type"],"name":i["name"],"price":str(i["price"]),"rarity":i["rarity"]} for i in DIRECT_ITEMS]}

@app.post("/shop/buy/{item_type}")
async def shop_buy(item_type:str,u:User=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    item=next((i for i in SHOP_ITEMS if i["type"]==item_type),None)
    ditem=next((i for i in DIRECT_ITEMS if i["type"]==item_type),None)
    u2=(await db.execute(select(User).where(User.id==u.id).with_for_update())).scalar_one()
    if item:
        if u2.balance<item["price"]: raise HTTPException(400,"No balance")
        u2.balance-=item["price"]
        if item_type=="cooldown_remove":
            if u2.case_cooldown_removed: raise HTTPException(400,"Already")
            u2.case_cooldown_removed=True
        elif item_type=="odd_bets":
            if u2.can_odd_bets: raise HTTPException(400,"Already")
            u2.can_odd_bets=True
    elif ditem:
        if u2.balance<ditem["price"]: raise HTTPException(400,"No balance")
        u2.balance-=ditem["price"]
        db.add(UserInventory(user_id=u.id,item_name=ditem["name"],item_value=ditem["price"],item_rarity=ditem["rarity"]))
    else: raise HTTPException(404)
    await db.flush(); return {"success":True,"balance":str(u2.balance)}

# WebSocket
class WsManager:
    def __init__(self): self.connections:Dict[int,WebSocket]={}; self.online:Set[int]=set(); self.spam:Dict[int,list]={}
    async def connect(self,ws:WebSocket,user:User): await ws.accept(); self.connections[user.id]=ws; self.online.add(user.id); await self.broadcast_online()
    def disconnect(self,uid:int): self.connections.pop(uid,None); self.online.discard(uid); self.spam.pop(uid,None)
    async def broadcast(self,msg:dict):
        dead=[]
        for uid,ws in self.connections.items():
            try: await ws.send_text(json.dumps(msg,default=str))
            except: dead.append(uid)
        for d in dead: self.disconnect(d)
    async def broadcast_online(self): await self.broadcast({"type":"online","count":len(self.online)})
    def check_spam(self,uid:int)->bool:
        now=time.time(); self.spam[uid]=[t for t in self.spam.get(uid,[]) if now-t<5]
        if len(self.spam[uid])>=3: return True
        self.spam[uid].append(now); return False
ws_manager=WsManager()

@app.websocket("/ws/chat")
async def ws_chat(websocket:WebSocket):
    async with async_session() as db:
        user=await get_ws_user(websocket,db)
        if not user: return
    await ws_manager.connect(websocket,user)
    try:
        while True:
            data=json.loads(await websocket.receive_text())
            if data.get("type")=="chat":
                msg=data.get("message","").strip()
                if not msg or len(msg)>300: continue
                if ws_manager.check_spam(user.id):
                    await websocket.send_text(json.dumps({"type":"error","message":"Slow down!"})); continue
                async with async_session() as db:
                    cm=ChatMessage(user_id=user.id,username=user.username or f"user_{user.id}",message=msg)
                    db.add(cm); await db.commit()
                    await ws_manager.broadcast({"type":"chat","id":cm.id,"user_id":user.id,"username":cm.username,"message":msg,"created_at":cm.created_at.isoformat() if cm.created_at else None})
    except: pass
    finally: ws_manager.disconnect(user.id); await ws_manager.broadcast_online()

@app.get("/ws/online")
async def online(): return {"online":len(ws_manager.online)}

@app.get("/ws/chat/history")
async def chat_hist(limit:int=50):
    async with async_session() as db:
        msgs=(await db.execute(select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit))).scalars().all()
        return [{"id":m.id,"user_id":m.user_id,"username":m.username,"message":m.message,"created_at":m.created_at.isoformat() if m.created_at else None} for m in reversed(msgs)]

# Admin
@app.get("/admin/users")
async def admin_users(search:str=None,db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    q=select(User)
    if search: q=q.where(or_(User.username.ilike(f"%{search}%"),User.first_name.ilike(f"%{search}%"),User.telegram_id.cast(str).ilike(f"%{search}%")))
    return [UserOut.model_validate(u) for u in (await db.execute(q.order_by(User.registered_at.desc()).limit(100))).scalars().all()]

@app.post("/admin/balance")
async def admin_balance(req:AdminBalanceReq,db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    u=(await db.execute(select(User).where(User.id==req.user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404)
    amt=Decimal(str(req.amount))
    if req.operation=="add": u.balance+=amt
    elif req.operation=="subtract": u.balance=max(u.balance-amt,Decimal("0"))
    elif req.operation=="set": u.balance=amt
    db.add(ActionLog(user_id=admin.id,action_type="balance",description=f"User {u.id}: {req.operation} {amt}")); await db.flush()
    return {"success":True,"new_balance":str(u.balance)}

@app.post("/admin/block")
async def admin_block(req:AdminBlockReq,db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    u=(await db.execute(select(User).where(User.id==req.user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404)
    u.is_blocked=req.block
    db.add(ActionLog(user_id=admin.id,action_type="block" if req.block else "unblock",description=f"User {u.id} {'blocked' if req.block else 'unblocked'}")); await db.flush()
    return {"success":True}

@app.post("/admin/give-item")
async def admin_give(req:AdminGiveItemReq,db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    u=(await db.execute(select(User).where(User.id==req.user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404)
    db.add(UserInventory(user_id=u.id,item_name=req.item_name,item_value=req.item_value,item_rarity=req.item_rarity))
    db.add(ActionLog(user_id=admin.id,action_type="give_item",description=f"Gave {req.item_name} to user {u.id}")); await db.flush()
    return {"success":True}

@app.get("/admin/withdrawals")
async def admin_withdrawals(db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    reqs=(await db.execute(select(WithdrawRequest).where(WithdrawRequest.status=="pending").order_by(WithdrawRequest.created_at.desc()))).scalars().all()
    return [{"id":r.id,"user_id":r.user_id,"amount":str(r.amount),"fee":str(r.fee),"amount_after_fee":str(r.amount_after_fee),"status":r.status,"created_at":r.created_at.isoformat() if r.created_at else None} for r in reqs]

@app.post("/admin/withdrawals/process")
async def admin_process_withdraw(req:AdminWithdrawReq,db:AsyncSession=Depends(get_db),admin:User=Depends(get_admin)):
    wr=(await db.execute(select(WithdrawRequest).where(WithdrawRequest.id==req.request_id))).scalar_one_or_none()
    if not wr or wr.status!="pending": raise HTTPException(400)
    u=(await db.execute(select(User).where(User.id==wr.user_id))).scalar_one()
    if req.action=="approve": wr.status="approved"; u.total_withdrawn+=wr.amount_after_fee
    else: wr.status="rejected"; u.balance+=wr.amount
    wr.admin_id=admin.id; wr.admin_comment=req.comment; wr.processed_at=datetime.now(timezone.utc)
    db.add(ActionLog(user_id=admin.id,action_type="withdraw_"+req.action,description=f"Withdraw #{wr.id} {req.action}")); await db.flush()
    return {"success":True}

# ======================== FRONTEND ========================
@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no"><title>CaseFight</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
body{background:#0a0e14;color:#e6e8ec;min-height:100vh;display:flex;flex-direction:column;padding-bottom:56px}
header{background:#12161e;padding:8px 12px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;border-bottom:1px solid rgba(255,255,255,0.06)}
.logo{font-size:15px;font-weight:800;cursor:pointer}.logo span{color:#f5a623}
.balance{background:#1a1f2b;padding:5px 10px;border-radius:14px;font-size:12px;font-weight:700;cursor:pointer}
main{padding:10px;flex:1;overflow-y:auto}
.menu-list{display:flex;flex-direction:column;gap:6px}
.menu-item{background:#1e2430;border-radius:10px;padding:14px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;border:1px solid rgba(255,255,255,0.04)}
.menu-item:active{background:#252c38}.menu-name{font-weight:600;font-size:14px}
.case-list{display:flex;flex-direction:column;gap:6px}
.case-card{background:#1e2430;border-radius:10px;padding:12px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;border:1px solid rgba(255,255,255,0.04)}
.case-card:active{background:#252c38}.case-name{font-weight:600;font-size:13px}.case-price{color:#f5a623;font-weight:700;font-size:13px}
.btn{background:linear-gradient(135deg,#f5a623,#f7c948);color:#000;border:none;padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;width:100%;margin-top:8px}
.btn:active{transform:scale(0.96)}.btn-sm{padding:6px 12px;font-size:11px;width:auto}.btn-red{background:#f44336}
input,select{width:100%;background:#1a1f2b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px;color:#e6e8ec;font-size:13px;margin-bottom:6px}
.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:#12161e;display:flex;justify-content:space-around;padding:6px 4px 8px;border-top:1px solid rgba(255,255,255,0.06);z-index:100}
.nav-btn{background:none;border:none;color:#5c6370;font-size:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px}
.nav-btn.active{color:#f5a623}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:200;display:flex;align-items:center;justify-content:center}
.modal-content{background:#12161e;border-radius:14px;padding:20px;width:90%;max-width:360px;max-height:80vh;overflow-y:auto}
.toast{position:fixed;top:60px;right:10px;background:#1e2430;padding:10px 14px;border-radius:8px;font-size:12px;font-weight:600;z-index:300;border-left:3px solid #f5a623;animation:slideIn 0.3s}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
h2{font-size:15px;margin-bottom:8px}h3{font-size:14px;margin-bottom:8px}
.back-btn{background:none;border:none;color:#f5a623;font-size:12px;cursor:pointer;margin-bottom:8px;padding:0}
#loading{position:fixed;top:0;left:0;width:100%;height:100%;background:#0a0e14;display:flex;align-items:center;justify-content:center;z-index:999;flex-direction:column}
.spinner{width:32px;height:32px;border:3px solid #1a1f2b;border-top-color:#f5a623;border-radius:50%;animation:spin 0.7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<div id="loading"><div class="spinner"></div><p style="margin-top:10px;color:#8b92a0;font-size:13px">Загрузка...</p></div>
<div id="toasts"></div>
<div id="app" style="display:none">
<header><div class="logo" onclick="nav('home')">⚔️ <span>CaseFight</span></div><div class="balance" onclick="nav('deposit')">⭐ <span id="bal">0</span></div></header>
<main id="content"></main>
<nav class="bottom-nav">
<button class="nav-btn active" data-p="home">🏠<span>Главная</span></button>
<button class="nav-btn" data-p="cases">📦<span>Кейсы</span></button>
<button class="nav-btn" data-p="crash">🐸<span>Crash</span></button>
<button class="nav-btn" data-p="arena">⚔️<span>Арена</span></button>
<button class="nav-btn" data-p="inv">🎒<span>Инвентарь</span></button>
</nav></div>
<div id="modal" class="modal" style="display:none" onclick="if(event.target===this)closeModal()"><div class="modal-content" id="modal-body"></div></div>
<script>
var API=window.location.origin,STATE={user:null,balance:0},H={};
if(window.Telegram&&window.Telegram.WebApp){var tg=window.Telegram.WebApp;tg.ready();tg.expand();H['X-Telegram-Init-Data']=tg.initData}
async function api(m,u,b){var o={method:m,headers:Object.assign({'Content-Type':'application/json'},H)};if(b)o.body=JSON.stringify(b);var r=await fetch(API+u,o),d=await r.json();if(!r.ok)throw new Error(d.detail||'Error');return d}
async function loadUser(){try{STATE.user=await api('GET','/auth/me');STATE.balance=parseFloat(STATE.user.balance);document.getElementById('bal').textContent=fmt(STATE.balance);document.getElementById('loading').style.display='none';document.getElementById('app').style.display='flex';nav('home')}catch(e){document.getElementById('loading').innerHTML='<p style="color:#f44336;text-align:center;padding:20px">Откройте через Telegram!</p>'}}
function fmt(n){return parseFloat(n).toLocaleString('ru-RU',{maximumFractionDigits:2})}
function toast(m,e){var d=document.createElement('div');d.className='toast';if(e)d.style.borderLeftColor='#f44336';d.textContent=m;document.getElementById('toasts').appendChild(d);setTimeout(function(){d.remove()},2500)}
function showModal(h){document.getElementById('modal-body').innerHTML=h;document.getElementById('modal').style.display='flex'}
function closeModal(){document.getElementById('modal').style.display='none'}
function nav(p){document.querySelectorAll('.nav-btn').forEach(function(b){b.classList.toggle('active',b.dataset.p===p)});render(p)}
async function render(p){var c=document.getElementById('content');c.innerHTML='';if(p!=='home')c.innerHTML='<button class="back-btn" onclick="nav(\'home\')">← Назад</button>';
if(p==='home'){var items=[{id:'cases',icon:'📦',name:'Кейсы'},{id:'crash',icon:'🐸',name:'Crash'},{id:'arena',icon:'⚔️',name:'Арена'},{id:'inv',icon:'🎒',name:'Инвентарь'},{id:'upgrade',icon:'⬆️',name:'Апгрейд'},{id:'shop',icon:'🛒',name:'Магазин'},{id:'deposit',icon:'💳',name:'Баланс'}];if(STATE.user&&STATE.user.is_admin)items.push({id:'admin',icon:'🔧',name:'Админ-панель'});c.innerHTML+='<div class="menu-list">'+items.map(function(i){return'<div class="menu-item" onclick="nav(\''+i.id+'\')"><div class="menu-name">'+i.icon+' '+i.name+'</div><div style="color:#8b92a0">→</div></div>'}).join('')+'</div>'}
else if(p==='cases'){c.innerHTML+='<h2>📦 Кейсы</h2><div class="case-list" id="caseList">Загрузка...</div>';try{var cases=await api('GET','/cases/');document.getElementById('caseList').innerHTML=cases.map(function(cs){return'<div class="case-card" onclick="openCaseModal('+cs.id+')"><div><div class="case-name">'+cs.name+'</div><div style="font-size:10px;color:#8b92a0">'+cs.items.length+' предметов</div></div><div class="case-price">⭐'+fmt(cs.price)+'</div></div>'}).join('')}catch(e){}}
else if(p==='crash'){c.innerHTML+='<div style="text-align:center"><h2>🐸 Crash</h2><div style="font-size:50px;font-weight:900;color:#4caf50" id="cm">1.00x</div><div id="cs" style="font-size:12px;color:#8b92a0;margin-bottom:10px">Ожидание...</div><input type="number" id="cb" placeholder="Ставка" value="10" step="1"><input type="number" id="ca" placeholder="Автокэшаут (x)" value="2.0" step="0.1"><button class="btn" onclick="crashBet()">🎲 Поставить</button><button class="btn" id="cc" onclick="crashCash()" style="display:none;background:#4caf50">💰 Забрать</button></div>';setInterval(async function(){try{var g=await api('GET','/crash/current');if(g.status==='running'){document.getElementById('cm').textContent=parseFloat(g.multiplier).toFixed(2)+'x';document.getElementById('cs').textContent='🚀 Летит!'}else document.getElementById('cs').textContent='Ожидание...'}catch(e){}},200)}
else if(p==='arena'){c.innerHTML+='<h2>⚔️ Арена</h2><div class="case-list" id="al">Загрузка...</div><button class="btn" onclick="arenaCreate()">➕ Создать арену</button>';try{var gs=await api('GET','/arena/games');document.getElementById('al').innerHTML=gs.length?gs.map(function(g){return'<div class="case-card"><div><div class="case-name">Арена #'+g.id+'</div><div style="font-size:10px;color:#8b92a0">Игроков: '+g.players_count+'/'+g.max_players+'</div></div><div style="text-align:right"><div class="case-price">⭐'+fmt(g.total_pot)+'</div><button class="btn btn-sm" onclick="arenaJoin('+g.id+')">Войти</button></div></div>'}).join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Нет активных арен</div>'}catch(e){}}
else if(p==='inv'){c.innerHTML+='<h2>🎒 Инвентарь</h2><div class="case-list" id="il">Загрузка...</div>';try{var items=await api('GET','/inventory/?limit=50');document.getElementById('il').innerHTML=items.length?items.map(function(i){return'<div class="case-card" onclick="sellItem('+i.id+')"><div class="case-name">'+i.item_name+'</div><div class="case-price">⭐'+fmt(i.item_value)+'</div></div>'}).join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Инвентарь пуст</div>'}catch(e){}}
else if(p==='upgrade'){c.innerHTML+='<h2>⬆️ Апгрейд</h2><p style="font-size:11px;color:#8b92a0;margin-bottom:8px">Выберите 2 предмета</p><div class="case-list" id="ul">Загрузка...</div>';try{var uitems=await api('GET','/inventory/?limit=50');document.getElementById('ul').innerHTML=uitems.map(function(i){return'<div class="case-card" id="u_'+i.id+'" onclick="selUp('+i.id+')"><div class="case-name">'+i.item_name+'</div><div class="case-price">⭐'+fmt(i.item_value)+'</div></div>'}).join('');window._up=[];window._ui=uitems}catch(e){}}
else if(p==='shop'){c.innerHTML+='<h2>🛒 Магазин</h2><div class="case-list" id="sl">Загрузка...</div>';try{var s=await api('GET','/shop/');document.getElementById('sl').innerHTML=s.upgrades.map(function(i){return'<div class="case-card"><div class="case-name">'+i.name+'</div><button class="btn btn-sm" onclick="shopBuy(\''+i.type+'\')">⭐'+fmt(i.price)+'</button></div>'}).join('')+s.direct_items.map(function(i){return'<div class="case-card"><div class="case-name">'+i.name+'</div><button class="btn btn-sm" onclick="shopBuy(\''+i.type+'\')">⭐'+fmt(i.price)+'</button></div>'}).join('')}catch(e){}}
else if(p==='deposit'){c.innerHTML+='<h2>💳 Баланс: ⭐'+fmt(STATE.balance)+'</h2><input type="number" id="da" placeholder="Сумма Stars (мин 50)" value="50"><button class="btn" onclick="deposit()">⭐ Пополнить через Telegram Stars</button><div style="margin-top:16px"><input type="number" id="wa" placeholder="Сумма вывода (мин 100)" value="100"><button class="btn" onclick="withdraw()">💸 Вывести</button></div>'}
else if(p==='admin'&&STATE.user&&STATE.user.is_admin){c.innerHTML+='<h2>🔧 Админ-панель</h2><div class="menu-list"><div class="menu-item" onclick="adminWithdraws()"><div class="menu-name">💸 Заявки на вывод</div><div>→</div></div><div class="menu-item" onclick="showAdminBalance()"><div class="menu-name">💰 Изменить баланс</div><div>→</div></div><div class="menu-item" onclick="showAdminBlock()"><div class="menu-name">🚫 Блокировка</div><div>→</div></div><div class="menu-item" onclick="showAdminGive()"><div class="menu-name">🎁 Выдать предмет</div><div>→</div></div></div>'}}

window.selUp=function(id){if(!window._up)window._up=[];if(window._up.length>=2)window._up=[];window._up.push(id);document.getElementById('u_'+id).style.border='2px solid #f5a623';if(window._up.length===2){var a=window._ui.find(function(i){return i.id===window._up[0]}),b=window._ui.find(function(i){return i.id===window._up[1]});showModal('<h3>Апгрейд?</h3><p>'+a.item_name+' → '+b.item_name+'</p><p style="color:#ff9800;font-size:12px">⚠️ При неудаче оба уничтожаются!</p><button class="btn" onclick="doUpgrade()">Подтвердить</button>')}};
window.doUpgrade=async function(){try{var r=await api('POST','/upgrade/',{item_id:window._up[0],target_item_id:window._up[1]});toast(r.message,r.success?0:1);closeModal();nav('inv')}catch(e){toast(e.message,1)}};
window.openCaseModal=async function(id){var c=await api('GET','/cases/'+id);showModal('<h3>'+c.name+'</h3><p>Цена: ⭐'+fmt(c.price)+'</p><button class="btn" onclick="openCase('+id+','+c.price+')">🎲 Открыть</button>')};
window.openCase=async function(id,price){if(STATE.balance<price){toast('Недостаточно средств!',1);return}closeModal();try{var r=await api('POST','/cases/open',{case_id:id,idempotency_key:'k_'+Date.now()});STATE.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(STATE.balance);showModal('<h3>🎉 '+r.item.name+'!</h3><p>Стоимость: ⭐'+fmt(r.item.value)+'</p><p style="text-transform:uppercase;font-weight:700">'+r.item.rarity+'</p><button class="btn" onclick="closeModal()">OK</button>')}catch(e){toast(e.message,1)}};
window.sellItem=async function(id){showModal('<h3>Продать предмет?</h3><p>Цена: 80% от стоимости</p><button class="btn" onclick="doSell('+id+')">Продать</button>')};
window.doSell=async function(id){try{var r=await api('POST','/inventory/sell',{item_id:id});STATE.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(STATE.balance);toast('Продано за ⭐'+fmt(r.sold_for));closeModal();nav('inv')}catch(e){toast(e.message,1)}};
window.crashBet=async function(){var a=parseFloat(document.getElementById('cb').value),ac=document.getElementById('ca').value?parseFloat(document.getElementById('ca').value):null;try{var r=await api('POST','/crash/bet',{amount:a,auto_cashout:ac});STATE.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(STATE.balance);document.getElementById('cc').style.display='block';toast('Ставка принята!')}catch(e){toast(e.message,1)}};
window.crashCash=async function(){try{var r=await api('POST','/crash/cashout',{game_id:0});STATE.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(STATE.balance);document.getElementById('cc').style.display='none';toast('+'+fmt(r.profit)+' ('+parseFloat(r.multiplier).toFixed(2)+'x)')}catch(e){toast(e.message,1)}};
window.arenaCreate=async function(){try{var r=await api('POST','/arena/create');toast('Арена #'+r.game_id+' создана!');nav('arena')}catch(e){toast(e.message,1)}};
window.arenaJoin=async function(id){var a=prompt('Сумма ставки:');if(!a)return;try{var r=await api('POST','/arena/join/'+id,{bet_amount:parseFloat(a)});STATE.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(STATE.balance);toast('Вы в игре!');nav('arena')}catch(e){toast(e.message,1)}};
window.shopBuy=async function(t){try{var r=await api('POST','/shop/buy/'+t);STATE.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(STATE.balance);toast('Куплено!')}catch(e){toast(e.message,1)}};
window.deposit=async function(){var a=parseInt(document.getElementById('da').value);if(a<50){toast('Минимум 50 Stars',1);return}try{var r=await api('POST','/stars/deposit',{amount:a});showModal('<h3>⭐ Пополнение</h3><p>'+r.amount_stars+' Stars</p><p>К зачислению: '+fmt(r.amount_received)+'</p><p style="font-size:11px;color:#8b92a0">Комиссия: '+fmt(r.fee)+'</p><button class="btn" onclick="confirmDeposit('+a+')">Подтвердить оплату</button>')}catch(e){toast(e.message,1)}};
window.confirmDeposit=async function(a){try{var r=await api('POST','/stars/deposit/confirm',{telegram_payment_id:'stars_'+Date.now(),amount_stars:a});STATE.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(STATE.balance);closeModal();toast('Пополнено на ⭐'+fmt(r.amount_received))}catch(e){toast(e.message,1)}};
window.withdraw=async function(){var a=parseFloat(document.getElementById('wa').value);if(a<100){toast('Минимум 100',1);return}try{var r=await api('POST','/withdraw/',{amount:a});STATE.balance-=a;document.getElementById('bal').textContent=fmt(STATE.balance);toast('Заявка #'+r.id+' создана')}catch(e){toast(e.message,1)}};
window.adminWithdraws=async function(){try{var w=await api('GET','/admin/withdrawals');showModal('<h3>💸 Заявки на вывод</h3>'+w.map(function(r){return'<div class="case-card" style="margin:4px 0"><div><div class="case-name">#'+r.id+' | User: '+r.user_id+'</div><div style="font-size:10px;color:#8b92a0">⭐'+fmt(r.amount)+' → '+fmt(r.amount_after_fee)+'</div></div><div style="display:flex;gap:4px"><button class="btn btn-sm" onclick="adminW('+r.id+',\'approve\')">✅</button><button class="btn btn-sm btn-red" onclick="adminW('+r.id+',\'reject\')">❌</button></div></div>'}).join('')+'<button class="btn" onclick="closeModal()" style="margin-top:8px">Закрыть</button>')}catch(e){toast(e.message,1)}};
window.adminW=async function(id,a){try{await api('POST','/admin/withdrawals/process',{request_id:id,action:a});toast(a==='approve'?'Одобрено':'Отклонено');adminWithdraws()}catch(e){toast(e.message,1)}};
window.showAdminBalance=function(){showModal('<h3>💰 Изменить баланс</h3><input type="number" id="ab_uid" placeholder="ID пользователя"><input type="number" id="ab_amt" placeholder="Сумма"><select id="ab_op"><option value="add">Добавить</option><option value="subtract">Вычесть</option><option value="set">Установить</option></select><button class="btn" onclick="adminBalance()">Применить</button>')};
window.adminBalance=async function(){try{var r=await api('POST','/admin/balance',{user_id:parseInt(document.getElementById('ab_uid').value),amount:parseFloat(document.getElementById('ab_amt').value),operation:document.getElementById('ab_op').value});toast('Баланс: '+fmt(r.new_balance));closeModal()}catch(e){toast(e.message,1)}};
window.showAdminBlock=function(){showModal('<h3>🚫 Блокировка</h3><input type="number" id="abl_uid" placeholder="ID пользователя"><button class="btn" onclick="adminBlock(true)">Заблокировать</button><button class="btn" onclick="adminBlock(false)" style="margin-top:4px">Разблокировать</button>')};
window.adminBlock=async function(b){try{await api('POST','/admin/block',{user_id:parseInt(document.getElementById('abl_uid').value),block:b});toast(b?'Заблокирован':'Разблокирован');closeModal()}catch(e){toast(e.message,1)}};
window.showAdminGive=function(){showModal('<h3>🎁 Выдать предмет</h3><input type="number" id="ag_uid" placeholder="ID пользователя"><input type="text" id="ag_name" placeholder="Название"><input type="number" id="ag_val" placeholder="Стоимость"><select id="ag_rar"><option value="common">Обычный</option><option value="uncommon">Необычный</option><option value="rare">Редкий</option><option value="epic">Эпический</option><option value="legendary">Легендарный</option></select><button class="btn" onclick="adminGive()">Выдать</button>')};
window.adminGive=async function(){try{await api('POST','/admin/give-item',{user_id:parseInt(document.getElementById('ag_uid').value),item_name:document.getElementById('ag_name').value,item_value:parseFloat(document.getElementById('ag_val').value),item_rarity:document.getElementById('ag_rar').value});toast('Предмет выдан!');closeModal()}catch(e){toast(e.message,1)}};
loadUser();
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
