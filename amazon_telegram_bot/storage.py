import os
import sqlite3
from contextlib import contextmanager


class Storage:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_orders (
                    order_number TEXT PRIMARY KEY,
                    delivery_status TEXT,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_transactions (
                    transaction_key TEXT PRIMARY KEY,
                    seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS poll_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

    def get_order_status(self, order_number: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT delivery_status FROM seen_orders WHERE order_number = ?",
                (order_number,),
            ).fetchone()
            return row[0] if row else None

    def upsert_order(self, order_number: str, delivery_status: str | None, seen_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_orders (order_number, delivery_status, last_seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(order_number) DO UPDATE SET
                    delivery_status = excluded.delivery_status,
                    last_seen_at = excluded.last_seen_at
                """,
                (order_number, delivery_status, seen_at),
            )

    def is_new_transaction(self, transaction_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_transactions WHERE transaction_key = ?",
                (transaction_key,),
            ).fetchone()
            return row is None

    def mark_transaction_seen(self, transaction_key: str, seen_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_transactions (transaction_key, seen_at) VALUES (?, ?)",
                (transaction_key, seen_at),
            )

    def get_last_poll_at(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM poll_state WHERE key = 'last_poll_at'"
            ).fetchone()
            return row[0] if row else None

    def set_last_poll_at(self, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO poll_state (key, value) VALUES ('last_poll_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (timestamp,),
            )
