import json
from datetime import datetime, timezone

import aiosqlite

from config import DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                nickname TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(telegram_id, username)
            );

            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY,
                nickname TEXT,
                last_video_ids TEXT NOT NULL DEFAULT '[]',
                last_checked_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_subs_telegram
                ON subscriptions(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_subs_username
                ON subscriptions(username);
            """
        )
        columns = await db.execute("PRAGMA table_info(accounts)")
        names = {row[1] for row in await columns.fetchall()}
        if "sec_uid" not in names:
            await db.execute("ALTER TABLE accounts ADD COLUMN sec_uid TEXT")
        await db.commit()


async def add_subscription(
    telegram_id: int,
    username: str,
    nickname: str | None,
    video_ids: list[str],
    sec_uid: str | None = None,
) -> bool:
    """Returns True if a new subscription was created."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO subscriptions (telegram_id, username, nickname, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, username, nickname, _now()),
            )
        except aiosqlite.IntegrityError:
            return False

        await db.execute(
            """
            INSERT INTO accounts (username, nickname, last_video_ids, last_checked_at, sec_uid)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                nickname = excluded.nickname,
                sec_uid = COALESCE(excluded.sec_uid, accounts.sec_uid),
                last_video_ids = CASE
                    WHEN accounts.last_video_ids IN ('[]', '')
                    THEN excluded.last_video_ids
                    ELSE accounts.last_video_ids
                END
            """,
            (username, nickname, json.dumps(video_ids), _now(), sec_uid),
        )
        await db.commit()
        return True


async def remove_subscription(telegram_id: int, username: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM subscriptions WHERE telegram_id = ? AND username = ?",
            (telegram_id, username),
        )
        deleted = cursor.rowcount > 0
        remaining = await db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE username = ?",
            (username,),
        )
        count = (await remaining.fetchone())[0]
        if count == 0:
            await db.execute("DELETE FROM accounts WHERE username = ?", (username,))
        await db.commit()
        return deleted


async def count_subscriptions(telegram_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def list_subscriptions(telegram_id: int) -> list[tuple[str, str | None]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT username, nickname
            FROM subscriptions
            WHERE telegram_id = ?
            ORDER BY created_at
            """,
            (telegram_id,),
        )
        rows = await cursor.fetchall()
        return [(row["username"], row["nickname"]) for row in rows]


async def get_tracked_accounts() -> list[tuple[str, list[str], str | None]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT username, last_video_ids, sec_uid FROM accounts"
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            try:
                ids = json.loads(row["last_video_ids"] or "[]")
            except json.JSONDecodeError:
                ids = []
            result.append((row["username"], ids, row["sec_uid"]))
        return result


async def get_subscribers(username: str) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_id FROM subscriptions WHERE username = ?",
            (username,),
        )
        rows = await cursor.fetchall()
        return [int(row[0]) for row in rows]


async def update_account_videos(
    username: str,
    video_ids: list[str],
    nickname: str | None = None,
    sec_uid: str | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE accounts
            SET last_video_ids = ?,
                last_checked_at = ?,
                nickname = COALESCE(?, nickname),
                sec_uid = COALESCE(?, sec_uid)
            WHERE username = ?
            """,
            (json.dumps(video_ids), _now(), nickname, sec_uid, username),
        )
        if nickname:
            await db.execute(
                "UPDATE subscriptions SET nickname = ? WHERE username = ?",
                (nickname, username),
            )
        await db.commit()
