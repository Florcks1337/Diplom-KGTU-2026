"""
Модуль базы данных — хранение отслеживаемых товаров и истории цен.
Использует SQLite (не требует дополнительных серверов).
"""

import sqlite3
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DB_FILE = "prices.db"


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        log.info("База данных инициализирована: %s", DB_FILE)

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                url           TEXT    NOT NULL,
                marketplace   TEXT    NOT NULL,
                name          TEXT    NOT NULL,
                current_price REAL    NOT NULL,
                target_price  REAL,
                added_at      TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                price     REAL    NOT NULL,
                checked_at TEXT   DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
            CREATE INDEX IF NOT EXISTS idx_history_item ON price_history(item_id);
        """)
        self.conn.commit()

    # ── Товары ──────────────────────────────────────────────────

    def add_item(self, user_id: int, url: str, marketplace: str,
                 name: str, current_price: float, target_price: float | None) -> int:
        cur = self.conn.execute(
            """INSERT INTO items (user_id, url, marketplace, name, current_price, target_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, url, marketplace, name, current_price, target_price),
        )
        self.conn.commit()
        item_id = cur.lastrowid
        self.add_price_history(item_id, current_price)
        return item_id

    def get_user_items(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM items WHERE user_id = ? ORDER BY added_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_items(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM items").fetchall()
        return [dict(r) for r in rows]

    def update_price(self, item_id: int, new_price: float):
        self.conn.execute(
            "UPDATE items SET current_price = ? WHERE id = ?", (new_price, item_id)
        )
        self.conn.commit()

    def remove_item(self, user_id: int, item_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ── История цен ─────────────────────────────────────────────

    def add_price_history(self, item_id: int, price: float):
        self.conn.execute(
            "INSERT INTO price_history (item_id, price) VALUES (?, ?)", (item_id, price)
        )
        self.conn.commit()

    def get_price_history(self, item_id: int, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            """SELECT price, checked_at FROM price_history
               WHERE item_id = ? ORDER BY checked_at DESC LIMIT ?""",
            (item_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
