"""Local history store (SQLite). Nothing leaves the machine; export is explicit and redacted."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from printmedic.diagnostics.engine import DiagnosticSession
from printmedic.repair.executor import RepairRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    target TEXT,
    target_label TEXT,
    simulated INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (
    record_id TEXT PRIMARY KEY,
    session_id TEXT,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    risk TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    simulated INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_repairs_started ON repairs(started_at);
"""


def session_to_dict(s: DiagnosticSession) -> dict[str, Any]:
    d = asdict(s)
    d["findings"] = [f.to_dict() for f in s.findings]
    return d


class HistoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_session(self, s: DiagnosticSession) -> None:
        body = session_to_dict(s)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                (s.session_id, s.started_at, s.finished_at, s.target, s.target_label, int(s.simulated),
                 int(s.cancelled), json.dumps(s.summary), json.dumps(body, default=str)))
            self._conn.commit()

    def save_repair(self, r: RepairRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO repairs VALUES (?,?,?,?,?,?,?,?,?)",
                (r.record_id, r.session_id, r.action_id, r.status, r.risk, r.started_at, r.finished_at,
                 int(r.simulated), json.dumps(asdict(r), default=str)))
            self._conn.commit()

    def sessions(self, search: str = "", limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = [json.loads(r["body"]) for r in rows]
        if search:
            s = search.lower()
            out = [d for d in out if s in json.dumps(d, ensure_ascii=False).lower()]
        return out

    def session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def repairs(self, search: str = "", session_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            if session_id:
                rows = self._conn.execute("SELECT body FROM repairs WHERE session_id=? ORDER BY started_at DESC",
                                          (session_id,)).fetchall()
            else:
                rows = self._conn.execute("SELECT body FROM repairs ORDER BY started_at DESC LIMIT ?",
                                          (limit,)).fetchall()
        out = [json.loads(r["body"]) for r in rows]
        if search:
            s = search.lower()
            out = [d for d in out if s in json.dumps(d, ensure_ascii=False).lower()]
        return out

    def update_repair_confirmation(self, record_id: str, confirmation: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM repairs WHERE record_id=?", (record_id,)).fetchone()
            if not row:
                return
            body = json.loads(row["body"])
            body["user_confirmation"] = confirmation
            status = "verified" if confirmation == "yes" else "failed"
            body["status"] = status
            body.setdefault("verification", []).append(["verify.user_confirmed", {"answer": confirmation}])
            self._conn.execute("UPDATE repairs SET body=?, status=? WHERE record_id=?",
                               (json.dumps(body, default=str), status, record_id))
            self._conn.commit()

    def apply_retention(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)).isoformat()
        with self._lock:
            n = self._conn.execute("DELETE FROM sessions WHERE started_at < ?", (cutoff,)).rowcount
            n += self._conn.execute("DELETE FROM repairs WHERE started_at < ?", (cutoff,)).rowcount
            self._conn.commit()
        return n

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions")
            self._conn.execute("DELETE FROM repairs")
            self._conn.commit()
            self._conn.execute("VACUUM")
