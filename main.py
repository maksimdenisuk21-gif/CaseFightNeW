"""
CaseFight — Telegram Mini App (Fixed)
"""

import asyncio, hashlib, hmac, json, os, random, secrets, time, urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean,
    DateTime, ForeignKey, DECIMAL, select, func, and_, or_
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
    STARS_RATE: Decimal = Decimal(os.getenv("STARS_RATE", "1.0"))
    MIN_DEPOSIT: int = int(os.getenv("MIN_DEPOSIT_STARS", "50"))
    MIN_WITHDRAW: Decimal = Decimal(os.getenv("MIN_WITHDRAW", "100.0"))
    DEPOSIT_FEE: Decimal = Decimal(os.getenv("DEPOSIT_FEE_PERCENT", "5.0")) / 100
    WITHDRAW_FEE: Decimal = Decimal(os.getenv("WITHDRAW_FEE_PERCENT", "5.0")) / 100
    CRASH_EDGE: Decimal = Decimal(os.getenv("CRASH_HOUSE_EDGE", "0.05"))
    ARENA_FEE: Decimal = Decimal(os.getenv("ARENA_PLATFORM_FEE", "5.0")) / 100
    ARENA_MAX: int = int(os.getenv("ARENA_MAX_PLAYERS", "5"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", os.urandom(32).hex())
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

settings = Settings()

# ======================== DATABASE ========================
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as s:
        yield s

# ======================== MODELS ========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255)); first_name = Column(String(255))
    balance = Column(DECIMAL(15,2), default=Decimal("0.00"))
    total_deposited = Column(DECIMAL(15,2), default=Decimal("0.00"))
    total_withdrawn = Column(DECIMAL(15,2), default=Decimal("0.00"))
    is_blocked = Column(Boolean, default=False); is_admin = Column(Boolean, default=False)
    can_odd_bets = Column(Boolean, default=False); cooldown_removed = Column(Boolean, default=False)
    cooldown_until = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True); name = Column(String(255))
    price = Column(DECIMAL(15,2)); type = Column(String(50), default="stars")
    is_active = Column(Boolean, default=True); cooldown = Column(Integer, default=0)

class CaseItem(Base):
    __tablename__ = "case_items"
    id = Column(Integer, primary_key=True); case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    name = Column(String(255)); value = Column(DECIMAL(15,2)); chance = Column(DECIMAL(6,4)); rarity = Column(String(50))

class UserItem(Base):
    __tablename__ = "user_items"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name = Column(String(255)); value = Column(DECIMAL(15,2)); rarity = Column(String(50))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    amount = Column(DECIMAL(15,2)); fee = Column(DECIMAL(15,2)); net = Column(DECIMAL(15,2))
    status = Column(String(50), default="pending"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class CrashGame(Base):
    __tablename__ = "crash_games"
    id = Column(Integer, primary_key=True); point = Column(DECIMAL(10,4))
    status = Column(String(50), default="active"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class CrashBet(Base):
    __tablename__ = "crash_bets"
    id = Column(Integer, primary_key=True); game_id = Column(Integer, ForeignKey("crash_games.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount = Column(DECIMAL(15,2)); auto_cashout = Column(DECIMAL(10,4)); multiplier = Column(DECIMAL(10,4))
    profit = Column(DECIMAL(15,2)); status = Column(String(50), default="active")

class ArenaRoom(Base):
    __tablename__ = "arena_rooms"
    id = Column(Integer, primary_key=True); creator_id = Column(Integer)
    pot = Column(DECIMAL(15,2), default=Decimal("0.00")); status = Column(String(50), default="waiting")
    winner_id = Column(Integer); created_at = Column(DateTime(timezone=True), server_default=func.now())

class ArenaPlayer(Base):
    __tablename__ = "arena_players"
    id = Column(Integer, primary_key=True); room_id = Column(Integer, ForeignKey("arena_rooms.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer); bet = Column(DECIMAL(15,2)); chance = Column(DECIMAL(6,4))

# ======================== SCHEMAS ========================
class UserOut(BaseModel):
    id: int; telegram_id: int; username: Optional[str]; first_name: Optional[str]
    balance: Decimal; is_admin: bool; can_odd_bets: bool; cooldown_removed: bool
    model_config = {"from_attributes": True}

class ItemOut(BaseModel): id: int; name: str; value: Decimal; rarity: str; model_config = {"from_attributes": True}
class CaseOut(BaseModel): id: int; name: str; price: Decimal; type: str; items: List[ItemOut] = []; model_config = {"from_attributes": True}
class OpenCaseReq(BaseModel): case_id: int
class OpenCaseResp(BaseModel): item: ItemOut; balance: Decimal
class SellReq(BaseModel): item_id: int
class SellResp(BaseModel): sold: Decimal; balance: Decimal
class DepositReq(BaseModel): amount: int = Field(ge=50)
class WithdrawReq(BaseModel): amount: Decimal = Field(ge=100)
class CrashBetReq(BaseModel): amount: Decimal; auto: Optional[Decimal] = None
class ArenaJoinReq(BaseModel): bet: Decimal
class UpgradeReq(BaseModel): item_id: int; target_id: int

# ======================== AUTH ========================
async def get_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    tid = None
    init = request.headers.get("X-Telegram-Init-Data")
    if init and settings.BOT_TOKEN:
        parsed = {}
        for item in init.split("&"):
            if "=" in item: k, v = item.split("=", 1); parsed[k] = urllib.parse.unquote(v)
        if "hash" in parsed:
            received = parsed.pop("hash")
            check = "\n".join(sorted([f"{k}={v}" for k, v in parsed.items()]))
            secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
            if hmac.new(secret, check.encode(), hashlib.sha256).hexdigest() == received:
                try: tid = json.loads(parsed.get("user", "{}")).get("id")
                except: pass
    if not tid:
        tid = int(request.headers.get("X-Telegram-User-Id", 0))
    if not tid:
        raise HTTPException(401, "No auth")
    
    r = await db.execute(select(User).where(User.telegram_id == tid))
    u = r.scalar_one_or_none()
    if not u:
        u = User(telegram_id=tid, username=f"u_{tid}", first_name="Player",
                 is_admin=(tid == settings.ADMIN_TELEGRAM_ID))
        db.add(u); await db.commit(); await db.refresh(u)
    elif u.is_blocked:
        raise HTTPException(403, "Blocked")
    return u

async def get_admin(u: User = Depends(get_user)) -> User:
    if not u.is_admin: raise HTTPException(403)
    return u

# ======================== APP ========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    await seed_db()
    yield

app = FastAPI(title="CaseFight", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ======================== SEED ========================
async def seed_db():
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(Case).limit(1))).scalar_one_or_none(): return
        data = [
            ("🌟 Starter", 10, [("Деревянный Меч",5,0.25,"common"),("Кожаный Щит",6,0.20,"common"),("Кольцо Силы",10,0.15,"uncommon"),("Амулет",12,0.10,"uncommon"),("Шлем",15,0.07,"rare"),("Браслет",20,0.04,"epic"),("Меч Новичка",30,0.01,"legendary")]),
            ("🥉 Bronze", 50, [("Бронзовый Меч",25,0.22,"common"),("Бронзовый Щит",28,0.20,"common"),("Кольцо",35,0.18,"uncommon"),("Плащ",45,0.15,"uncommon"),("Амулет",60,0.12,"rare"),("Топор",75,0.08,"rare"),("Корона",100,0.04,"epic"),("Дракон",150,0.01,"legendary")]),
            ("🥈 Silver", 200, [("Серебряный Меч",100,0.20,"common"),("Щит",110,0.18,"common"),("Кольцо",140,0.16,"uncommon"),("Лук",180,0.15,"uncommon"),("Посох",240,0.12,"rare"),("Кольчуга",300,0.10,"rare"),("Тиара",400,0.07,"epic"),("Феникс",600,0.02,"legendary")]),
            ("🥇 Gold", 500, [("Золотой Меч",250,0.18,"common"),("Щит",280,0.17,"common"),("Кольцо",350,0.16,"uncommon"),("Посох",450,0.14,"uncommon"),("Доспехи",600,0.13,"rare"),("Клинок",750,0.10,"rare"),("Корона",1000,0.08,"epic"),("Дракон",1500,0.04,"legendary")]),
        ]
        for name, price, items in data:
            c = Case(name=name, price=Decimal(price)); db.add(c); await db.flush()
            for nm, vl, ch, rr in items: db.add(CaseItem(case_id=c.id, name=nm, value=Decimal(vl), chance=Decimal(str(ch)), rarity=rr))
        await db.commit()

# ======================== CRASH ========================
crash_game = None; crash_mult = Decimal("1.00"); crash_lock = asyncio.Lock()

async def crash_loop():
    global crash_game, crash_mult
    while True:
        async with crash_lock:
            async with AsyncSessionLocal() as db:
                pt = Decimal(str(round(max((Decimal("0.99")/(Decimal("1")-Decimal(str(random.random()))))*(Decimal("1")-settings.CRASH_EDGE), Decimal("1.01")), 2))) if random.random() > 0.01 else Decimal("1.00")
                g = CrashGame(point=pt); db.add(g); await db.commit()
                crash_game = g; crash_mult = Decimal("1.00")
                start = time.monotonic(); dur = min(10.0, max(2.0, float(pt)*0.2))
                while crash_mult < pt:
                    p = min((time.monotonic()-start)/dur, 1.0)
                    crash_mult = (Decimal("1.00")+(pt-Decimal("1.00"))*Decimal(str(p))).quantize(Decimal("0.01"))
                    await asyncio.sleep(0.15)
                    bets = (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==g.id, CrashBet.status=="active", CrashBet.auto_cashout.isnot(None), CrashBet.auto_cashout<=crash_mult)))).scalars().all()
                    for b in bets:
                        b.multiplier = crash_mult; b.profit = (b.amount*crash_mult)-b.amount; b.status = "won"
                        u = (await db.execute(select(User).where(User.id==b.user_id))).scalar_one_or_none()
                        if u: u.balance += b.amount + b.profit
                    if bets: await db.commit()
                for b in (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==g.id, CrashBet.status=="active")))).scalars().all():
                    b.status = "lost"; b.profit = -b.amount
                g.status = "done"; await db.commit()
                crash_game = None; crash_mult = Decimal("1.00")
        await asyncio.sleep(3)

