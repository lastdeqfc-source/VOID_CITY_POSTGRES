import os
import json
import hmac
import hashlib
from urllib.parse import parse_qsl
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DEVELOPER = os.getenv("DEVELOPER", "@hiddenvoicer")

app = FastAPI(title="VOID CITY")
app.mount("/web", StaticFiles(directory="web"), name="web")


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS players(
            id SERIAL PRIMARY KEY,
            tg_id BIGINT UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            name TEXT DEFAULT 'Игрок',
            avatar TEXT DEFAULT '🌑',
            coins INTEGER DEFAULT 500,
            energy INTEGER DEFAULT 100,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            last_daily TEXT,
            district TEXT DEFAULT 'Центр',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS buildings(
            player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
            kind TEXT,
            level INTEGER DEFAULT 1,
            stored INTEGER DEFAULT 0,
            PRIMARY KEY(player_id, kind)
        );

        CREATE TABLE IF NOT EXISTS inventory(
            player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
            item TEXT,
            amount INTEGER DEFAULT 0,
            PRIMARY KEY(player_id, item)
        );

        CREATE TABLE IF NOT EXISTS achievements(
            player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
            key TEXT,
            unlocked_at TEXT,
            PRIMARY KEY(player_id, key)
        );

        CREATE TABLE IF NOT EXISTS quests(
            player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
            key TEXT,
            progress INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            PRIMARY KEY(player_id, key)
        );

        CREATE TABLE IF NOT EXISTS clans(
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            owner_id INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS clan_members(
            clan_id INTEGER REFERENCES clans(id) ON DELETE CASCADE,
            player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
            PRIMARY KEY(clan_id, player_id)
        );

        CREATE INDEX IF NOT EXISTS idx_players_level
        ON players(level DESC, xp DESC);

        CREATE INDEX IF NOT EXISTS idx_buildings_player
        ON buildings(player_id);

        CREATE INDEX IF NOT EXISTS idx_inventory_player
        ON inventory(player_id);

        CREATE INDEX IF NOT EXISTS idx_quests_player
        ON quests(player_id);
        """)


init()


def valid_init_data(raw):
    if not raw or not BOT_TOKEN:
        return None

    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
        received = pairs.pop("hash", None)

        if not received:
            return None

        check = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs)
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated = hmac.new(
            secret,
            check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received):
            return None

        return json.loads(pairs.get("user", "{}"))

    except Exception:
        return None


def get_player(request: Request):
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = valid_init_data(raw)

    if user:
        return user

    if os.getenv("DEMO_MODE") == "1":
        return {
            "id": 999999,
            "first_name": "Demo",
            "username": "demo"
        }

    raise HTTPException(
        status_code=401,
        detail="Telegram session is not valid"
    )


def ensure_player(user):
    with db() as c:
        player = c.execute(
            "SELECT * FROM players WHERE tg_id=%s",
            (user["id"],)
        ).fetchone()

        if player:
            return player

        name = (user.get("first_name") or "Игрок")[:32]

        c.execute("""
            INSERT INTO players
            (tg_id, username, name, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tg_id) DO NOTHING
        """, (
            user["id"],
            user.get("username", ""),
            name,
            datetime.now(timezone.utc).isoformat()
        ))

        player = c.execute(
            "SELECT * FROM players WHERE tg_id=%s",
            (user["id"],)
        ).fetchone()

        for kind in ("factory", "lab", "market"):
            c.execute("""
                INSERT INTO buildings(player_id, kind)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (player["id"], kind))

        for item in ("metal", "energy", "data", "chips"):
            c.execute("""
                INSERT INTO inventory(player_id, item, amount)
                VALUES (%s, %s, 0)
                ON CONFLICT DO NOTHING
            """, (player["id"], item))

        return player


def xp_for(level):
    return 100 + (level - 1) * 75


def add_xp(c, player_id, amount):
    player = c.execute(
        "SELECT xp, level FROM players WHERE id=%s",
        (player_id,)
    ).fetchone()

    xp = player["xp"] + amount
    level = player["level"]

    while xp >= xp_for(level):
        xp -= xp_for(level)
        level += 1

    c.execute("""
        UPDATE players
        SET xp=%s, level=%s
        WHERE id=%s
    """, (xp, level, player_id))


@app.get("/")
def home():
    return FileResponse(
        "web/index.html",
        headers={"Cache-Control": "no-cache"}
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    with db() as c:
        buildings = c.execute("""
            SELECT kind, level, stored
            FROM buildings
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

        inventory = c.execute("""
            SELECT item, amount
            FROM inventory
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

        achievements = c.execute("""
            SELECT key
            FROM achievements
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

        quests = c.execute("""
            SELECT key, progress, claimed
            FROM quests
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

        clan = c.execute("""
            SELECT cl.name
            FROM clans cl
            JOIN clan_members cm ON cm.clan_id=cl.id
            WHERE cm.player_id=%s
        """, (player["id"],)).fetchone()

    return {
        "player": player,
        "buildings": buildings,
        "inventory": inventory,
        "achievements": [x["key"] for x in achievements],
        "quests": quests,
        "clan": clan["name"] if clan else None,
        "developer": DEVELOPER
    }


@app.post("/api/profile")
async def profile(request: Request):
    user = get_player(request)
    player = ensure_player(user)
    data = await request.json()

    name = str(
        data.get("name", player["name"])
    )[:32].strip() or player["name"]

    avatar = str(
        data.get("avatar", player["avatar"])
    )[:8]

    with db() as c:
        c.execute("""
            UPDATE players
            SET name=%s, avatar=%s
            WHERE id=%s
        """, (name, avatar, player["id"]))

    return {"ok": True}


@app.post("/api/daily")
def daily(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    if player["last_daily"] == today:
        return {
            "ok": False,
            "message": "Награда уже получена"
        }

    with db() as c:
        streak = (player["streak"] or 0) + 1
        reward = 100 + min(streak, 7) * 50

        c.execute("""
            UPDATE players
            SET coins=coins+%s,
                streak=%s,
                last_daily=%s
            WHERE id=%s
        """, (
            reward,
            streak,
            today,
            player["id"]
        ))

        add_xp(c, player["id"], 30)

    return {
        "ok": True,
        "reward": reward,
        "streak": streak
    }


@app.post("/api/collect")
def collect(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    with db() as c:
        rows = c.execute("""
            SELECT kind, level, stored
            FROM buildings
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

        total = 0

        for row in rows:
            amount = row["stored"] + row["level"] * 8
            total += amount

            c.execute("""
                UPDATE buildings
                SET stored=0
                WHERE player_id=%s AND kind=%s
            """, (
                player["id"],
                row["kind"]
            ))

        c.execute("""
            UPDATE players
            SET coins=coins+%s
            WHERE id=%s
        """, (total, player["id"]))

        add_xp(c, player["id"], total // 5)

    return {
        "ok": True,
        "coins": total
    }


@app.post("/api/upgrade/{kind}")
def upgrade(kind: str, request: Request):
    if kind not in ("factory", "lab", "market"):
        raise HTTPException(
            400,
            "Unknown building"
        )

    user = get_player(request)
    player = ensure_player(user)

    with db() as c:
        building = c.execute("""
            SELECT *
            FROM buildings
            WHERE player_id=%s AND kind=%s
        """, (
            player["id"],
            kind
        )).fetchone()

        if not building:
            raise HTTPException(
                404,
                "Building not found"
            )

        cost = 150 * building["level"]

        if player["coins"] < cost:
            raise HTTPException(
                400,
                "Недостаточно монет"
            )

        c.execute("""
            UPDATE players
            SET coins=coins-%s
            WHERE id=%s
        """, (cost, player["id"]))

        c.execute("""
            UPDATE buildings
            SET level=level+1
            WHERE player_id=%s AND kind=%s
        """, (
            player["id"],
            kind
        ))

        add_xp(c, player["id"], 50)

    return {
        "ok": True,
        "cost": cost
    }


@app.get("/api/leaderboard")
def leaderboard(request: Request):
    get_player(request)

    with db() as c:
        rows = c.execute("""
            SELECT name, avatar, level, xp, coins, district
            FROM players
            ORDER BY level DESC, xp DESC
            LIMIT 20
        """).fetchall()

    return rows


@app.get("/api/quests")
def quests(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    definitions = [
        ("login", "Забрать ежедневную награду", 1, 80),
        ("collector", "Собрать производство", 1, 120),
        ("builder", "Улучшить здание", 1, 150)
    ]

    with db() as c:
        for key, title, target, reward in definitions:
            c.execute("""
                INSERT INTO quests(player_id, key, progress)
                VALUES (%s, %s, 0)
                ON CONFLICT DO NOTHING
            """, (player["id"], key))

        rows = c.execute("""
            SELECT key, progress, claimed
            FROM quests
            WHERE player_id=%s
        """, (player["id"],)).fetchall()

    result = []

    for row in rows:
        definition = next(
            x for x in definitions
            if x[0] == row["key"]
        )

        result.append({
            "key": row["key"],
            "progress": row["progress"],
            "claimed": row["claimed"],
            "title": definition[1],
            "target": definition[2],
            "reward": definition[3]
        })

    return result


@app.post("/api/quest/{key}/claim")
def claim_quest(key: str, request: Request):
    user = get_player(request)
    player = ensure_player(user)

    definitions = {
        "login": (1, 80),
        "collector": (1, 120),
        "builder": (1, 150)
    }

    if key not in definitions:
        raise HTTPException(
            404,
            "Quest not found"
        )

    target, reward = definitions[key]

    with db() as c:
        quest = c.execute("""
            SELECT *
            FROM quests
            WHERE player_id=%s AND key=%s
        """, (
            player["id"],
            key
        )).fetchone()

        if (
            not quest
            or quest["progress"] < target
            or quest["claimed"]
        ):
            raise HTTPException(
                400,
                "Квест ещё не выполнен"
            )

        c.execute("""
            UPDATE quests
            SET claimed=1
            WHERE player_id=%s AND key=%s
        """, (
            player["id"],
            key
        ))

        c.execute("""
            UPDATE players
            SET coins=coins+%s
            WHERE id=%s
        """, (
            reward,
            player["id"]
        ))

        add_xp(c, player["id"], 40)

    return {
        "ok": True,
        "reward": reward
    }


@app.post("/api/clan/create")
async def clan_create(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    data = await request.json()
    name = str(data.get("name", "")).strip()[:24]

    if len(name) < 3:
        raise HTTPException(
            400,
            "Название слишком короткое"
        )

    try:
        with db() as c:
            result = c.execute("""
                INSERT INTO clans
                (name, owner_id, created_at)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (
                name,
                player["id"],
                datetime.now(timezone.utc).isoformat()
            )).fetchone()

            c.execute("""
                INSERT INTO clan_members
                (clan_id, player_id)
                VALUES (%s, %s)
            """, (
                result["id"],
                player["id"]
            ))

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            400,
            "Такой клан уже существует"
        )

    return {
        "ok": True,
        "name": name
    }


@app.post("/api/clan/join")
async def clan_join(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    data = await request.json()
    name = str(
        data.get("name", "")
    ).strip()

    with db() as c:
        clan = c.execute("""
            SELECT *
            FROM clans
            WHERE name=%s
        """, (name,)).fetchone()

        if not clan:
            raise HTTPException(
                404,
                "Клан не найден"
            )

        c.execute("""
            INSERT INTO clan_members
            (clan_id, player_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (
            clan["id"],
            player["id"]
        ))

    return {"ok": True}


@app.get("/api/news")
def news(request: Request):
    get_player(request)

    return [
        {
            "title": "Новый район открыт",
            "text": "Центральный сектор расширен. Стройте и развивайте город."
        },
        {
            "title": "Ночь синтетиков",
            "text": "Сегодня действует бонус XP за сбор производства."
        },
        {
            "title": "Городская хроника",
            "text": "Лучшие игроки недели появятся на доске рейтинга."
        }
    ]


@app.get("/api/meta")
def meta(request: Request):
    get_player(request)

    return {
        "developer": DEVELOPER,
        "version": "2.0 POSTGRES",
        "features": [
            "Профиль",
            "Город",
            "Производство",
            "Квесты",
            "Достижения",
            "Кланы",
            "Рейтинг",
            "Районы",
            "Инвентарь",
            "Ежедневные награды",
            "Безопасный PvP"
        ]
    }


@app.post("/api/pvp/training")
def pvp_training(request: Request):
    user = get_player(request)
    player = ensure_player(user)

    reward = 35

    with db() as c:
        c.execute("""
            UPDATE players
            SET coins=coins+%s,
                energy=GREATEST(0, energy-10)
            WHERE id=%s
        """, (
            reward,
            player["id"]
        ))

        add_xp(c, player["id"], 25)

    return {
        "ok": True,
        "reward": reward,
        "message": "Тренировочный бой завершён"
    }


@app.get("/api/support")
def support(request: Request):
    get_player(request)

    return {
        "developer": DEVELOPER,
        "message": "Спасибо за поддержку проекта! Раздел оплаты пока отключён."
        }
