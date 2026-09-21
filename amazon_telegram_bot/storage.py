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
            existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(seen_orders)")}
            if "delivered_at" not in existing_columns:
                conn.execute("ALTER TABLE seen_orders ADD COLUMN delivered_at TEXT")
            if "grand_total" not in existing_columns:
                conn.execute("ALTER TABLE seen_orders ADD COLUMN grand_total REAL")
            if "cancelled" not in existing_columns:
                conn.execute("ALTER TABLE seen_orders ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0")
            if "item_description" not in existing_columns:
                conn.execute("ALTER TABLE seen_orders ADD COLUMN item_description TEXT")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_returns (
                    return_id TEXT PRIMARY KEY,
                    order_number TEXT,
                    return_status TEXT,
                    qr_sent INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL
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

    def upsert_order(
        self,
        order_number: str,
        delivery_status: str | None,
        seen_at: str,
        grand_total: float | None = None,
        cancelled: bool = False,
        item_description: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_orders
                    (order_number, delivery_status, last_seen_at, grand_total, cancelled, item_description)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_number) DO UPDATE SET
                    delivery_status = excluded.delivery_status,
                    last_seen_at = excluded.last_seen_at,
                    grand_total = excluded.grand_total,
                    cancelled = excluded.cancelled,
                    item_description = excluded.item_description
                """,
                (order_number, delivery_status, seen_at, grand_total, int(cancelled), item_description),
            )

    def get_cached_orders(self) -> list[tuple[str, str | None, float | None, bool, str | None]]:
        """All orders the poller has seen, for serving /orders without hitting Amazon.

        Only reflects the poller's last30-day polling window - a fresh
        install with no poll cycle yet returns an empty list, which callers
        should treat as "cache not populated," not "no orders exist."
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT order_number, delivery_status, grand_total, cancelled, item_description FROM seen_orders"
            ).fetchall()
            return [
                (number, status, total, bool(cancelled), item_description)
                for number, status, total, cancelled, item_description in rows
            ]

    def mark_delivered(self, order_number: str, delivered_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE seen_orders SET delivered_at = ? WHERE order_number = ?",
                (delivered_at, order_number),
            )

    def get_recent_deliveries(self, since_iso: str) -> list[tuple[str, str, float | None]]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT order_number, delivered_at, grand_total FROM seen_orders
                WHERE delivered_at IS NOT NULL AND delivered_at >= ?
                ORDER BY delivered_at DESC
                """,
                (since_iso,),
            ).fetchall()

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

    def is_new_return(self, return_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_returns WHERE return_id = ?",
                (return_id,),
            ).fetchone()
            return row is None

    def upsert_return(self, return_id: str, order_number: str, return_status: str | None, seen_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_returns (return_id, order_number, return_status, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(return_id) DO UPDATE SET
                    order_number = excluded.order_number,
                    return_status = excluded.return_status,
                    last_seen_at = excluded.last_seen_at
                """,
                (return_id, order_number, return_status, seen_at),
            )

    def return_qr_already_sent(self, return_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT qr_sent FROM seen_returns WHERE return_id = ?",
                (return_id,),
            ).fetchone()
            return bool(row and row[0])

    def mark_qr_sent(self, return_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE seen_returns SET qr_sent = 1 WHERE return_id = ?",
                (return_id,),
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
