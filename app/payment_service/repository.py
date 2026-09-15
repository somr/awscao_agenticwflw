"""PostgreSQL-standing-in persistence for payment callback idempotency.

Uses sqlite3 (stdlib, no external dependency) as a stand-in for the
PostgreSQL system of record described in the PAY-DEMO-001 fixture. The
idempotency primitive is a UNIQUE constraint on provider_event_id: the first
INSERT for a given event ID succeeds, every subsequent one raises
sqlite3.IntegrityError. This is a database-constraint check, not a
distributed lock, per the fixture's explicit constraint against introducing
a lock service solely for callback idempotency.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class PaymentRepository:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_callbacks (
                provider_event_id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def record_if_new(self, provider_event_id: str, payment_id: str) -> bool:
        """Return True if this event is being recorded for the first time."""
        try:
            self._conn.execute(
                "INSERT INTO processed_callbacks (provider_event_id, payment_id) VALUES (?, ?)",
                (provider_event_id, payment_id),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self._conn.close()