@app.on_event("startup")
async def startup(): asyncio.create_task(crash_loop())

# ======================== API ========================
@app.get("/health")
async def health(): return {"ok":True}

@app.get("/auth/me", response_model=UserOut)
async def me(u: User=Depends(get_user)): return u

@app.get("/cases/", response_model=List[CaseOut])
async def cases(db: AsyncSession=Depends(get_db)):
    cs = (await db.execute(select(Case).where(Case.is_active==True))).scalars().all()
    return [CaseOut(id=c.id, name=c.name, price=c.price, type=c.type, items=[]) for c in cs]

@app.get("/cases/{cid}", response_model=CaseOut)
async def case_detail(cid: int, db: AsyncSession=Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id==cid))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    items = (await db.execute(select(CaseItem).where(CaseItem.case_id==cid))).scalars().all()
    return CaseOut(id=c.id, name=c.name, price=c.price, type=c.type, items=[ItemOut.model_validate(i) for i in items])

@app.post("/cases/open", response_model=OpenCaseResp)
async def open_case(req: OpenCaseReq, db: AsyncSession=Depends(get_db), u: User=Depends(get_user)):
    c = (await db.execute(select(Case).where(Case.id==req.case_id))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < c.price: raise HTTPException(400, "No balance")
    u.balance -= c.price
    items = (await db.execute(select(CaseItem).where(CaseItem.case_id==c.id))).scalars().all()
    total = sum(Decimal(str(i.chance)) for i in items)
    r = Decimal(str(random.uniform(0, float(total))))
    cum = Decimal("0"); sel = items[-1]
    for i in items:
        cum += Decimal(str(i.chance))
        if r <= cum: sel = i; break
    db.add(UserItem(user_id=u.id, name=sel.name, value=sel.value, rarity=sel.rarity))
    await db.commit(); await db.refresh(u)
    return OpenCaseResp(item=ItemOut.model_validate(sel), balance=u.balance)

@app.get("/inventory/")
async def inventory(u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    items = (await db.execute(select(UserItem).where(UserItem.user_id==u.id).order_by(UserItem.created_at.desc()).limit(50))).scalars().all()
    return [{"id":i.id, "name":i.name, "value":str(i.value), "rarity":i.rarity} for i in items]

@app.post("/inventory/sell", response_model=SellResp)
async def sell(req: SellReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    item = (await db.execute(select(UserItem).where(and_(UserItem.id==req.item_id, UserItem.user_id==u.id)))).scalar_one_or_none()
    if not item: raise HTTPException(404)
    price = (item.value * Decimal("0.8")).quantize(Decimal("0.01"))
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += price
    await db.delete(item); await db.commit(); await db.refresh(u)
    return SellResp(sold=price, balance=u.balance)

@app.post("/deposit")
async def deposit(req: DepositReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    gross = Decimal(req.amount) * settings.STARS_RATE
    fee = (gross * settings.DEPOSIT_FEE).quantize(Decimal("0.01"))
    net = gross - fee
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    u.balance += net; u.total_deposited += net
    await db.commit(); await db.refresh(u)
    return {"balance":str(u.balance), "received":str(net)}

@app.post("/withdraw")
async def withdraw(req: WithdrawReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if req.amount > u.balance: raise HTTPException(400)
    fee = (req.amount * settings.WITHDRAW_FEE).quantize(Decimal("0.01"))
    net = req.amount - fee
    u.balance -= req.amount
    db.add(Withdrawal(user_id=u.id, amount=req.amount, fee=fee, net=net))
    await db.commit(); await db.refresh(u)
    return {"balance":str(u.balance)}

@app.get("/crash/current")
async def crash_current():
    if crash_game: return {"multiplier":str(crash_mult), "status":"running"}
    return {"status":"waiting"}

@app.post("/crash/bet")
async def crash_bet(req: CrashBetReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    if not crash_game: raise HTTPException(400)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < req.amount: raise HTTPException(400)
    u.balance -= req.amount
    db.add(CrashBet(game_id=crash_game.id, user_id=u.id, amount=req.amount, auto_cashout=req.auto))
    await db.commit(); await db.refresh(u)
    return {"balance":str(u.balance)}

@app.post("/crash/cashout")
async def crash_cashout(u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    if not crash_game: raise HTTPException(400)
    async with crash_lock:
        bet = (await db.execute(select(CrashBet).where(and_(CrashBet.user_id==u.id, CrashBet.game_id==crash_game.id, CrashBet.status=="active")))).scalar_one_or_none()
        if not bet: raise HTTPException(404)
        profit = (bet.amount*crash_mult)-bet.amount; bet.multiplier=crash_mult; bet.profit=profit; bet.status="won"
        u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += bet.amount+profit
        await db.commit(); await db.refresh(u)
    return {"profit":str(profit), "balance":str(u.balance)}

@app.get("/arena/")
async def arena_list(db: AsyncSession=Depends(get_db)):
    rooms = (await db.execute(select(ArenaRoom).where(ArenaRoom.status=="waiting"))).scalars().all()
    res = []
    for r in rooms:
        cnt = (await db.execute(select(func.count(ArenaPlayer.id)).where(ArenaPlayer.room_id==r.id))).scalar()
        res.append({"id":r.id, "pot":str(r.pot), "players":cnt, "max":settings.ARENA_MAX})
    return res

@app.post("/arena/create")
async def arena_create(u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    r = ArenaRoom(creator_id=u.id); db.add(r); await db.commit(); await db.refresh(r)
    return {"id":r.id}

@app.post("/arena/join/{rid}")
async def arena_join(rid: int, req: ArenaJoinReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    r = (await db.execute(select(ArenaRoom).where(ArenaRoom.id==rid))).scalar_one_or_none()
    if not r or r.status!="waiting": raise HTTPException(400)
    cnt = (await db.execute(select(func.count(ArenaPlayer.id)).where(ArenaPlayer.room_id==rid))).scalar()
    if cnt >= settings.ARENA_MAX: raise HTTPException(400)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < req.bet: raise HTTPException(400)
    u.balance -= req.bet; r.pot += req.bet
    db.add(ArenaPlayer(room_id=rid, user_id=u.id, bet=req.bet))
    await db.commit()
    return {"ok":True}

@app.post("/arena/start/{rid}")
async def arena_start(rid: int, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    r = (await db.execute(select(ArenaRoom).where(ArenaRoom.id==rid))).scalar_one_or_none()
    if not r or r.creator_id!=u.id: raise HTTPException(403)
    players = (await db.execute(select(ArenaPlayer).where(ArenaPlayer.room_id==rid))).scalars().all()
    if len(players) < 2: raise HTTPException(400)
    total = r.pot
    for p in players: p.chance = (p.bet / total).quantize(Decimal("0.0001"))
    rand = Decimal(str(random.random())); cum = Decimal("0"); winner = players[-1]
    for p in players:
        cum += p.chance
        if rand <= cum: winner = p; break
    fee = (total * settings.ARENA_FEE).quantize(Decimal("0.01")); prize = total - fee
    r.winner_id = winner.user_id; r.status = "done"
    wu = (await db.execute(select(User).where(User.id==winner.user_id))).scalar_one()
    wu.balance += prize
    await db.commit()
    return {"winner":winner.user_id, "prize":str(prize)}

@app.post("/upgrade")
async def upgrade(req: UpgradeReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    a = (await db.execute(select(UserItem).where(and_(UserItem.id==req.item_id, UserItem.user_id==u.id)))).scalar_one_or_none()
    b = (await db.execute(select(UserItem).where(and_(UserItem.id==req.target_id, UserItem.user_id==u.id)))).scalar_one_or_none()
    if not a or not b: raise HTTPException(404)
    if b.value <= a.value: raise HTTPException(400)
    chance = min(max(a.value/b.value, Decimal("0.01")), Decimal("0.99"))
    ok = random.random() < float(chance)
    await db.delete(a)
    if ok: b.value = (b.value*Decimal("1.05")).quantize(Decimal("0.01"))
    else: await db.delete(b)
    await db.commit()
    return {"success":ok}

@app.get("/shop/")
async def shop(): return {"items":[{"id":"cooldown","name":"Снятие кулдауна","price":"500"},{"id":"odd","name":"Нечётные ставки","price":"250"}]}

@app.post("/shop/buy/{tid}")
async def shop_buy(tid: str, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    price = Decimal("500") if tid=="cooldown" else Decimal("250")
    if u.balance < price: raise HTTPException(400)
    u.balance -= price
    if tid=="cooldown": u.cooldown_removed = True
    else: u.can_odd_bets = True
    await db.commit()
    return {"ok":True}

@app.get("/admin/withdrawals")
async def admin_wd(db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    wds = (await db.execute(select(Withdrawal).where(Withdrawal.status=="pending"))).scalars().all()
    return [{"id":w.id, "uid":w.user_id, "amount":str(w.amount), "net":str(w.net)} for w in wds]

@app.post("/admin/withdrawals/{wid}")
async def admin_wd_process(wid: int, data: dict, db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    w = (await db.execute(select(Withdrawal).where(Withdrawal.id==wid))).scalar_one_or_none()
    if not w or w.status!="pending": raise HTTPException(400)
    act = data.get("action")
    if act=="approve": w.status="approved"
    elif act=="reject":
        w.status="rejected"
        u = (await db.execute(select(User).where(User.id==w.user_id))).scalar_one()
        if u: u.balance += w.amount
    await db.commit()
    return {"ok":True}

# ======================== FRONTEND ========================
@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no"><title>CaseFight</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
body{background:#0a0e14;color:#e6e8ec;min-height:100vh;padding-bottom:56px}
header{background:#12161e;padding:8px 12px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}
.logo{font-size:15px;font-weight:800}.logo span{color:#f5a623}
.balance{background:#1a1f2b;padding:5px 10px;border-radius:14px;font-size:12px;font-weight:700}
main{padding:10px}
.mi{background:#1e2430;border-radius:10px;padding:14px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.mi:active{background:#252c38}
.cc{background:#1e2430;border-radius:10px;padding:12px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.cn{font-weight:600;font-size:13px}.cp{color:#f5a623;font-weight:700;font-size:13px}
.btn{background:linear-gradient(135deg,#f5a623,#f7c948);color:#000;border:none;padding:10px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;width:100%;margin-top:6px}
.btn:active{transform:scale(0.96)}.btn-s{padding:5px 10px;font-size:11px;width:auto}
input{width:100%;background:#1a1f2b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px;color:#e6e8ec;font-size:13px;margin-bottom:6px}
nav{position:fixed;bottom:0;left:0;right:0;background:#12161e;display:flex;justify-content:space-around;padding:6px 4px 8px;z-index:100}
.nb{background:none;border:none;color:#5c6370;font-size:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px}
.nb.active{color:#f5a623}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:200;display:flex;align-items:center;justify-content:center}
.mc{background:#12161e;border-radius:14px;padding:20px;width:90%;max-width:360px}
.toast{position:fixed;top:60px;right:10px;background:#1e2430;padding:10px;border-radius:8px;font-size:12px;z-index:300;border-left:3px solid #f5a623}
h2{font-size:15px;margin-bottom:8px}.back{background:none;border:none;color:#f5a623;font-size:12px;cursor:pointer;margin-bottom:8px}
</style></head>
<body>
<div id="toasts"></div>
<div id="app">
<header><div class="logo" onclick="nav('home')">⚔️ <span>CaseFight</span></div><div class="balance" onclick="nav('dep')">⭐ <span id="bal">0</span></div></header>
<main id="content"></main>
<nav>
<button class="nb active" data-p="home">🏠<span>Главная</span></button>
<button class="nb" data-p="cases">📦<span>Кейсы</span></button>
<button class="nb" data-p="crash">🐸<span>Crash</span></button>
<button class="nb" data-p="arena">⚔️<span>Арена</span></button>
<button class="nb" data-p="inv">🎒<span>Инвент</span></button>
</nav></div>
<div id="modal" class="modal" style="display:none" onclick="if(event.target===this)closeModal()"><div class="mc" id="modal-body"></div></div>

<script>
var A=window.location.origin,S={user:null,balance:0},H={};
if(window.Telegram?.WebApp){var tg=window.Telegram.WebApp;tg.ready();tg.expand();H['X-Telegram-Init-Data']=tg.initData}
nav('home');loadUser();

async function api(m,u,b){try{var o={method:m,headers:Object.assign({'Content-Type':'application/json'},H)};if(b)o.body=JSON.stringify(b);var r=await fetch(A+u,o);var d=await r.json();if(!r.ok)throw new Error(d.detail||'Error');return d}catch(e){return null}}
async function loadUser(){var u=await api('GET','/auth/me');if(u){S.user=u;S.balance=parseFloat(u.balance);document.getElementById('bal').textContent=fmt(S.balance)}}
function fmt(n){return parseFloat(n).toLocaleString('ru-RU',{maximumFractionDigits:2})}
function toast(m,e){var d=document.createElement('div');d.className='toast';if(e)d.style.borderLeftColor='#f44336';d.textContent=m;document.getElementById('toasts').appendChild(d);setTimeout(function(){d.remove()},2500)}
function showModal(h){document.getElementById('modal-body').innerHTML=h;document.getElementById('modal').style.display='flex'}
function closeModal(){document.getElementById('modal').style.display='none'}
function nav(p){document.querySelectorAll('.nb').forEach(function(b){b.classList.toggle('active',b.dataset.p===p)});render(p)}

async function render(p){
var c=document.getElementById('content');c.innerHTML='';
if(p!=='home')c.innerHTML='<button class="back" onclick="nav(\'home\')">← Назад</button>';
if(p==='home'){
var items=[{id:'cases',icon:'📦',name:'Кейсы'},{id:'crash',icon:'🐸',name:'Crash'},{id:'arena',icon:'⚔️',name:'Арена'},{id:'inv',icon:'🎒',name:'Инвентарь'},{id:'upgrade',icon:'⬆️',name:'Апгрейд'},{id:'shop',icon:'🛒',name:'Магазин'},{id:'dep',icon:'💳',name:'Баланс'}];
if(S.user?.is_admin)items.push({id:'admin',icon:'🔧',name:'Админ'});
c.innerHTML+='<div>'+items.map(function(i){return'<div class="mi" onclick="nav(\''+i.id+'\')"><div>'+i.icon+' '+i.name+'</div><div style="color:#8b92a0">→</div></div>'}).join('')+'</div>';
}
else if(p==='cases'){
c.innerHTML+='<h2>📦 Кейсы</h2><div id="list">Загрузка...</div>';
var cs=await api('GET','/cases/');
if(cs){document.getElementById('list').innerHTML=cs.map(function(cs){return'<div class="cc" onclick="openCase('+cs.id+')"><div class="cn">'+cs.name+'</div><div class="cp">⭐'+fmt(cs.price)+'</div></div>'}).join('')}
}
else if(p==='crash'){
c.innerHTML='<div style="text-align:center"><h2>🐸 Crash</h2><div style="font-size:50px;font-weight:900;color:#4caf50" id="cm">1.00x</div><div id="cs">Ожидание...</div><input id="cb" placeholder="Ставка" value="10"><input id="ca" placeholder="Автокэшаут" value="2.0"><button class="btn" onclick="crashBet()">Ставка</button><button class="btn" id="cc" onclick="crashCash()" style="display:none;background:#4caf50">Забрать</button></div>';
var iv=setInterval(async function(){var g=await api('GET','/crash/current');if(g&&g.status==='running'){var el=document.getElementById('cm');if(el)el.textContent=parseFloat(g.multiplier).toFixed(2)+'x';var s=document.getElementById('cs');if(s)s.textContent='🚀 Летит!'}},500);
window._crashIv=iv;
}
else if(p==='arena'){c.innerHTML+='<h2>⚔️ Арена</h2><div id="al">Загрузка...</div><button class="btn" onclick="arenaCreate()">Создать</button>';
var gs=await api('GET','/arena/');if(gs)document.getElementById('al').innerHTML=gs.length?gs.map(function(g){return'<div class="cc"><div>Арена #'+g.id+'</div><div>'+g.players+'/'+g.max+' ⭐'+fmt(g.pot)+'</div><button class="btn btn-s" onclick="arenaJoin('+g.id+')">Войти</button></div>'}).join(''):'Нет арен'}
else if(p==='inv'){c.innerHTML+='<h2>🎒 Инвентарь</h2><div id="il">Загрузка...</div>';
var it=await api('GET','/inventory/');if(it)document.getElementById('il').innerHTML=it.length?it.map(function(i){return'<div class="cc" onclick="sellItem('+i.id+')"><div>'+i.name+'</div><div>⭐'+fmt(i.value)+'</div></div>'}).join(''):'Пусто'}
else if(p==='upgrade'){c.innerHTML+='<h2>⬆️ Апгрейд</h2><div id="ul">Загрузка...</div>';
var it=await api('GET','/inventory/');if(it){document.getElementById('ul').innerHTML=it.map(function(i){return'<div class="cc" id="u_'+i.id+'" onclick="selUp('+i.id+')"><div>'+i.name+'</div><div>⭐'+fmt(i.value)+'</div></div>'}).join('');window._up=[];window._ui=it}}
else if(p==='shop'){c.innerHTML='<h2>🛒 Магазин</h2><div class="cc"><div>Снятие кулдауна</div><button class="btn btn-s" onclick="shopBuy(\'cooldown\')">⭐500</button></div><div class="cc"><div>Нечётные ставки</div><button class="btn btn-s" onclick="shopBuy(\'odd\')">⭐250</button></div>'}
else if(p==='dep'){c.innerHTML='<h2>💳 Баланс: ⭐'+fmt(S.balance)+'</h2><input id="da" placeholder="Сумма Stars" value="50"><button class="btn" onclick="deposit()">Пополнить</button><div style="margin-top:16px"><input id="wa" placeholder="Сумма вывода" value="100"><button class="btn" onclick="withdraw()">Вывести</button></div>'}
else if(p==='admin'){c.innerHTML='<h2>🔧 Админ</h2><div class="mi" onclick="adminW()">💸 Выводы</div>'}
if(window._crashIv&&p!=='crash'){clearInterval(window._crashIv);window._crashIv=null}
}

window.selUp=function(id){if(!window._up)window._up=[];if(window._up.length>=2)window._up=[];window._up.push(id);document.getElementById('u_'+id).style.border='2px solid #f5a623';if(window._up.length===2){var a=window._ui.find(function(i){return i.id===window._up[0]}),b=window._ui.find(function(i){return i.id===window._up[1]});showModal('<h3>Апгрейд?</h3><p>'+a.name+' → '+b.name+'</p><button class="btn" onclick="doUp()">Да</button>')}};
window.doUp=async function(){var r=await api('POST','/upgrade',{item_id:window._up[0],target_id:window._up[1]});if(r){toast(r.success?'Успех!':'Провал');closeModal();nav('inv')}};
window.openCase=async function(id){var c=await api('GET','/cases/'+id);if(c)showModal('<h3>'+c.name+'</h3><p>⭐'+fmt(c.price)+'</p><button class="btn" onclick="doOpen('+id+')">Открыть</button>')};
window.doOpen=async function(id){var r=await api('POST','/cases/open',{case_id:id});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);showModal('<h3>🎉 '+r.item.name+'!</h3><p>⭐'+fmt(r.item.value)+'</p><button class="btn" onclick="closeModal()">OK</button>')}};
window.sellItem=function(id){showModal('<h3>Продать?</h3><button class="btn" onclick="doSell('+id+')">Да</button>')};
window.doSell=async function(id){var r=await api('POST','/inventory/sell',{item_id:id});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('+'+fmt(r.sold));closeModal();nav('inv')}};
window.crashBet=async function(){var a=parseFloat(document.getElementById('cb').value);var r=await api('POST','/crash/bet',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);document.getElementById('cc').style.display='block'}};
window.crashCash=async function(){var r=await api('POST','/crash/cashout');if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);document.getElementById('cc').style.display='none';toast('+'+fmt(r.profit))}};
window.arenaCreate=async function(){var r=await api('POST','/arena/create');if(r){toast('Создана!');nav('arena')}};
window.arenaJoin=async function(id){var a=prompt('Ставка:');if(a){await api('POST','/arena/join/'+id,{bet:parseFloat(a)});toast('Вошли!')}};
window.shopBuy=async function(t){var r=await api('POST','/shop/buy/'+t);if(r){S.balance-=(t==='cooldown'?500:250);document.getElementById('bal').textContent=fmt(S.balance);toast('Куплено!')}};
window.deposit=async function(){var a=parseInt(document.getElementById('da').value);var r=await api('POST','/deposit',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('+'+fmt(r.received))}};
window.withdraw=async function(){var a=parseFloat(document.getElementById('wa').value);var r=await api('POST','/withdraw',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('Заявка создана')}};
window.adminW=async function(){var w=await api('GET','/admin/withdrawals');if(w)showModal('<h3>Выводы</h3>'+w.map(function(r){return'<div>#'+r.id+' User:'+r.uid+' ⭐'+fmt(r.amount)+' <button class="btn btn-s" onclick="adminWp('+r.id+',\'approve\')">✅</button></div>'}).join('')+'<button class="btn" onclick="closeModal()">Закрыть</button>')};
window.adminWp=async function(id,a){await api('POST','/admin/withdrawals/'+id,{action:a});toast('OK');adminW()};
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
