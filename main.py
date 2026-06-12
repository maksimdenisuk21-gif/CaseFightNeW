"""
CaseFight — Telegram Mini App
Full: 8 cases, Crash, Arena, Upgrade, Shop, Admin, WebSocket, Stars, Withdraw
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
    MIN_DEPOSIT: int = int(os.getenv("MIN_DEPOSIT_STARS", "50"))
    MIN_WITHDRAW: float = float(os.getenv("MIN_WITHDRAW", "100.0"))
    DEPOSIT_FEE: float = float(os.getenv("DEPOSIT_FEE_PERCENT", "5.0"))
    WITHDRAW_FEE: float = float(os.getenv("WITHDRAW_FEE_PERCENT", "5.0"))
    CRASH_EDGE: float = float(os.getenv("CRASH_HOUSE_EDGE", "0.05"))
    ARENA_FEE: float = float(os.getenv("ARENA_PLATFORM_FEE", "5.0"))
    ARENA_MAX: int = int(os.getenv("ARENA_MAX_PLAYERS", "5"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", os.urandom(32).hex())
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

settings = Settings()

# ======================== DATABASE ========================
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"): db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as s:
        try:
            yield s
            await s.commit()
        except:
            await s.rollback()
            raise

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
    can_odd_bets = Column(Boolean, default=False); cooldown_removed = Column(Boolean, default=False)
    cooldown_until = Column(DateTime(timezone=True))
    registered_at = Column(DateTime(timezone=True), server_default=func.now())

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True); name = Column(String(255)); description = Column(Text)
    price = Column(DECIMAL(15,2)); image_url = Column(Text); type = Column(String(50), default="stars")
    is_active = Column(Boolean, default=True); cooldown = Column(Integer, default=0)

class CaseItem(Base):
    __tablename__ = "case_items"
    id = Column(Integer, primary_key=True); case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    name = Column(String(255)); image_url = Column(Text); value = Column(DECIMAL(15,2))
    chance = Column(DECIMAL(6,4)); rarity = Column(String(50), default="common")

class UserItem(Base):
    __tablename__ = "user_items"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_item_id = Column(Integer); case_id = Column(Integer); case_name = Column(String(255))
    name = Column(String(255)); image_url = Column(Text); value = Column(DECIMAL(15,2)); rarity = Column(String(50))
    obtained_at = Column(DateTime(timezone=True), server_default=func.now())
    is_upgraded = Column(Boolean, default=False)

class CaseHistory(Base):
    __tablename__ = "case_history"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    case_name = Column(String(255)); item_name = Column(String(255)); item_value = Column(DECIMAL(15,2))
    item_rarity = Column(String(50)); opened_at = Column(DateTime(timezone=True), server_default=func.now())

class Deposit(Base):
    __tablename__ = "deposits"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); amount_stars = Column(Integer)
    received = Column(DECIMAL(15,2)); fee = Column(DECIMAL(15,2)); verified = Column(Boolean, default=False)

class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    amount = Column(DECIMAL(15,2)); fee = Column(DECIMAL(15,2)); net = Column(DECIMAL(15,2))
    status = Column(String(50), default="pending"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class CrashGame(Base):
    __tablename__ = "crash_games"
    id = Column(Integer, primary_key=True); point = Column(DECIMAL(10,4)); seed = Column(String(255))
    status = Column(String(50), default="active"); created_at = Column(DateTime(timezone=True), server_default=func.now())

class CrashBet(Base):
    __tablename__ = "crash_bets"
    id = Column(Integer, primary_key=True); game_id = Column(Integer, ForeignKey("crash_games.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount = Column(DECIMAL(15,2)); auto_cashout = Column(DECIMAL(10,4)); mult = Column(DECIMAL(10,4))
    profit = Column(DECIMAL(15,2)); status = Column(String(50), default="active")

class ArenaRoom(Base):
    __tablename__ = "arena_rooms"
    id = Column(Integer, primary_key=True); creator_id = Column(Integer); pot = Column(DECIMAL(15,2), default=Decimal("0.00"))
    status = Column(String(50), default="waiting"); winner_id = Column(Integer); fee = Column(DECIMAL(15,2), default=Decimal("0.00"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ArenaPlayer(Base):
    __tablename__ = "arena_players"
    id = Column(Integer, primary_key=True); room_id = Column(Integer, ForeignKey("arena_rooms.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer); bet = Column(DECIMAL(15,2)); chance = Column(DECIMAL(6,4)); result = Column(String(50))

class ChatMsg(Base):
    __tablename__ = "chat_msgs"
    id = Column(Integer, primary_key=True); user_id = Column(Integer); username = Column(String(255))
    message = Column(Text); created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

# ======================== SCHEMAS ========================
class UserOut(BaseModel):
    id: int; telegram_id: int; username: Optional[str]; first_name: Optional[str]
    balance: Decimal; is_admin: bool; can_odd_bets: bool; cooldown_removed: bool
    model_config = {"from_attributes": True}

class ItemOut(BaseModel): id: int; name: str; value: Decimal; chance: Decimal; rarity: str; model_config = {"from_attributes": True}
class CaseOut(BaseModel): id: int; name: str; price: Decimal; type: str; cooldown: int; items: List[ItemOut] = []; model_config = {"from_attributes": True}
class OpenReq(BaseModel): case_id: int
class OpenResp(BaseModel): item: ItemOut; balance: Decimal
class SellReq(BaseModel): item_id: int
class SellResp(BaseModel): sold: Decimal; balance: Decimal
class DepositReq(BaseModel): amount: int = Field(ge=50)
class WithdrawReq(BaseModel): amount: Decimal = Field(ge=100)
class CrashBetReq(BaseModel): amount: Decimal; auto: Optional[Decimal] = None
class ArenaJoinReq(BaseModel): bet: Decimal
class UpgradeReq(BaseModel): item_id: int; target_id: int
class AdminBalReq(BaseModel): user_id: int; amount: Decimal; operation: str

# ======================== AUTH ========================
def validate_init(init_data: str) -> Optional[Dict]:
    if not settings.BOT_TOKEN: return None
    parsed = {}
    for item in init_data.split("&"):
        if "=" in item: k, v = item.split("=", 1); parsed[k] = urllib.parse.unquote(v)
    if "hash" not in parsed: return None
    received = parsed.pop("hash")
    check_str = "\n".join(sorted([f"{k}={v}" for k, v in parsed.items()]))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    if hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest() != received: return None
    return parsed

async def get_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    tid = None
    init = request.headers.get("X-Telegram-Init-Data")
    if init:
        parsed = validate_init(init)
        if parsed:
            try: tid = json.loads(parsed.get("user", "{}")).get("id")
            except: pass
    if not tid:
        dev_id = request.headers.get("X-Telegram-User-Id")
        if dev_id: tid = int(dev_id)
    if not tid: raise HTTPException(401, "No auth")
    r = await db.execute(select(User).where(User.telegram_id == tid))
    u = r.scalar_one_or_none()
    if not u:
        u = User(telegram_id=tid, username=f"u_{tid}", first_name="Player", is_admin=(tid == settings.ADMIN_TELEGRAM_ID))
        db.add(u); await db.commit(); await db.refresh(u)
    elif u.is_blocked: raise HTTPException(403, "Blocked")
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
            total_ch = sum(ch for _,_,ch,_ in items)
            if abs(total_ch-1.0)>0.001: items = [(n,v,round(ch/total_ch,4),r) for n,v,ch,r in items]
            c = Case(name=name,description=desc,price=Decimal(price),type=ctype,cooldown=cd)
            db.add(c); await db.flush()
            for iname,val,ch,rar in items:
                db.add(CaseItem(case_id=c.id,name=iname,value=Decimal(val),chance=Decimal(str(ch)),rarity=rar))
        await db.commit()
        print(f"✅ Seeded {len(cases_data)} cases")

# ======================== CRASH ========================
crash_game = None; crash_mult = Decimal("1.00"); crash_lock = asyncio.Lock()

async def crash_loop():
    global crash_game, crash_mult
    while True:
        async with crash_lock:
            async with AsyncSessionLocal() as db:
                pt = Decimal("1.00")
                if random.random() > 0.01:
                    raw = (Decimal("0.99")/(Decimal("1")-Decimal(str(random.random()))))*(Decimal("1")-Decimal(str(settings.CRASH_EDGE)))
                    pt = Decimal(str(round(max(float(raw), 1.01), 2)))
                g = CrashGame(point=pt, seed=secrets.token_hex(16))
                db.add(g); await db.commit()
                crash_game = g; crash_mult = Decimal("1.00")
                start = time.monotonic(); dur = min(12.0, max(2.0, float(pt)*0.2))
                while crash_mult < pt:
                    p = min((time.monotonic()-start)/dur, 1.0)
                    crash_mult = (Decimal("1.00")+(pt-Decimal("1.00"))*Decimal(str(p))).quantize(Decimal("0.01"))
                    await asyncio.sleep(0.12)
                    bets = (await db.execute(select(CrashBet).where(and_(CrashBet.game_id==g.id, CrashBet.status=="active", CrashBet.auto_cashout.isnot(None), CrashBet.auto_cashout<=crash_mult)))).scalars().all()
                    for b in bets:
                        b.mult = crash_mult; b.profit = (b.amount*crash_mult)-b.amount; b.status = "won"
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
    cs = (await db.execute(select(Case).where(Case.is_active==True).order_by(Case.price))).scalars().all()
    ids = [c.id for c in cs]; all_items = (await db.execute(select(CaseItem).where(CaseItem.case_id.in_(ids)))).scalars().all()
    imap = {c.id:[] for c in cs}
    for i in all_items: imap[i.case_id].append(ItemOut.model_validate(i))
    return [CaseOut(id=c.id, name=c.name, price=c.price, type=c.type, cooldown=c.cooldown, items=imap[c.id]) for c in cs]

@app.get("/cases/{cid}", response_model=CaseOut)
async def case_detail(cid: int, db: AsyncSession=Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id==cid))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    items = (await db.execute(select(CaseItem).where(CaseItem.case_id==cid))).scalars().all()
    return CaseOut(id=c.id, name=c.name, price=c.price, type=c.type, cooldown=c.cooldown, items=[ItemOut.model_validate(i) for i in items])

@app.post("/cases/open", response_model=OpenResp)
async def open_case(req: OpenReq, db: AsyncSession=Depends(get_db), u: User=Depends(get_user)):
    c = (await db.execute(select(Case).where(Case.id==req.case_id))).scalar_one_or_none()
    if not c: raise HTTPException(404)
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if u.balance < c.price: raise HTTPException(400, "No balance")
    u.balance -= c.price
    items = (await db.execute(select(CaseItem).where(CaseItem.case_id==c.id))).scalars().all()
    total = float(sum(Decimal(str(i.chance)) for i in items))
    r = random.uniform(0, total); cum = 0.0; sel = items[-1]
    for i in items:
        cum += float(i.chance)
        if r <= cum: sel = i; break
    db.add(UserItem(user_id=u.id, case_item_id=sel.id, case_id=c.id, case_name=c.name, name=sel.name, value=sel.value, rarity=sel.rarity))
    db.add(CaseHistory(user_id=u.id, case_name=c.name, item_name=sel.name, item_value=sel.value, item_rarity=sel.rarity))
    await db.commit(); await db.refresh(u)
    return OpenResp(item=ItemOut.model_validate(sel), balance=u.balance)

@app.get("/inventory/")
async def inventory(u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    items = (await db.execute(select(UserItem).where(UserItem.user_id==u.id).order_by(UserItem.obtained_at.desc()).limit(50))).scalars().all()
    return [{"id":i.id, "name":i.name, "value":str(i.value), "rarity":i.rarity, "upgraded":i.is_upgraded} for i in items]

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
    gross = Decimal(req.amount) * Decimal(str(settings.STARS_RATE))
    fee = (gross * Decimal(str(settings.DEPOSIT_FEE)) / 100).quantize(Decimal("0.01"))
    net = gross - fee
    db.add(Deposit(user_id=u.id, amount_stars=req.amount, received=net, fee=fee, verified=True))
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    u.balance += net; u.total_deposited += net
    await db.commit(); await db.refresh(u)
    return {"balance":str(u.balance), "received":str(net)}

@app.post("/withdraw")
async def withdraw(req: WithdrawReq, u: User=Depends(get_user), db: AsyncSession=Depends(get_db)):
    u = (await db.execute(select(User).where(User.id==u.id))).scalar_one()
    if req.amount > u.balance: raise HTTPException(400)
    fee = (req.amount * Decimal(str(settings.WITHDRAW_FEE)) / 100).quantize(Decimal("0.01"))
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
        profit = (bet.amount*crash_mult)-bet.amount; bet.mult=crash_mult; bet.profit=profit; bet.status="won"
        u = (await db.execute(select(User).where(User.id==u.id))).scalar_one(); u.balance += bet.amount+profit
        await db.commit(); await db.refresh(u)
    return {"profit":str(profit), "multiplier":str(crash_mult), "balance":str(u.balance)}

@app.get("/crash/history")
async def crash_history(db: AsyncSession=Depends(get_db)):
    games = (await db.execute(select(CrashGame).order_by(CrashGame.created_at.desc()).limit(10))).scalars().all()
    return [{"id":g.id, "point":str(g.point)} for g in games]

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
    fee = (total * Decimal(str(settings.ARENA_FEE)) / 100).quantize(Decimal("0.01"))
    prize = total - fee
    r.winner_id = winner.user_id; r.status = "done"; r.fee = fee; winner.result = "win"
    for p in players:
        if p.id != winner.id: p.result = "lose"
    wu = (await db.execute(select(User).where(User.id==winner.user_id))).scalar_one(); wu.balance += prize
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
    if ok: b.value = (b.value*Decimal("1.05")).quantize(Decimal("0.01")); b.is_upgraded = True
    else: await db.delete(b)
    await db.commit()
    return {"success":ok}

@app.get("/shop/")
async def shop(): return {"items":[{"id":"cooldown","name":"🔥 Снятие кулдауна","price":"500"},{"id":"odd","name":"🎲 Нечётные ставки","price":"250"}]}

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

# Admin
@app.get("/admin/withdrawals")
async def admin_wd(db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    wds = (await db.execute(select(Withdrawal).where(Withdrawal.status=="pending"))).scalars().all()
    return [{"id":w.id, "uid":w.user_id, "amount":str(w.amount), "net":str(w.net)} for w in wds]

@app.post("/admin/withdrawals/{wid}")
async def admin_wd_process(wid: int, data: dict, db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    w = (await db.execute(select(Withdrawal).where(Withdrawal.id==wid))).scalar_one_or_none()
    if not w or w.status!="pending": raise HTTPException(400)
    if data.get("action")=="approve": w.status="approved"
    else:
        w.status="rejected"
        u = (await db.execute(select(User).where(User.id==w.user_id))).scalar_one()
        if u: u.balance += w.amount
    await db.commit()
    return {"ok":True}

@app.post("/admin/balance")
async def admin_balance(req: AdminBalReq, db: AsyncSession=Depends(get_db), admin: User=Depends(get_admin)):
    u = (await db.execute(select(User).where(User.id==req.user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404)
    amt = Decimal(str(req.amount))
    if req.operation=="add": u.balance += amt
    elif req.operation=="set": u.balance = amt
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
.logo{font-size:16px;font-weight:800}.logo span{color:#f5a623}
.bal{background:#1a1f2b;padding:6px 12px;border-radius:14px;font-size:12px;font-weight:700;cursor:pointer}
main{padding:10px}
.mi{background:#1e2430;border-radius:10px;padding:14px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.mi:active{background:#252c38}
.cc{background:#1e2430;border-radius:10px;padding:12px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.cn{font-weight:600;font-size:13px}.cp{color:#f5a623;font-weight:700;font-size:13px}
.btn{background:linear-gradient(135deg,#f5a623,#f7c948);color:#000;border:none;padding:10px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;width:100%;margin-top:6px}
.btn-s{padding:6px 12px;font-size:11px;width:auto}.btn-r{background:#f44336}
input,select{width:100%;background:#1a1f2b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:10px;color:#e6e8ec;font-size:13px;margin-bottom:6px}
nav{position:fixed;bottom:0;left:0;right:0;background:#12161e;display:flex;justify-content:space-around;padding:6px 4px 8px;z-index:100}
.nb{background:none;border:none;color:#5c6370;font-size:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px}
.nb.active{color:#f5a623}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:200;display:flex;align-items:center;justify-content:center}
.mc{background:#12161e;border-radius:14px;padding:20px;width:90%;max-width:360px;max-height:80vh;overflow-y:auto}
.toast{position:fixed;top:60px;right:10px;background:#1e2430;padding:10px;border-radius:8px;font-size:12px;z-index:300;border-left:3px solid #f5a623}
h2{font-size:15px;margin-bottom:8px}.back{background:none;border:none;color:#f5a623;font-size:12px;cursor:pointer;margin-bottom:8px}
</style></head>
<body>
<div id="toasts"></div>
<div id="app" style="display:none">
<header><div class="logo" onclick="nav('home')">⚔️ <span>CaseFight</span></div><div class="bal" onclick="nav('dep')">⭐ <span id="bal">0</span></div></header>
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
var A=window.location.origin,S={user:null,balance:0},H={},intervals=[];
if(window.Telegram?.WebApp){var tg=window.Telegram.WebApp;tg.ready();tg.expand();H['X-Telegram-Init-Data']=tg.initData}
document.getElementById('app').style.display='flex';
nav('home');loadUser();

async function api(m,u,b){try{var o={method:m,headers:Object.assign({'Content-Type':'application/json'},H)};if(b)o.body=JSON.stringify(b);var r=await fetch(A+u,o);var d=await r.json();if(!r.ok)throw new Error(d.detail||'Error');return d}catch(e){return null}}
async function loadUser(){var u=await api('GET','/auth/me');if(u){S.user=u;S.balance=parseFloat(u.balance);document.getElementById('bal').textContent=fmt(S.balance)}}
function fmt(n){return parseFloat(n||0).toLocaleString('ru-RU',{maximumFractionDigits:2})}
function toast(m,e){var d=document.createElement('div');d.className='toast';if(e)d.style.borderLeftColor='#f44336';d.textContent=m;document.getElementById('toasts').appendChild(d);setTimeout(function(){d.remove()},2500)}
function showModal(h){document.getElementById('modal-body').innerHTML=h;document.getElementById('modal').style.display='flex'}
function closeModal(){document.getElementById('modal').style.display='none'}
function nav(p){intervals.forEach(clearInterval);intervals=[];document.querySelectorAll('.nb').forEach(function(b){b.classList.toggle('active',b.dataset.p===p)});render(p)}

async function render(p){
var c=document.getElementById('content');c.innerHTML='';
if(p!=='home')c.innerHTML='<button class="back" onclick="nav(\'home\')">← Назад</button>';
if(p==='home'){
var items=[{id:'cases',icon:'📦',name:'Кейсы'},{id:'crash',icon:'🐸',name:'Crash'},{id:'arena',icon:'⚔️',name:'Арена'},{id:'inv',icon:'🎒',name:'Инвентарь'},{id:'upgrade',icon:'⬆️',name:'Апгрейд'},{id:'shop',icon:'🛒',name:'Магазин'},{id:'dep',icon:'💳',name:'Баланс'}];
if(S.user?.is_admin)items.push({id:'admin',icon:'🔧',name:'Админ-панель'});
c.innerHTML+='<div>'+items.map(function(i){return'<div class="mi" onclick="nav(\''+i.id+'\')"><div>'+i.icon+' '+i.name+'</div><div style="color:#8b92a0">→</div></div>'}).join('')+'</div>';
}
else if(p==='cases'){
c.innerHTML+='<h2>📦 Кейсы</h2><div id="list">Загрузка...</div>';
var cs=await api('GET','/cases/');
if(cs)document.getElementById('list').innerHTML=cs.map(function(cs){return'<div class="cc" onclick="openCase('+cs.id+')"><div><div class="cn">'+cs.name+'</div><div style="font-size:10px;color:#8b92a0">'+cs.items.length+' предметов</div></div><div class="cp">⭐'+fmt(cs.price)+'</div></div>'}).join('');
}
else if(p==='crash'){
c.innerHTML='<div style="text-align:center"><h2>🐸 Crash</h2><div style="font-size:55px;font-weight:900;color:#4caf50" id="cm">1.00x</div><div id="cs" style="font-size:12px;color:#8b92a0;margin-bottom:10px">Ожидание...</div><input id="cb" placeholder="Ставка" value="10"><input id="ca" placeholder="Автокэшаут (x)" value="2.0"><button class="btn" onclick="crashBet()">🎲 Поставить</button><button class="btn" id="cc" onclick="crashCash()" style="display:none;background:#4caf50">💰 Забрать</button></div>';
var iv=setInterval(async function(){var g=await api('GET','/crash/current');if(g&&g.status==='running'){var el=document.getElementById('cm');if(el)el.textContent=parseFloat(g.multiplier).toFixed(2)+'x';var s=document.getElementById('cs');if(s)s.textContent='🚀 Летит!'}else{var s=document.getElementById('cs');if(s)s.textContent='Ожидание...'}},500);
intervals.push(iv);
}
else if(p==='arena'){
c.innerHTML+='<h2>⚔️ Арена</h2><div id="al">Загрузка...</div><button class="btn" onclick="arenaCreate()">➕ Создать арену</button>';
var gs=await api('GET','/arena/');
if(gs)document.getElementById('al').innerHTML=gs.length?gs.map(function(g){return'<div class="cc"><div><div class="cn">Арена #'+g.id+'</div><div style="font-size:10px;color:#8b92a0">Игроков: '+g.players+'/'+g.max+'</div></div><div style="text-align:right"><div class="cp">⭐'+fmt(g.pot)+'</div><button class="btn btn-s" onclick="arenaJoin('+g.id+')">Войти</button></div></div>'}).join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Нет активных арен</div>';
}
else if(p==='inv'){
c.innerHTML+='<h2>🎒 Инвентарь</h2><div id="il">Загрузка...</div>';
var it=await api('GET','/inventory/');
if(it)document.getElementById('il').innerHTML=it.length?it.map(function(i){return'<div class="cc" onclick="sellItem('+i.id+')"><div><div class="cn">'+i.name+'</div><div style="font-size:10px;color:#8b92a0">'+i.rarity+(i.upgraded?' ⬆️':'')+'</div></div><div class="cp">⭐'+fmt(i.value)+'</div></div>'}).join(''):'<div style="text-align:center;color:#8b92a0;padding:20px">Инвентарь пуст</div>';
}
else if(p==='upgrade'){
c.innerHTML+='<h2>⬆️ Апгрейд</h2><p style="font-size:11px;color:#8b92a0;margin-bottom:8px">Выберите 2 предмета</p><div id="ul">Загрузка...</div>';
var it=await api('GET','/inventory/');
if(it){document.getElementById('ul').innerHTML=it.map(function(i){return'<div class="cc" id="u_'+i.id+'" onclick="selUp('+i.id+')"><div class="cn">'+i.name+'</div><div class="cp">⭐'+fmt(i.value)+'</div></div>'}).join('');window._up=[];window._ui=it}
}
else if(p==='shop'){
c.innerHTML='<h2>🛒 Магазин</h2>';
var s=await api('GET','/shop/');
if(s)c.innerHTML+=s.items.map(function(i){return'<div class="cc"><div class="cn">'+i.name+'</div><button class="btn btn-s" onclick="shopBuy(\''+i.id+'\')">⭐'+i.price+'</button></div>'}).join('');
}
else if(p==='dep'){
c.innerHTML='<h2>💳 Баланс: ⭐'+fmt(S.balance)+'</h2><input type="number" id="da" placeholder="Сумма Stars (мин 50)" value="50"><button class="btn" onclick="deposit()">⭐ Пополнить</button><div style="margin-top:16px"><input type="number" id="wa" placeholder="Сумма вывода (мин 100)" value="100"><button class="btn" onclick="withdraw()">💸 Вывести</button></div>';
}
else if(p==='admin'){
c.innerHTML='<h2>🔧 Админ-панель</h2><div class="mi" onclick="adminW()"><div>💸 Заявки на вывод</div><div style="color:#8b92a0">→</div></div><div class="mi" onclick="showAdminBal()"><div>💰 Изменить баланс</div><div style="color:#8b92a0">→</div></div>';
}}

window.selUp=function(id){if(!window._up)window._up=[];if(window._up.length>=2)window._up=[];window._up.push(id);document.getElementById('u_'+id).style.border='2px solid #f5a623';if(window._up.length===2){var a=window._ui.find(function(i){return i.id===window._up[0]}),b=window._ui.find(function(i){return i.id===window._up[1]});showModal('<h3>Апгрейд?</h3><p>'+a.name+' → '+b.name+'</p><p style="color:#ff9800;font-size:12px">⚠️ При неудаче оба уничтожаются!</p><button class="btn" onclick="doUp()">Подтвердить</button>')}};
window.doUp=async function(){var r=await api('POST','/upgrade',{item_id:window._up[0],target_id:window._up[1]});if(r){toast(r.success?'🎉 Успех!':'💔 Провал!');closeModal();nav('inv')}};
window.openCase=async function(id){var c=await api('GET','/cases/'+id);if(c)showModal('<h3>'+c.name+'</h3><p>Цена: ⭐'+fmt(c.price)+'</p><p style="font-size:11px;color:#8b92a0">'+c.items.length+' предметов</p><button class="btn" onclick="doOpen('+id+')">🎲 Открыть</button>')};
window.doOpen=async function(id){var r=await api('POST','/cases/open',{case_id:id});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);showModal('<h3>🎉 '+r.item.name+'!</h3><p>Стоимость: ⭐'+fmt(r.item.value)+'</p><p style="font-weight:700;text-transform:uppercase;color:#f5a623">'+r.item.rarity+'</p><button class="btn" onclick="closeModal()">OK</button>')}};
window.sellItem=function(id){showModal('<h3>Продать?</h3><p>Цена: 80% от стоимости</p><button class="btn" onclick="doSell('+id+')">💰 Продать</button>')};
window.doSell=async function(id){var r=await api('POST','/inventory/sell',{item_id:id});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('Продано за ⭐'+fmt(r.sold));closeModal();nav('inv')}};
window.crashBet=async function(){var a=parseFloat(document.getElementById('cb').value);var r=await api('POST','/crash/bet',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);document.getElementById('cc').style.display='block';toast('Ставка принята!')}};
window.crashCash=async function(){var r=await api('POST','/crash/cashout');if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);document.getElementById('cc').style.display='none';toast('+'+fmt(r.profit)+' ('+parseFloat(r.multiplier).toFixed(2)+'x)')}};
window.arenaCreate=async function(){var r=await api('POST','/arena/create');if(r){toast('Арена #'+r.id+' создана!');nav('arena')}};
window.arenaJoin=async function(id){var a=prompt('Сумма ставки:');if(a){var r=await api('POST','/arena/join/'+id,{bet:parseFloat(a)});if(r){S.balance-=parseFloat(a);document.getElementById('bal').textContent=fmt(S.balance);toast('Вы в игре!');nav('arena')}}};
window.shopBuy=async function(t){var r=await api('POST','/shop/buy/'+t);if(r){S.balance-=(t==='cooldown'?500:250);document.getElementById('bal').textContent=fmt(S.balance);toast('Куплено!')}};
window.deposit=async function(){var a=parseInt(document.getElementById('da').value);if(a<50){toast('Минимум 50',1);return}var r=await api('POST','/deposit',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('Пополнено на ⭐'+fmt(r.received))}};
window.withdraw=async function(){var a=parseFloat(document.getElementById('wa').value);if(a<100){toast('Минимум 100',1);return}var r=await api('POST','/withdraw',{amount:a});if(r){S.balance=parseFloat(r.balance);document.getElementById('bal').textContent=fmt(S.balance);toast('Заявка создана')}};
window.adminW=async function(){var w=await api('GET','/admin/withdrawals');if(w)showModal('<h3>💸 Заявки на вывод</h3>'+w.map(function(r){return'<div class="cc" style="margin:4px 0"><div><div class="cn">#'+r.id+' | User: '+r.uid+'</div><div style="font-size:10px;color:#8b92a0">⭐'+fmt(r.amount)+' → '+fmt(r.net)+'</div></div><div style="display:flex;gap:4px"><button class="btn btn-s" onclick="adminWp('+r.id+',\'approve\')">✅</button><button class="btn btn-s btn-r" onclick="adminWp('+r.id+',\'reject\')">❌</button></div></div>'}).join('')+'<button class="btn" onclick="closeModal()" style="margin-top:8px">Закрыть</button>')};
window.adminWp=async function(id,a){await api('POST','/admin/withdrawals/'+id,{action:a});toast(a==='approve'?'Одобрено':'Отклонено');adminW()};
window.showAdminBal=function(){showModal('<h3>💰 Изменить баланс</h3><input id="ab_uid" placeholder="ID пользователя"><input id="ab_amt" placeholder="Сумма"><select id="ab_op"><option value="add">Добавить</option><option value="set">Установить</option></select><button class="btn" onclick="adminBal()">Применить</button>')};
window.adminBal=async function(){var r=await api('POST','/admin/balance',{user_id:parseInt(document.getElementById('ab_uid').value),amount:parseFloat(document.getElementById('ab_amt').value),operation:document.getElementById('ab_op').value});if(r){toast('Готово!');closeModal()}};
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn, os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
