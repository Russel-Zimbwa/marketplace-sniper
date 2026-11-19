import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    max_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    seeded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    watch_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (watch_id, listing_id)
);
CREATE TABLE IF NOT EXISTS prices (
    watch_id INTEGER NOT NULL, listing_id TEXT NOT NULL, price REAL NOT NULL, seen_at REAL NOT NULL,
    PRIMARY KEY (watch_id, listing_id)
);
CREATE TABLE IF NOT EXISTS ads (
    text_hash TEXT NOT NULL, listing_id TEXT NOT NULL, seen_at REAL NOT NULL,
    PRIMARY KEY (text_hash, listing_id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER, listing_id TEXT, title TEXT, price TEXT,
    url TEXT, score INTEGER, summary TEXT, created_at REAL
);
"""


@dataclass
class Watch:
    id: int
    query: str
    max_price: Optional[float]
    active: bool
    seeded: bool


class DB:
    def __init__(self, path=config.db_path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        try:  # added later: the price a listing had when last seen, to notice price drops
            self.conn.execute("ALTER TABLE seen ADD COLUMN price REAL")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    # settings
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, None if value is None else str(value)),
        )
        self.conn.commit()

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self.conn.commit()

    def items(self, prefix: str) -> list[tuple[str, str]]:
        return list(self.conn.execute("SELECT key,value FROM settings WHERE key LIKE ?", (prefix + "%",)))

    # watches
    def add_watch(self, query: str, max_price: Optional[float]) -> int:
        cur = self.conn.execute(
            "INSERT INTO watches(query,max_price,created_at) VALUES(?,?,?)", (query, max_price, time.time())
        )
        self.conn.commit()
        return cur.lastrowid

    def watches(self, only_active: bool = False) -> list[Watch]:
        sql = "SELECT id,query,max_price,active,seeded FROM watches"
        if only_active:
            sql += " WHERE active=1"
        return [Watch(r[0], r[1], r[2], bool(r[3]), bool(r[4])) for r in self.conn.execute(sql + " ORDER BY id")]

    def get_watch(self, watch_id: int) -> Optional[Watch]:
        return next((w for w in self.watches() if w.id == watch_id), None)

    def delete_watch(self, watch_id: int) -> None:
        self.conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        self.conn.execute("DELETE FROM seen WHERE watch_id=?", (watch_id,))
        self.conn.execute("DELETE FROM prices WHERE watch_id=?", (watch_id,))
        for k in ("deep", "pick", "asked"):
            self.conn.execute("DELETE FROM settings WHERE key=?", (f"{k}:{watch_id}",))
        self.conn.commit()

    def set_active(self, watch_id: int, active: bool) -> None:
        self.conn.execute("UPDATE watches SET active=? WHERE id=?", (int(active), watch_id))
        self.conn.commit()

    def mark_seeded(self, watch_id: int) -> None:
        self.conn.execute("UPDATE watches SET seeded=1 WHERE id=?", (watch_id,))
        self.conn.commit()

    # seen listings
    def is_seen(self, watch_id: int, listing_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM seen WHERE watch_id=? AND listing_id=?", (watch_id, listing_id)
        ).fetchone() is not None

    def mark_seen(self, watch_id: int, listing_id: str, price: Optional[float] = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen(watch_id,listing_id,first_seen,price) VALUES(?,?,?,?)",
            (watch_id, listing_id, time.time(), price),
        )
        self.conn.commit()

    def seen_price(self, watch_id: int, listing_id: str) -> Optional[float]:
        row = self.conn.execute(
            "SELECT price FROM seen WHERE watch_id=? AND listing_id=?", (watch_id, listing_id)).fetchone()
        return row[0] if row else None

    def set_seen_price(self, watch_id: int, listing_id: str, price: Optional[float]) -> None:
        self.conn.execute("UPDATE seen SET price=? WHERE watch_id=? AND listing_id=?", (price, watch_id, listing_id))
        self.conn.commit()

    def same_ad(self, text_hash: str, listing_id: str) -> int:
        """Remember a listing's description; return how many OTHER listings had the same one (30 days)."""
        now = time.time()
        self.conn.execute("INSERT OR IGNORE INTO ads(text_hash,listing_id,seen_at) VALUES(?,?,?)",
                          (text_hash, listing_id, now))
        self.conn.commit()
        return self.conn.execute("SELECT COUNT(*) FROM ads WHERE text_hash=? AND listing_id!=? AND seen_at>=?",
                                 (text_hash, listing_id, now - 30 * 86400)).fetchone()[0]

    def rename_watch(self, watch_id: int, query: str) -> None:
        self.conn.execute("UPDATE watches SET query=? WHERE id=?", (query, watch_id))
        self.conn.commit()

    # alerts
    def log_alert(self, watch_id, listing, score, summary) -> None:
        self.conn.execute(
            "INSERT INTO alerts(watch_id,listing_id,title,price,url,score,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (watch_id, listing.id, listing.title, listing.price_text, listing.url, score, summary, time.time()),
        )
        self.conn.commit()

    def alerts_since(self, ts: float) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM alerts WHERE created_at>=?", (ts,)).fetchone()[0]
