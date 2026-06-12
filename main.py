"""
CaseFight — Telegram Mini App
Full backend + frontend: FastAPI + PostgreSQL + Telegram Stars + Crash + Arena + Upgrade + WebSocket
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

# ======================== MODELS (сокращённые) ========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
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
    id = Column(Integer, primary_key=True); case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"))
    name = Column(String(255)); image_url = Column(Text); value = Column(DECIMAL(15,2))
    drop_chance = Column(DECIMAL(6,4)); rarity = Column(String(50), default="common")

class UserInventory(Base):
    __tablename__ = "user_inventory"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    item_name = Column(String(255)); item_value = Column(DECIMAL(15,2)); item_rarity = Column(String(50))
    obtained_at = Column(DateTime(timezone=True), server_default=func.now())

class DepositTransaction(Base):
    __tablename__ = "deposit_transactions"
    id = Column(Integer, primary_key=True); user_id = Column(Integer)
    telegram_payment_id = Column(String(255), unique=True); amount_stars = Column(Integer)
    amount_received = Column(DECIMAL(15,2)); fee = Column(DECIMAL(15,2)); verified = Column(Boolean, default=False)

class WithdrawRequest(Base):
    __tablename__ = "withdraw_requests"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); amount = Column(DECIMAL(15,2))
    fee = Column(DECIMAL(15,2)); amount_after_fee = Column(DECIMAL(15,2)); status = Column(String(50), default="pending")

class CrashGame(Base):
    __tablename__ = "crash_games"
    id = Column(Integer, primary_key=True); crash_point = Column(DECIMAL(10,4)); status = Column(String(50), default="active")

class CrashBet(Base):
    __tablename__ = "crash_bets"
    id = Column(Integer, primary_key=True); game_id = Column(Integer); user_id = Column(Integer)
    amount = Column(DECIMAL(15,2)); auto_cashout = Column(DECIMAL(10,4)); profit = Column(DECIMAL(15,2))
    status = Column(String(50), default="active")

class ArenaGame(Base):
    __tablename__ = "arena_games"
    id = Column(Integer, primary_key=True); creator_id = Column(Integer); status = Column(String(50), default="waiting")
    total_pot = Column(DECIMAL(15,2), default=Decimal("0.00")); winner_id = Column(Integer)

class ArenaPlayer(Base):
    __tablename__ = "arena_players"
    id = Column(Integer, primary_key=True); game_id = Column(Integer); user_id = Column(Integer); bet_amount = Column(DECIMAL(15,2))

# ======================== SCHEMAS ========================
class UserOut(BaseModel):
    id: int; telegram_id: int; username: Optional[str]; first_name: Optional[str]
    balance: Decimal; is_admin: bool; can_odd_bets: bool; case_cooldown_removed: bool
    model_config = {"from_attributes": True}

class ItemOut(BaseModel):
    id: int; name: str; value: Decimal; rarity: str
    model_config = {"from_attributes": True}

class CaseOut(BaseModel):
    id: int; name: str; price: Decimal; type: str; items: List[ItemOut] = []
    model_config = {"from_attributes": True}

class OpenCaseReq(BaseModel): case_id: int; idempotency_key: str
class OpenCaseResp(BaseModel): success: bool; item: ItemOut; balance_after: Decimal
class SellReq(BaseModel): item_id: int
class SellResp(BaseModel): success: bool; sold_for: Decimal; balance_after: Decimal
class StarsDepositReq(BaseModel): amount: int = Field(ge=50, le=10000)
class StarsInvoiceResp(BaseModel): invoice_link: str; amount_stars: int; amount_received: Decimal; fee: Decimal
class WithdrawReq(BaseModel): amount: Decimal = Field(ge=100, le=1000000)
class WithdrawOut(BaseModel): id: int; amount: Decimal; status: str; created_at: Optional[datetime]; model_config = {"from_attributes": True}
class CrashBetReq(BaseModel): amount: Decimal = Field(gt=0, le=100000); auto_cashout: Optional[Decimal] = None
class ArenaJoinReq(BaseModel): bet_amount: Decimal = Field(gt=0, le=100000)
class UpgradeReq(BaseModel): item_id: int; target_item_id: int
class UpgradeResp(BaseModel): success: bool; message: str
class AdminBalanceReq(BaseModel): user_id: int; amount: Decimal; operation: str

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
    return {"telegram_id": u.get("id"), "username": u.get("username"), "first_name": u.get("first_name")}

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    if settings.DEBUG:
        dev_id = request.headers.get("X-Telegram-User-Id")
        if dev_id and request.headers.get("X-Dev-Token") == settings.SECRET_KEY:
            r = await db.execute(select(User).where(User.telegram_id == int(dev_id)))
            u = r.scalar_one_or_none()
            if u: return u
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data: raise HTTPException(401, "No auth")
    parsed = validate_telegram_init(init_data)
    if not parsed: raise HTTPException(401, "Invalid auth")
    ud = extract_user(parsed); tid = ud.get("telegram_id")
    r = await db.execute(select(User).where(User.telegram_id == tid))
    u = r.scalar_one_or_none()
    if not u:
        u = User(telegram_id=tid, username=ud.get("username"), first_name=ud.get("first_name"),
                 is_admin=(tid == settings.ADMIN_TELEGRAM_ID))
        db.add(u); await db.flush(); await db.refresh(u)
    if u.is_blocked: raise HTTPException(403, "Blocked")
    return u

async def get_admin(u: User = Depends(get_current_user)) -> User:
    if not u.is_admin: raise HTTPException(403, "Admin only")
    return u

# ======================== APP ========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    await seed_db()
    crash_task = asyncio.create_task(crash_loop())
    yield; crash_task.cancel()

app = FastAPI(title="CaseFight", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.CORS_ORIGINS],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ======================== SEED ========================
async def seed_db():
    async with async_session() as db:
        if (await db.execute(select(Case).limit(1))).scalar_one_or_none(): return
        cases_data = [
            ("🌟 Starter Stars", 10, 0, [("Деревянный Меч",5,0.25,"common"),("Кожаный Щит",6,0.20,"common"),("Железный Кинжал",8,0.18,"common"),("Кольцо Силы",10,0.15,"uncommon"),("Амулет Защиты",12,0.10,"uncommon"),("Стальной Шлем",15,0.07,"rare"),("Золотой Браслет",20,0.04,"epic"),("Меч Новичка",30,0.01,"legendary")]),
            ("🥉 Bronze Stars", 50, 5, [("Бронзовый Меч",25,0.22,"common"),("Бронзовый Щит",28,0.20,"common"),("Кольцо Ловкости",35,0.18,"uncommon"),("Плащ Теней",45,0.15,"uncommon"),("Серебряный Амулет",60,0.12,"rare"),("Топор Гномов",75,0.08,"rare"),("Корона Воина",100,0.04,"epic"),("Бронзовый Дракон",150,0.01,"legendary")]),
            ("🥈 Silver Stars", 200, 10, [("Серебряный Меч",100,0.20,"common"),("Серебряный Щит",110,0.18,"common"),("Кольцо Магии",140,0.16,"uncommon"),("Эльфийский Лук",180,0.15,"uncommon"),("Рунический Посох",240,0.12,"rare"),("Мифриловая Кольчуга",300,0.10,"rare"),("Сапфировая Тиара",400,0.07,"epic"),("Серебряный Феникс",600,0.02,"legendary")]),
            ("🥇 Gold Stars", 500, 15, [("Золотой Меч",250,0.18,"common"),("Золотой Щит",280,0.17,"common"),("Кольцо Власти",350,0.16,"uncommon"),("Посох Архимага",450,0.14,"uncommon"),("Доспехи Паладина",600,0.13,"rare"),("Клинок Титана",750,0.10,"rare"),("Корона Короля",1000,0.08,"epic"),("Золотой Дракон",1500,0.04,"legendary")]),
        ]
        for name, price, cd, items in cases_data:
            c = Case(name=name, price=Decimal(price), cooldown_seconds=cd, type="stars"); db.add(c); await db.flush()
            for iname, val, ch, rar in items:
                db.add(CaseItem(case_id=c.id, name=iname, value=Decimal(val), drop_chance=Decimal(str(ch)), rarity=rar))
        await db.commit()

# ======================== CRASH ========================
current_crash_game = None; current_multiplier = Decimal("1.00"); crash_lock = asyncio.Lock()

async def crash_loop():
    global current_crash_game, current_multiplier
    while True:
        async with crash_lock:
            async with async_session() as db:
                crash_pt = Decimal(str(round(max((0.99/(1.0-random.random()))*0.95, 1.01), 2))) if random.random()>0.01 else Decimal("1.00")
                game = CrashGame(crash_point=crash_pt, status="active"); db.add(game); await db.commit()
                current_crash_game = game; current_multiplier = Decimal("1.00")
                start = time.monotonic(); dur = min(15.0, max(3.0, float(crash_pt)*0.3))
                while current_multiplier < crash_pt:
                    p = min((time.monotonic()-start)/dur, 1.0)
                    current_multiplier = (Decimal("1.00")+(crash_pt-Decimal("1.00"))*Decimal(str(p))).quantize(Decimal("0.01"))
                    await asyncio.sleep(0.1)
                    bets = (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==game.id, CrashBet.status=="active", CrashBet.auto_cashout.isnot(None), CrashBet.auto_cashout<=current_multiplier)))).scalars().all()
                    for b in bets:
                        b.profit = (b.amount*current_multiplier)-b.amount; b.status="cashed_out"
                        u = (await db.execute(select(User).where(User.id==b.user_id))).scalar_one(); u.balance += b.amount+b.profit
                    if bets: await db.commit()
                for b in (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==game.id, CrashBet.status=="active")))).scalars().all():
                    b.status="lost"; b.profit=-b.amount
                game.status="finished"; await db.commit()
                current_crash_game=None; current_multiplier=Decimal("1.00")
        await asyncio.sleep(5)

# ======================== API ========================
@app.get("/health")
async def health(): return {"status":"ok"}

@app.get("/auth/me", response_model=UserOut)
async def me(u: User=Depends(get_current_user)): return u

@app.get("/cases/", response_model=List[CaseOut])
async def get_cases(db: AsyncSession=Depends(get_db)):
    cases = (await db.execute(select(Case).where(Case.is_active==True).order_by(Case.price))).scalars().all()
    res = []
    for c in cases:
        items = (await db.execute(select(CaseItem).where(CaseItem.case_id==c.id))).scalars().all()
        res.append(CaseOut(id=c.id, name=c.name, price=c.price, type=c.type, items=[ItemOut.model_validate(i) for i in items]))
    return res

@app.post("/cases/open", response_model=OpenCaseResp)
async def open_case(req: OpenCaseReq, db: AsyncSession=Depends(get_db), u: User=Depends(get_current_user)):
    c = (await db.execute(select(Case).where(Case.id==req.case_id))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < c.price: raise HTTPException(400, "No balance")
    u.balance -= c.price
    items = (await db.execute(select(CaseItem).where(CaseItem.case_id==c.id))).scalars().all()
    r = random.uniform(0, sum(float(i.drop_chance) for i in items)); cum=0.0; sel=items[-1]
    for i in items:
        cum+=float(i.drop_chance)
        if r<=cum: sel=i; break
    db.add(UserInventory(user_id=u.id, item_name=sel.name, item_value=sel.value, item_rarity=sel.rarity))
    await db.flush()
    return OpenCaseResp(success=True, item=ItemOut.model_validate(sel), balance_after=u.balance)

@app.get("/inventory/")
async def get_inventory(u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    items = (await db.execute(select(UserInventory).where(UserInventory.user_id==u.id).order_by(UserInventory.obtained_at.desc()).limit(50))).scalars().all()
    return [{"id":i.id,"item_name":i.item_name,"item_value":str(i.item_value),"item_rarity":i.item_rarity} for i in items]

@app.post("/inventory/sell", response_model=SellResp)
async def sell_item(req: SellReq, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    item = (await db.execute(select(UserInventory).where(and_(UserInventory.id==req.item_id, UserInventory.user_id==u.id)))).scalar_one_or_none()
    if not item: raise HTTPException(404)
    price = (Decimal(str(item.item_value or 0))*Decimal("0.8")).quantize(Decimal("0.01"))
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += price
    await db.delete(item); await db.flush()
    return SellResp(success=True, sold_for=price, balance_after=u.balance)

@app.post("/stars/deposit", response_model=StarsInvoiceResp)
async def stars_deposit(req: StarsDepositReq, u: User=Depends(get_current_user)):
    gross = Decimal(req.amount)*Decimal(str(settings.STARS_RATE))
    fee = (gross*Decimal(str(settings.DEPOSIT_FEE_PERCENT))/100).quantize(Decimal("0.01"))
    received = gross-fee
    payload = f"stars_{u.id}_{int(time.time())}_{secrets.token_hex(4)}"
    invoice_link = f"https://t.me/{settings.BOT_USERNAME}?start=pay_{payload}"
    return StarsInvoiceResp(invoice_link=invoice_link, amount_stars=req.amount, amount_received=received, fee=fee)

@app.post("/stars/deposit/confirm")
async def confirm_deposit(data: dict, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    pid = data.get("telegram_payment_id"); amt = data.get("amount_stars")
    if not pid: raise HTTPException(400)
    gross = Decimal(str(amt))*Decimal(str(settings.STARS_RATE))
    fee = (gross*Decimal(str(settings.DEPOSIT_FEE_PERCENT))/100).quantize(Decimal("0.01"))
    received = gross-fee
    db.add(DepositTransaction(user_id=u.id, telegram_payment_id=pid, amount_stars=int(amt), amount_received=received, fee=fee, verified=True))
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += received; u.total_deposited += received
    await db.flush()
    return {"success":True,"balance":str(u.balance),"amount_received":str(received)}

@app.post("/withdraw/", response_model=WithdrawOut)
async def create_withdraw(req: WithdrawReq, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if req.amount < settings.MIN_WITHDRAW or req.amount > u.balance: raise HTTPException(400)
    fee = (req.amount*Decimal(str(settings.WITHDRAW_FEE_PERCENT))/100).quantize(Decimal("0.01"))
    u.balance -= req.amount
    wr = WithdrawRequest(user_id=u.id, amount=req.amount, fee=fee, amount_after_fee=req.amount-fee, status="pending")
    db.add(wr); await db.flush()
    return WithdrawOut.model_validate(wr)

@app.get("/crash/current")
async def crash_current():
    if current_crash_game: return {"game_id":current_crash_game.id,"multiplier":str(current_multiplier),"status":"running"}
    return {"status":"waiting"}

@app.post("/crash/bet")
async def crash_bet(req: CrashBetReq, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    if not current_crash_game: raise HTTPException(400)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < req.amount: raise HTTPException(400)
    u.balance -= req.amount
    db.add(CrashBet(game_id=current_crash_game.id, user_id=u.id, amount=req.amount, auto_cashout=req.auto_cashout, status="active"))
    await db.flush()
    return {"success":True,"balance":str(u.balance)}

@app.post("/crash/cashout")
async def crash_cashout(u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    if not current_crash_game: raise HTTPException(400)
    async with crash_lock:
        bet = (await db.execute(select(CrashBet).where(and_(CrashBet.user_id==u.id, CrashBet.game_id==current_crash_game.id, CrashBet.status=="active")))).scalar_one_or_none()
        if not bet: raise HTTPException(404)
        profit = (bet.amount*current_multiplier)-bet.amount; bet.profit=profit; bet.status="cashed_out"
        u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += bet.amount+profit
        await db.flush()
    return {"success":True,"multiplier":str(current_multiplier),"profit":str(profit),"balance":str(u.balance)}

@app.get("/arena/games")
async def arena_games(db: AsyncSession=Depends(get_db)):
    games = (await db.execute(select(ArenaGame).where(ArenaGame.status=="waiting"))).scalars().all()
    res = []
    for g in games:
        cnt = (await db.execute(select(func.count(ArenaPlayer.id)).where(ArenaPlayer.game_id==g.id))).scalar()
        res.append({"id":g.id,"total_pot":str(g.total_pot),"players_count":cnt,"max_players":settings.ARENA_MAX_PLAYERS})
    return res

@app.post("/arena/create")
async def arena_create(u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    g = ArenaGame(creator_id=u.id, status="waiting"); db.add(g); await db.flush()
    return {"game_id":g.id}

@app.post("/arena/join/{game_id}")
async def arena_join(game_id: int, req: ArenaJoinReq, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    g = (await db.execute(select(ArenaGame).where(ArenaGame.id==game_id))).scalar_one_or_none()
    if not g: raise HTTPException(404)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < req.bet_amount: raise HTTPException(400)
    u.balance -= req.bet_amount; g.total_pot += req.bet_amount
    db.add(ArenaPlayer(game_id=g.id, user_id=u.id, bet_amount=req.bet_amount))
    await db.flush()
    return {"success":True}

@app.post("/upgrade/", response_model=UpgradeResp)
async def upgrade(req: UpgradeReq, u: User=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    frm = (await db.execute(select(UserInventory).where(and_(UserInventory.id==req.item_id, UserInventory.user_id==u.id)))).scalar_one_or_none()
    to = (await db.execute(select(UserInventory).where(and_(UserInventory.id==req.target_item_id, UserInventory.user_id==u.id)))).scalar_one_or_none()
    if not frm or not to: raise HTTPException(404)
    if to.item_value <= frm.item_value: raise HTTPException(400, "Target must be more expensive")
    chance = min(max(Decimal(str(frm.item_value))/Decimal(str(to.item_value)), Decimal("0.01")), Decimal("0.99"))
    success = random.random() < float(chance)
    await db.delete(frm)
    if success: to.item_value = (Decimal(str(to.item_value))*Decimal("1.05")).quantize(Decimal("0.01")); await db.flush(); return UpgradeResp(success=True, message=f"Upgraded to {to.item_value}!")
    else: await db.delete(to); await db.flush(); return UpgradeResp(success=False, message="Failed! Both destroyed.")

@app.post("/admin/balance")
async def admin_balance(req: AdminBalanceReq, db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    u = (await db.execute(select(User).where(User.id==req.user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404)
    amt = Decimal(str(req.amount))
    if req.operation=="add": u.balance += amt
    elif req.operation=="subtract": u.balance = max(u.balance-amt, Decimal("0"))
    elif req.operation=="set": u.balance = amt
    await db.flush()
    return {"success":True,"new_balance":str(u.balance)}

@app.get("/admin/withdrawals")
async def admin_withdrawals(db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    reqs = (await db.execute(select(WithdrawRequest).where(WithdrawRequest.status=="pending"))).scalars().all()
    return [{"id":r.id,"user_id":r.user_id,"amount":str(r.amount),"status":r.status} for r in reqs]

@app.post("/admin/withdrawals/process")
async def admin_process(data: dict, db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    wr = (await db.execute(select(WithdrawRequest).where(WithdrawRequest.id==data.get("request_id")))).scalar_one_or_none()
    if not wr: raise HTTPException(404)
    u = (await db.execute(select(User).where(User.id==wr.user_id))).scalar_one()
    if data.get("action")=="approve": wr.status="approved"; u.total_withdrawn+=wr.amount_after_fee
    else: wr.status="rejected"; u.balance+=wr.amount
    await db.flush()
    return {"success":True}

# ======================== FRONTEND ========================
@app.get("/", response_class=HTMLResponse)
async def root():
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no"><title>CaseFight</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,sans-serif}}
body{{background:#0a0e14;color:#e6e8ec;min-height:100vh;display:flex;flex-direction:column;padding-bottom:56px}}
header{{background:#12161e;padding:8px 12px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;border-bottom:1px solid rgba(255,255,255,0.06)}}
.logo{{font-size:16px;font-weight:800;cursor:pointer}} .logo span{{color:#f5a623}}
.balance{{background:#1a1f2b;padding:5px 10px;border-radius:14px;font-size:12px;font-weight:700;cursor:pointer}}
main{{padding:10px;flex:1;overflow-y:auto}}
.case-list{{display:flex;flex-direction:column;gap:8px}}
.case-card{{background:#1e2430;border-radius:10px;padding:12px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;border:1px solid rgba(255,255,255,0.04)}}
.case-card:active{{background:#252c38}}
.case-name{{font-weight:600;font-size:13px}} .case-price{{color:#f5a623;font-weight:700;font-size:13px}}
.btn{{background:linear-gradient(135deg,#f5a623,#f7c948);color:#000;border:none;padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer}}
.btn:active{{transform:scale(0.96)}} .btn-sm{{padding:6px 12px;font-size:11px}}
input,select{{width:100%;background:#1a1f2b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px;color:#e6e8ec;font-size:13px;margin-bottom:6px}}
.bottom-nav{{position:fixed;bottom:0;left:0;right:0;background:#12161e;display:flex;justify-content:space-around;padding:6px 4px 8px;border-top:1px solid rgba(255,255,255,0.06);z-index:100}}
.nav-btn{{background:none;border:none;color:#5c6370;font-size:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px}}
.nav-btn.active{{color:#f5a623}}
.modal{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:200;display:flex;align-items:center;justify-content:center}}
.modal-content{{background:#12161e;border-radius:14px;padding:20px;width:90%;max-width:360px;max-height:80vh;overflow-y:auto}}
.toast{{position:fixed;top:60px;right:10px;background:#1e2430;padding:10px 14px;border-radius:8px;font-size:12px;font-weight:600;z-index:300;border-left:3px solid #f5a623;animation:slideIn 0.3s}}
@keyframes slideIn{{from{{transform:translateX(100%);opacity:0}}to{{transform:translateX(0);opacity:1}}}}
h2{{font-size:16px;margin-bottom:10px}} .back-btn{{background:none;border:none;color:#f5a623;font-size:13px;cursor:pointer;margin-bottom:10px;padding:0}}
</style></head>
<body>
<div id="toasts"></div>
<div id="app" style="display:none">
<header><div class="logo">⚔️ <span>CaseFight</span></div><div class="balance" onclick="page('deposit')">⭐ <span id="bal">0</span></div></header>
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
const API=window.location.origin;let state={{user:null,balance:0,page:'home'}};
const H={{}};
if(window.Telegram?.WebApp){{const tg=window.Telegram.WebApp;tg.ready();tg.expand();H['X-Telegram-Init-Data']=tg.initData}}
else{{H['X-Dev-Token']='{settings.SECRET_KEY}';H['X-Telegram-User-Id']='123456789'}}

async function api(m,u,b=null){{const o={{method:m,headers:{{'Content-Type':'application/json',...H}}}};if(b)o.body=JSON.stringify(b);const r=await fetch(API+u,o);const d=await r.json();if(!r.ok)throw new Error(d.detail||'Error');return d}}

async function loadUser(){{try{{state.user=await api('GET','/auth/me');state.balance=parseFloat(state.user.balance);document.getElementById('bal').textContent=fmt(state.balance);document.getElementById('app').style.display='flex'}}catch(e){{alert('Откройте приложение через Telegram!')}}}}

function fmt(n){{return parseFloat(n).toLocaleString('ru-RU',{{maximumFractionDigits:2}})}}
function toast(m,e){{const d=document.createElement('div');d.className='toast';if(e)d.style.borderLeftColor='#f44336';d.textContent=m;document.getElementById('toasts').appendChild(d);setTimeout(()=>d.remove(),2500)}}
function showModal(h){{document.getElementById('modal-body').innerHTML=h;document.getElementById('modal').style.display='flex'}}
function closeModal(){{document.getElementById('modal').style.display='none'}}

function page(p){{state.page=p;document.querySelectorAll('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.p===p));render(p)}}

async function render(p){{
const c=document.getElementById('content');
if(p!=='home')c.innerHTML='<button class="back-btn" onclick="page(\'home\')">← Назад</button>'+c.innerHTML;
switch(p){{
case'home':c.innerHTML='<h2>Меню</h2><div class="case-list">'+['cases','crash','arena','inv','upgrade','shop','deposit'].map(x=>'<div class="case-card"onclick="page(\''+x+'\')"><div class="case-name">'+{{cases:'📦 Кейсы',crash:'🐸 Crash',arena:'⚔️ Арена',inv:'🎒 Инвентарь',upgrade:'⬆️ Апгрейд',shop:'🛒 Магазин',deposit:'💳 Баланс'}}[x]+'</div><div>→</div></div>').join('')+(state.user?.is_admin?'<div class="case-card"onclick="page(\'admin\')"><div class="case-name">🔧 Админ</div><div>→</div></div>':'')+'</div>';break;
case'cases':c.innerHTML='<h2>Кейсы</h2><div class="case-list"id="caseList"></div>';try{{const cs=await api('GET','/cases/');document.getElementById('caseList').innerHTML=cs.map(cs=>'<div class="case-card"onclick="openCase('+cs.id+',\''+cs.name+'\','+cs.price+')"><div class="case-name">'+cs.name+'</div><div class="case-price">⭐'+fmt(cs.price)+'</div></div>').join('')}}catch(e){{}}break;
case'crash':c.innerHTML='<h2>Crash</h2><div style="text-align:center"><div style="font-size:48px;font-weight:900;color:#4caf50"id="cm">1.00x</div><div id="cs"style="font-size:12px;color:#8b92a0">Ожидание...</div><input id="cb"placeholder="Ставка"value="10"><input id="ca"placeholder="Автокэшаут"value="2.0"><button class="btn"onclick="crashBet()"style="width:100%">Ставка</button><button class="btn"onclick="crashCash()"id="cc"style="display:none;width:100%;margin-top:6px;background:#4caf50">Забрать</button></div>';setInterval(async()=>{{try{{const g=await api('GET','/crash/current');if(g.status==='running'){{document.getElementById('cm').textContent=parseFloat(g.multiplier).toFixed(2)+'x';document.getElementById('cs').textContent='Летит!'}}else{{document.getElementById('cs').textContent='Ожидание...'}}}}catch{{}}}},200);break;
case'arena':c.innerHTML='<h2>Арена</h2><div class="case-list"id="al"></div><button class="btn"onclick="arenaCreate()"style="margin-top:10px;width:100%">Создать арену</button>';try{{const gs=await api('GET','/arena/games');document.getElementById('al').innerHTML=gs.length?gs.map(g=>'<div class="case-card"><div class="case-name">Арена #'+g.id+'</div><div class="case-price">'+g.players_count+'/'+g.max_players+' | ⭐'+fmt(g.total_pot)+'</div><button class="btn btn-sm"onclick="arenaJoin('+g.id+')">Войти</button></div>').join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Нет арен</div>'}}catch{{}}break;
case'inv':c.innerHTML='<h2>Инвентарь</h2><div class="case-list"id="il"></div>';try{{const it=await api('GET','/inventory/');document.getElementById('il').innerHTML=it.length?it.map(i=>'<div class="case-card"onclick="sellItem('+i.id+')"><div class="case-name">'+i.item_name+'</div><div class="case-price">⭐'+fmt(i.item_value)+'</div></div>').join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Пусто</div>'}}catch{{}}break;
case'upgrade':c.innerHTML='<h2>Апгрейд</h2><div class="case-list"id="ul"></div>';try{{const it=await api('GET','/inventory/');document.getElementById('ul').innerHTML=it.map(i=>'<div class="case-card"id="u_'+i.id+'"onclick="selUp('+i.id+')"><div class="case-name">'+i.item_name+'</div><div class="case-price">⭐'+fmt(i.item_value)+'</div></div>').join('');window._up=[];window._ui=it}}catch{{}}break;
case'shop':c.innerHTML='<h2>Магазин</h2><div class="case-list"><div class="case-card"><div class="case-name">🔥 Снятие кулдауна</div><button class="btn btn-sm"onclick="buy(\'cooldown_remove\')">⭐500</button></div><div class="case-card"><div class="case-name">🎲 Нечётные ставки</div><button class="btn btn-sm"onclick="buy(\'odd_bets\')">⭐250</button></div></div>';break;
case'deposit':c.innerHTML='<h2>Баланс: ⭐'+fmt(state.balance)+'</h2><input id="da"placeholder="Сумма Stars (мин 50)"value="50"type="number"><button class="btn"onclick="deposit()"style="width:100%">⭐ Пополнить через Stars</button><div style="margin-top:16px"><input id="wa"placeholder="Сумма вывода (мин 100)"value="100"type="number"><button class="btn"onclick="withdraw()"style="width:100%">💸 Вывод</button></div>';break;
case'admin':c.innerHTML='<h2>Админ</h2><div class="case-list"><div class="case-card"onclick="adminUsers()"><div class="case-name">👥 Пользователи</div></div><div class="case-card"onclick="adminWithdraws()"><div class="case-name">💸 Выводы</div></div></div>';break;
}}}}

window.selUp=function(id){{if(!window._up)window._up=[];if(window._up.length>=2)window._up=[];window._up.push(id);document.getElementById('u_'+id).style.border='2px solid #f5a623';if(window._up.length===2){{const a=window._ui.find(i=>i.id===window._up[0]);const b=window._ui.find(i=>i.id===window._up[1]);showModal('<h3>Апгрейд?</h3><p>'+a.item_name+' → '+b.item_name+'</p><p style="color:#ff9800">⚠️ При неудаче оба уничтожаются!</p><button class="btn"onclick="doUp()">Подтвердить</button>')}}}};
window.doUp=async function(){{try{{const r=await api('POST','/upgrade/',{{item_id:window._up[0],target_item_id:window._up[1]}});toast(r.message);closeModal();page('inv')}}catch(e){{toast(e.message,1)}}}};
window.openCase=async function(id,name,price){{if(state.balance<price){{toast('Недостаточно средств!',1);return}}try{{const r=await api('POST','/cases/open',{{case_id:id,idempotency_key:'k_'+Date.now()}});state.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(state.balance);showModal('<h3>🎉 '+r.item.name+'!</h3><p>⭐'+fmt(r.item.value)+'</p><p>'+r.item.rarity+'</p><button class="btn"onclick="closeModal()">OK</button>')}}catch(e){{toast(e.message,1)}}}};
window.sellItem=async function(id){{showModal('<h3>Продать?</h3><p>80% от стоимости</p><button class="btn"onclick="doSell('+id+')">Продать</button>')}};
window.doSell=async function(id){{try{{const r=await api('POST','/inventory/sell',{{item_id:id}});state.balance=parseFloat(r.balance_after);document.getElementById('bal').textContent=fmt(state.balance);toast('Продано за ⭐'+fmt(r.sold_for));closeModal();page('inv')}}catch(e){{toast(e.message,1)}}}};
window.crashBet=async function(){{const a=parseFloat(document.getElementById('cb').value);const ac=document.getElementById('ca').value?parseFloat(document.getElementById('ca').value):null;try{{const r=await api('POST','/crash/bet',{{amount:a,auto_cashout:ac}});state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);document.getElementById('cc').style.display='block';toast('Ставка!')}}catch(e){{toast(e.message,1)}}}};
window.crashCash=async function(){{try{{const r=await api('POST','/crash/cashout',{{game_id:0}});state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);document.getElementById('cc').style.display='none';toast('+'+fmt(r.profit))}}catch(e){{toast(e.message,1)}}}};
window.arenaCreate=async function(){{try{{const r=await api('POST','/arena/create');toast('Арена #'+r.game_id);page('arena')}}catch(e){{toast(e.message,1)}}}};
window.arenaJoin=async function(id){{const a=prompt('Ставка:');if(!a)return;try{{const r=await api('POST','/arena/join/'+id,{{bet_amount:parseFloat(a)}});toast('Вошли!');page('arena')}}catch(e){{toast(e.message,1)}}}};
window.buy=async function(t){{try{{const r=await api('POST','/shop/buy/'+t);state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);toast('Куплено!')}}catch(e){{toast(e.message,1)}}}};
window.deposit=async function(){{const a=parseInt(document.getElementById('da').value);if(a<50)return toast('Мин 50',1);try{{const r=await api('POST','/stars/deposit',{{amount:a}});window.Telegram?.WebApp?.openInvoice?window.Telegram.WebApp.openInvoice(r.invoice_link):showModal('<h3>Пополнение</h3><p>'+r.amount_stars+' Stars</p><p>К зачислению: '+fmt(r.amount_received)+'</p><button class="btn"onclick="confirmDep('+a+',\'stars_'+Date.now()+'\')">Подтвердить</button>')}}catch(e){{toast(e.message,1)}}}};
window.confirmDep=async function(a,pid){{try{{const r=await api('POST','/stars/deposit/confirm',{{telegram_payment_id:pid,amount_stars:a}});state.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(state.balance);closeModal();toast('+'+fmt(r.amount_received))}}catch(e){{toast(e.message,1)}}}};
window.withdraw=async function(){{const a=parseFloat(document.getElementById('wa').value);if(a<100)return toast('Мин 100',1);try{{const r=await api('POST','/withdraw/',{{amount:a}});state.balance-=a;document.getElementById('bal').textContent=fmt(state.balance);toast('Заявка #'+r.id)}}catch(e){{toast(e.message,1)}}}};
window.adminUsers=function(){{showModal('<h3>В разработке</h3><button class="btn"onclick="closeModal()">OK</button>')}};
window.adminWithdraws=async function(){{try{{const w=await api('GET','/admin/withdrawals');showModal('<h3>Выводы</h3>'+w.map(r=>'<div class="case-card"style="margin:4px 0"><span>#'+r.id+' User:'+r.user_id+'</span><span>⭐'+fmt(r.amount)+'</span><button class="btn btn-sm"onclick="adminW('+r.id+',\'approve\')">✅</button><button class="btn btn-sm"onclick="adminW('+r.id+',\'reject\')">❌</button></div>').join('')+'<button class="btn"onclick="closeModal()"style="margin-top:8px">Закрыть</button>')}}catch(e){{toast(e.message,1)}}}};
window.adminW=async function(id,a){{try{{await api('POST','/admin/withdrawals/process',{{request_id:id,action:a}});toast('OK');adminWithdraws()}}catch(e){{toast(e.message,1)}}}};

loadUser().then(()=>{{page('home')}});
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
