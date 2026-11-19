"""Session-scoped conversation memory with optional or default SQLite persistence.
Every lookup requires the owning session id (isolation).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Turn:
    role: str
    content: str
    message_id: str
    mode: str = "text"
    ts: str = field(default_factory=_now_iso)
    audio_interrupted: bool = False


@dataclass
class LeadState:
    fields: dict[str, str] = field(default_factory=dict)
    score: int = 0
    status: str = "none"  # none | exploring | qualified | captured | handoff_requested
    offered: bool = False


@dataclass
class Conversation:
    id: str
    turns: list[Turn] = field(default_factory=list)
    summary: str = ""
    summarized_upto: int = 0
    active_topic: str | None = None
    last_intent: str | None = None
    selected_service: str | None = None
    stage: str = "greeting"  # greeting | exploring | discovery | qualifying | concluded
    entities: dict[str, Any] = field(default_factory=dict)
    last_options_presented: list[str] = field(default_factory=list)
    unresolved_count: int = 0
    summarizing: bool = False
    lead: LeadState = field(default_factory=LeadState)
    handled: "OrderedDict[str, Any]" = field(default_factory=OrderedDict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_at: float = field(default_factory=time.monotonic)
    session_id: str | None = None


@dataclass
class Session:
    id: str
    conversations: dict[str, Conversation] = field(default_factory=dict)
    last_active: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(
        self,
        ttl_minutes: int = 60,
        max_sessions: int = 2000,
        max_turns: int = 120,
        db_path: Path | str | None = None,
    ) -> None:
        self.ttl_s = ttl_minutes * 60
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self.db_path = db_path
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()
        self._db: sqlite3.Connection | None = None

        if db_path is not None:
            if str(db_path) != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._init_db()

    def _init_db(self) -> None:
        if self._db is None:
            return
        with self._lock:
            self._db.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    last_active REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    summarized_upto INTEGER DEFAULT 0,
                    active_topic TEXT,
                    last_intent TEXT,
                    selected_service TEXT,
                    stage TEXT DEFAULT 'greeting',
                    entities_json TEXT DEFAULT '{}',
                    last_options_json TEXT DEFAULT '[]',
                    unresolved_count INTEGER DEFAULT 0,
                    lead_json TEXT DEFAULT '{}',
                    handled_json TEXT DEFAULT '{}',
                    created_at REAL,
                    updated_at REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    mode TEXT DEFAULT 'text',
                    ts TEXT,
                    audio_interrupted INTEGER DEFAULT 0,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
                CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);
                """
            )
            self._db.commit()

    def _expired(self, s: Session, now: float) -> bool:
        return now - s.last_active > self.ttl_s

    def _db_save_session(self, session_id: str, now: float) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT INTO sessions (id, last_active, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_active=excluded.last_active",
            (session_id, now, now),
        )
        self._db.commit()

    def _db_load_session(self, session_id: str, now: float) -> Session | None:
        if self._db is None:
            return None
        row = self._db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        last_active = float(row["last_active"])
        if now - last_active > self.ttl_s:
            self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._db.commit()
            return None
        sess = Session(id=session_id, last_active=last_active)
        return sess

    def _db_load_conversation(self, session_id: str, conversation_id: str) -> Conversation | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT * FROM conversations WHERE id=? AND session_id=?", (conversation_id, session_id)
        ).fetchone()
        if not row:
            return None

        # Load turns
        turn_rows = self._db.execute(
            "SELECT role, content, message_id, mode, ts, audio_interrupted "
            "FROM turns WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        turns = [
            Turn(
                role=r["role"],
                content=r["content"],
                message_id=r["message_id"],
                mode=r["mode"] or "text",
                ts=r["ts"] or _now_iso(),
                audio_interrupted=bool(r["audio_interrupted"]),
            )
            for r in turn_rows
        ]

        # Parse lead
        lead_data = {}
        try:
            lead_data = json.loads(row["lead_json"] or "{}")
        except Exception:
            pass
        lead = LeadState(
            fields=lead_data.get("fields", {}),
            score=lead_data.get("score", 0),
            status=lead_data.get("status", "none"),
            offered=lead_data.get("offered", False),
        )

        entities = {}
        try:
            entities = json.loads(row["entities_json"] or "{}")
        except Exception:
            pass

        last_options = []
        try:
            last_options = json.loads(row["last_options_json"] or "[]")
        except Exception:
            pass

        conv = Conversation(
            id=conversation_id,
            turns=turns,
            summary=row["summary"] or "",
            summarized_upto=int(row["summarized_upto"] or 0),
            active_topic=row["active_topic"],
            last_intent=row["last_intent"],
            selected_service=row["selected_service"],
            stage=row["stage"] or "greeting",
            entities=entities,
            last_options_presented=last_options,
            unresolved_count=int(row["unresolved_count"] or 0),
            lead=lead,
            created_at=float(row["created_at"] or time.monotonic()),
            session_id=session_id,
        )
        return conv

    def save_conversation(self, conv: Conversation) -> None:
        """Persist conversation and its turns to SQLite."""
        if self._db is None or not conv.session_id:
            return
        with self._lock:
            lead_json = json.dumps({
                "fields": conv.lead.fields,
                "score": conv.lead.score,
                "status": conv.lead.status,
                "offered": conv.lead.offered,
            })
            entities_json = json.dumps(conv.entities)
            options_json = json.dumps(conv.last_options_presented)
            now = time.monotonic()

            self._db.execute(
                """
                INSERT INTO conversations (
                    id, session_id, summary, summarized_upto, active_topic, last_intent,
                    selected_service, stage, entities_json, last_options_json,
                    unresolved_count, lead_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    summary=excluded.summary,
                    summarized_upto=excluded.summarized_upto,
                    active_topic=excluded.active_topic,
                    last_intent=excluded.last_intent,
                    selected_service=excluded.selected_service,
                    stage=excluded.stage,
                    entities_json=excluded.entities_json,
                    last_options_json=excluded.last_options_json,
                    unresolved_count=excluded.unresolved_count,
                    lead_json=excluded.lead_json,
                    updated_at=excluded.updated_at
                """,
                (
                    conv.id,
                    conv.session_id,
                    conv.summary,
                    conv.summarized_upto,
                    conv.active_topic,
                    conv.last_intent,
                    conv.selected_service,
                    conv.stage,
                    entities_json,
                    options_json,
                    conv.unresolved_count,
                    lead_json,
                    conv.created_at,
                    now,
                ),
            )

            # Sync turns: replace turns for simplicity and strict ordering
            self._db.execute("DELETE FROM turns WHERE conversation_id=?", (conv.id,))
            for turn in conv.turns:
                self._db.execute(
                    "INSERT INTO turns (conversation_id, role, content, message_id, mode, ts, audio_interrupted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        conv.id,
                        turn.role,
                        turn.content,
                        turn.message_id,
                        turn.mode,
                        turn.ts,
                        1 if turn.audio_interrupted else 0,
                    ),
                )
            self._db.commit()

    def get_or_create_conversation(self, session_id: str, conversation_id: str | None) -> tuple[Session, Conversation, bool]:
        """Return (session, conversation, restarted). Unknown ids for this session start a fresh conversation."""
        now = time.monotonic()
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None and self._expired(s, now):
                del self._sessions[session_id]
                if self._db is not None:
                    self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                    self._db.commit()
                s = None

            if s is None:
                # Check DB
                s = self._db_load_session(session_id, now)

            if s is None:
                if len(self._sessions) >= self.max_sessions:
                    self._evict_oldest()
                s = Session(session_id)
                self._sessions[session_id] = s
                self._db_save_session(session_id, now)
            else:
                s.last_active = now
                self._sessions[session_id] = s
                self._db_save_session(session_id, now)

            conv = s.conversations.get(conversation_id) if conversation_id else None
            if conv is None and conversation_id and self._db is not None:
                conv = self._db_load_conversation(session_id, conversation_id)
                if conv:
                    s.conversations[conv.id] = conv

            restarted = bool(conversation_id) and conv is None
            if conv is None:
                conv = Conversation(id=new_id(), session_id=session_id)
                s.conversations[conv.id] = conv
                self._db_persist_new_conv(session_id, conv)
            else:
                conv.session_id = session_id

            return s, conv, restarted

    def _db_persist_new_conv(self, session_id: str, conv: Conversation) -> None:
        if self._db is None:
            return
        now = time.monotonic()
        self._db.execute(
            "INSERT OR IGNORE INTO conversations (id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv.id, session_id, conv.created_at, now),
        )
        self._db.commit()

    def find_conversation(self, session_id: str, conversation_id: str) -> Conversation | None:
        now = time.monotonic()
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None and self._expired(s, now):
                del self._sessions[session_id]
                if self._db is not None:
                    self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                    self._db.commit()
                s = None

            if s is None:
                s = self._db_load_session(session_id, now)
                if s:
                    self._sessions[session_id] = s

            if s is None or self._expired(s, now):
                return None

            s.last_active = now
            self._db_save_session(session_id, now)

            conv = s.conversations.get(conversation_id)
            if conv is None and self._db is not None:
                conv = self._db_load_conversation(session_id, conversation_id)
                if conv:
                    s.conversations[conv.id] = conv
            return conv

    def delete_conversation(self, session_id: str, conversation_id: str) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            deleted_mem = bool(s and s.conversations.pop(conversation_id, None) is not None)
            deleted_db = False
            if self._db is not None:
                cur = self._db.execute(
                    "DELETE FROM conversations WHERE id=? AND session_id=?", (conversation_id, session_id)
                )
                self._db.commit()
                deleted_db = bool(cur.rowcount)
            return deleted_mem or deleted_db

    def trim(self, conv: Conversation) -> None:
        overflow = len(conv.turns) - self.max_turns
        if overflow > 0:
            del conv.turns[:overflow]
            conv.summarized_upto = max(0, conv.summarized_upto - overflow)
        self.save_conversation(conv)

    def purge_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            dead = [sid for sid, s in self._sessions.items() if self._expired(s, now)]
            for sid in dead:
                del self._sessions[sid]
            db_purged = 0
            if self._db is not None:
                cutoff = now - self.ttl_s
                cur = self._db.execute("DELETE FROM sessions WHERE last_active < ?", (cutoff,))
                self._db.commit()
                db_purged = cur.rowcount
            return max(len(dead), db_purged)

    def _evict_oldest(self) -> None:
        oldest = min(self._sessions.values(), key=lambda s: s.last_active, default=None)
        if oldest:
            del self._sessions[oldest.id]

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def __len__(self) -> int:
        with self._lock:
            if self._db is not None:
                row = self._db.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
                return int(row["c"]) if row else len(self._sessions)
            return len(self._sessions)


class SQLiteSessionStore(SessionStore):
    """Convenience alias for explicit SQLite session store initialization."""

    def __init__(
        self,
        db_path: Path | str,
        ttl_minutes: int = 60,
        max_sessions: int = 2000,
        max_turns: int = 120,
    ) -> None:
        super().__init__(ttl_minutes=ttl_minutes, max_sessions=max_sessions, max_turns=max_turns, db_path=db_path)
