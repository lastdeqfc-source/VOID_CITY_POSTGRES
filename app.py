import os
import json
import time
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEVELOPER = os.getenv("DEVELOPER", "@hiddenvoicer")
APP_URL = os.getenv("APP_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="VOID CITY")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT NOT NULL DEFAULT '',
    coins BIGINT NOT NULL DEFAULT 500,
    xp BIGINT NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS buildings (
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    stored BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, kind)
);

CREATE TABLE IF NOT EXISTS inventory (
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item TEXT NOT NULL,
    amount BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, item)
);

CREATE TABLE IF NOT EXISTS quests (
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    claimed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (player_id, key)
);

CREATE TABLE IF NOT EXISTS achievements (
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    PRIMARY KEY (player_id, key)
);

CREATE TABLE IF NOT EXISTS daily (
    player_id BIGINT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    last_claim TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_players_last_seen ON players(last_seen);
"""

BUILDINGS = ("district", "factory", "lab")
START_COINS = 500

def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_db():
    with db() as conn:
        conn.execute(SCHEMA)
        conn.commit()

@app.on_event("startup")
def startup():
    init_db()

def valid_init_data(raw: str):
    if not raw or not BOT_TOKEN:
        return None
    try:
        pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True)
        data = dict(pairs)
        received_hash = data.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        auth_date = int(data.get("auth_date", "0"))
        if time.time() - auth_date > 86400:
            return None
        user = json.loads(data["user"])
        return user
    except Exception:
        return None

def get_player(request: Request):
    user = valid_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        raise HTTPException(401, "Telegram session is not valid")
    return user

def ensure_player(user):
    pid = int(user["id"])
    with db() as conn:
        row = conn.execute("SELECT * FROM players WHERE id=%s", (pid,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO players(id, username, first_name) VALUES(%s,%s,%s)",
                (pid, user.get("username"), user.get("first_name", "")),
            )
            for kind in BUILDINGS:
                conn.execute(
                    "INSERT INTO buildings(player_id,kind) VALUES(%s,%s)",
                    (pid, kind),
                )
            for key in ("collect", "upgrade"):
                conn.execute(
                    "INSERT INTO quests(player_id,key) VALUES(%s,%s)",
                    (pid, key),
                )
            conn.execute(
                "INSERT INTO inventory(player_id,item,amount) VALUES(%s,'energy',10)",
                (pid,),
            )
        else:
            conn.execute(
                "UPDATE players SET username=%s, first_name=%s, last_seen=NOW() WHERE id=%s",
                (user.get("username"), user.get("first_name", ""), pid),
            )
        conn.commit()
        return conn.execute("SELECT * FROM players WHERE id=%s", (pid,)).fetchone()

@app.get("/")
def home():
    return FileResponse("web/index.html", headers={"Cache-Control": "no-cache"})

@app.get("/health")
def health():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"ok": True, "database": "postgresql"}

@app.get("/api/me")
def me(request: Request):
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        buildings = conn.execute(
            "SELECT kind,level,stored FROM buildings WHERE player_id=%s ORDER BY kind",
            (p["id"],)
        ).fetchall()
        inventory = conn.execute(
            "SELECT item,amount FROM inventory WHERE player_id=%s ORDER BY item",
            (p["id"],)
        ).fetchall()
        achievements = conn.execute(
            "SELECT key FROM achievements WHERE player_id=%s ORDER BY key",
            (p["id"],)
        ).fetchall()
        quests = conn.execute(
            "SELECT key,progress,claimed FROM quests WHERE player_id=%s ORDER BY key",
            (p["id"],)
        ).fetchall()
    return {
        "player": dict(p),
        "buildings": [dict(x) for x in buildings],
        "inventory": [dict(x) for x in inventory],
        "achievements": [x["key"] for x in achievements],
        "quests": [dict(x) for x in quests],
        "developer": DEVELOPER,
        "storage": "PostgreSQL",
    }

@app.post("/api/daily")
def daily(request: Request):
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        row = conn.execute("SELECT last_claim FROM daily WHERE player_id=%s", (p["id"],)).fetchone()
        now = datetime.now(timezone.utc)
        if row and row["last_claim"] and (now - row["last_claim"]).total_seconds() < 86400:
            return {"ok": False, "message": "Ежедневная награда уже получена"}
        conn.execute(
            """INSERT INTO daily(player_id,last_claim) VALUES(%s,%s)
               ON CONFLICT(player_id) DO UPDATE SET last_claim=EXCLUDED.last_claim""",
            (p["id"], now)
        )
        conn.execute("UPDATE players SET coins=coins+100, xp=xp+20 WHERE id=%s", (p["id"],))
        conn.commit()
    return {"ok": True, "reward": 100}

@app.post("/api/collect/{kind}")
def collect(kind: str, request: Request):
    if kind not in BUILDINGS:
        raise HTTPException(404, "Unknown building")
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        b = conn.execute(
            "SELECT level FROM buildings WHERE player_id=%s AND kind=%s FOR UPDATE",
            (p["id"], kind)
        ).fetchone()
        amount = max(10, b["level"] * 15)
        conn.execute(
            "UPDATE players SET coins=coins+%s, xp=xp+5 WHERE id=%s",
            (amount, p["id"])
        )
        conn.execute(
            "UPDATE buildings SET stored=0 WHERE player_id=%s AND kind=%s",
            (p["id"], kind)
        )
        conn.execute(
            "UPDATE quests SET progress=LEAST(progress+1,10)
             WHERE player_id=%s AND key='collect'",
            (p["id"],)
        )
        conn.commit()
    return {"ok": True, "coins": amount}

@app.post("/api/upgrade/{kind}")
def upgrade(kind: str, request: Request):
    if kind not in BUILDINGS:
        raise HTTPException(404, "Unknown building")
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        b = conn.execute(
            "SELECT level FROM buildings WHERE player_id=%s AND kind=%s FOR UPDATE",
            (p["id"], kind)
        ).fetchone()
        cost = 150 * b["level"]
        if p["coins"] < cost:
            return JSONResponse({"ok": False, "message": "Недостаточно монет"}, status_code=400)
        conn.execute("UPDATE players SET coins=coins-%s, xp=xp+15 WHERE id=%s", (cost,p["id"]))
        conn.execute(
            "UPDATE buildings SET level=level+1 WHERE player_id=%s AND kind=%s",
            (p["id"], kind)
        )
        conn.execute(
            "UPDATE quests SET progress=LEAST(progress+1,5)
             WHERE player_id=%s AND key='upgrade'",
            (p["id"],)
        )
        conn.commit()
    return {"ok": True, "cost": cost}

@app.post("/api/quest/{key}/claim")
def claim_quest(key: str, request: Request):
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        q = conn.execute(
            "SELECT progress,claimed FROM quests WHERE player_id=%s AND key=%s FOR UPDATE",
            (p["id"], key)
        ).fetchone()
        if not q:
            raise HTTPException(404, "Quest not found")
        target = 10 if key == "collect" else 5
        if q["claimed"] or q["progress"] < target:
            return {"ok": False, "message": "Задание ещё не выполнено"}
        conn.execute(
            "UPDATE quests SET claimed=TRUE WHERE player_id=%s AND key=%s",
            (p["id"], key)
        )
        conn.execute("UPDATE players SET coins=coins+200, xp=xp+50 WHERE id=%s", (p["id"],))
        conn.commit()
    return {"ok": True, "reward": 200}

@app.get("/api/news")
def news(request: Request):
    get_player(request)
    return {
        "items": [
            {"title": "Первый сигнал", "text": "В городе появился неизвестный сигнал."},
            {"title": "Новый район", "text": "Развивай здания и собирай ресурсы."},
        ]
    }

@app.get("/api/meta")
def meta(request: Request):
    get_player(request)
    return {
        "name": "VOID CITY",
        "developer": DEVELOPER,
        "app_url": APP_URL,
        "storage": "PostgreSQL",
        "real_money": False,
        "real_gambling": False,
    }

@app.post("/api/pvp/training")
def pvp_training(request: Request):
    u = get_player(request)
    p = ensure_player(u)
    with db() as conn:
        conn.execute("UPDATE players SET xp=xp+10 WHERE id=%s", (p["id"],))
        conn.commit()
    return {"ok": True, "message": "Тренировочный бой завершён", "reward_xp": 10}

@app.post("/api/support")
def support(request: Request):
    get_player(request)
    return {"ok": False, "message": "Реальные платежи отключены. VOID CITY использует только виртуальную валюту."}
