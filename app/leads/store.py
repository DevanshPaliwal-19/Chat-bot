"""Lead persistence behind a small interface; SQLite implementation included."""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_COLUMNS = (
    "name", "company", "email", "phone", "project_type", "requirements",
    "timeline", "budget", "preferred_contact",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LeadStore(ABC):
    @abstractmethod
    def upsert(self, fields: dict[str, str | None], *, score: int, follow_up: bool, conversation_id: str | None,
               summary: str | None, dedupe_days: int = 30) -> tuple[int, bool, str]: ...

    @abstractmethod
    def get(self, lead_id: int) -> dict[str, Any] | None: ...

    @abstractmethod
    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_status(self, lead_id: int, status: str, note: str | None = None) -> bool: ...

    @abstractmethod
    def history(self, lead_id: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    def purge_older_than(self, days: int) -> int: ...


class SQLiteLeadStore(LeadStore):
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    name TEXT, company TEXT, email TEXT, phone TEXT, project_type TEXT, requirements TEXT,
                    timeline TEXT, budget TEXT, preferred_contact TEXT,
                    score INTEGER NOT NULL DEFAULT 0, follow_up_requested INTEGER NOT NULL DEFAULT 0,
                    consent_at TEXT NOT NULL, conversation_id TEXT, conversation_summary TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(lower(email));
                CREATE TABLE IF NOT EXISTS lead_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,
                    ts TEXT NOT NULL, event TEXT NOT NULL, note TEXT
                );
                """
            )

    def upsert(self, fields, *, score, follow_up, conversation_id, summary, dedupe_days=30):
        now = _now()
        email = (fields.get("email") or "").lower() or None
        with self._lock:
            existing = None
            if email:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=dedupe_days)).isoformat(timespec="seconds")
                existing = self._db.execute(
                    "SELECT * FROM leads WHERE lower(email)=? AND created_at>=? ORDER BY id DESC LIMIT 1", (email, cutoff)
                ).fetchone()
            status = "handoff_requested" if follow_up else "new"
            if existing:
                merged = {c: (fields.get(c) or existing[c]) for c in _COLUMNS}
                new_status = "handoff_requested" if follow_up else existing["status"]
                self._db.execute(
                    f"UPDATE leads SET {', '.join(c + '=?' for c in _COLUMNS)}, score=?, status=?, updated_at=?, "
                    "follow_up_requested=?, conversation_id=COALESCE(?, conversation_id), "
                    "conversation_summary=COALESCE(?, conversation_summary) WHERE id=?",
                    (*[merged[c] for c in _COLUMNS], max(score, existing["score"]), new_status, now,
                     int(bool(existing["follow_up_requested"]) or follow_up), conversation_id, summary, existing["id"]),
                )
                self._event(existing["id"], "updated", "duplicate email: merged into existing lead")
                self._db.commit()
                return int(existing["id"]), True, new_status
            cur = self._db.execute(
                f"INSERT INTO leads ({', '.join(_COLUMNS)}, score, status, created_at, updated_at, follow_up_requested, "
                "consent_at, conversation_id, conversation_summary) VALUES "
                f"({', '.join('?' for _ in _COLUMNS)}, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*[fields.get(c) for c in _COLUMNS], score, status, now, now, int(follow_up), now, conversation_id, summary),
            )
            lead_id = int(cur.lastrowid)
            self._event(lead_id, "created", "handoff requested" if follow_up else None)
            self._db.commit()
            return lead_id, False, status

    def _event(self, lead_id: int, event: str, note: str | None = None) -> None:
        self._db.execute("INSERT INTO lead_events (lead_id, ts, event, note) VALUES (?,?,?,?)", (lead_id, _now(), event, note))

    def get(self, lead_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def list(self, status=None, limit=100):
        with self._lock:
            if status:
                rows = self._db.execute("SELECT * FROM leads WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = self._db.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, lead_id, status, note=None):
        with self._lock:
            cur = self._db.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, _now(), lead_id))
            if cur.rowcount:
                self._event(lead_id, f"status:{status}", note)
            self._db.commit()
            return bool(cur.rowcount)

    def history(self, lead_id):
        with self._lock:
            rows = self._db.execute("SELECT ts, event, note FROM lead_events WHERE lead_id=? ORDER BY id", (lead_id,)).fetchall()
        return [dict(r) for r in rows]

    def purge_older_than(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            ids = [r["id"] for r in self._db.execute("SELECT id FROM leads WHERE created_at<?", (cutoff,)).fetchall()]
            for i in ids:
                self._db.execute("DELETE FROM lead_events WHERE lead_id=?", (i,))
                self._db.execute("DELETE FROM leads WHERE id=?", (i,))
            self._db.commit()
        return len(ids)

    def close(self) -> None:
        with self._lock:
            self._db.close()
