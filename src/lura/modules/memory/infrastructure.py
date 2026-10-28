"""History in SQLite, facts and summaries as Markdown files. All local.

Text only: audio is never stored. Search is LIKE, not FTS5.
ponytail: FTS5 is not compiled into every packaged sqlite (Windows, some
macOS builds); a LIKE scan is instant at one person's history and matches
Arabic substrings. Add an FTS5 index if it ever gets slow.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from ...shared import paths
from .domain import Turn

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, started REAL, ended REAL, title TEXT);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conv_id INTEGER, ts REAL, role TEXT,
                                     text TEXT, model TEXT);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def memory_dir() -> Path:
    return paths.data_dir() / "memory"


class MemoryStore:
    def __init__(self, root: Path | None = None):
        self.root = root or memory_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.facts_path = self.root / "memory.md"
        self.summaries = self.root / "summaries"
        # One connection, shared across the assistant, caption and dashboard
        # threads behind a lock.
        self._db = sqlite3.connect(self.root / "history.db", check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    def _insert(self, sql: str, args: tuple) -> int:
        with self._lock, self._db:
            return int(self._db.execute(sql, args).lastrowid)

    # ── conversations and messages ──
    def start(self) -> int:
        return self._insert("INSERT INTO conversations (started, title) VALUES (?, '')", (time.time(),))

    def end(self, conv_id: int) -> None:
        first = self._run("SELECT text FROM messages WHERE conv_id=? AND role='user' AND text!='' "
                          "ORDER BY id LIMIT 1", (conv_id,))
        title = " ".join(first[0][0].split())[:80] if first else ""
        self._run("UPDATE conversations SET ended=?, title=? WHERE id=?", (time.time(), title, conv_id))
        # A conversation where nothing was said is not worth a row in the list.
        self._run("DELETE FROM conversations WHERE id=? AND NOT EXISTS "
                  "(SELECT 1 FROM messages WHERE conv_id=? AND text!='')", (conv_id, conv_id))

    def add(self, conv_id: int, role: str, text: str, model: str = "") -> int:
        return self._insert("INSERT INTO messages (conv_id, ts, role, text, model) VALUES (?,?,?,?,?)",
                            (conv_id, time.time(), role, text, model))

    def delete_message(self, message_id: int) -> None:
        self._run("DELETE FROM messages WHERE id=?", (message_id,))

    def update(self, message_id: int, text: str) -> None:
        self._run("UPDATE messages SET text=? WHERE id=?", (text, message_id))

    def turns_after(self, message_id: int) -> list[Turn]:
        rows = self._run("SELECT id, role, text, ts FROM messages WHERE id>? AND text!='' ORDER BY id",
                         (message_id,))
        return [Turn(*row) for row in rows]

    def conversations(self, query: str = "", limit: int = 200) -> list[dict]:
        sql = ("SELECT c.id, c.started, c.ended, c.title, COUNT(m.id) FROM conversations c "
               "LEFT JOIN messages m ON m.conv_id=c.id AND m.text!='' ")
        args: tuple = ()
        if query:
            sql += "WHERE c.id IN (SELECT conv_id FROM messages WHERE text LIKE ? ESCAPE '\\') "
            args = (f"%{_like(query)}%",)
        sql += "GROUP BY c.id ORDER BY c.id DESC LIMIT ?"
        return [{"id": r[0], "started": r[1], "ended": r[2], "title": r[3], "messages": r[4]}
                for r in self._run(sql, args + (limit,))]

    def transcript(self, conv_id: int) -> list[dict]:
        return [{"id": r[0], "ts": r[1], "role": r[2], "text": r[3], "model": r[4]}
                for r in self._run("SELECT id, ts, role, text, model FROM messages "
                                   "WHERE conv_id=? AND text!='' ORDER BY id", (conv_id,))]

    def search(self, query: str, limit: int = 12) -> list[dict]:
        rows = self._run("SELECT ts, role, text FROM messages WHERE text LIKE ? ESCAPE '\\' "
                         "ORDER BY id DESC LIMIT ?", (f"%{_like(query)}%", limit))
        return [{"ts": r[0], "role": r[1], "text": r[2]} for r in rows]

    def delete(self, conv_id: int) -> bool:
        found = bool(self._run("SELECT 1 FROM conversations WHERE id=?", (conv_id,)))
        self._run("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self._run("DELETE FROM conversations WHERE id=?", (conv_id,))
        return found

    def delete_all(self) -> None:
        self._run("DELETE FROM messages")
        self._run("DELETE FROM conversations")
        self._run("DELETE FROM meta")

    def last_message_id(self) -> int:
        return int(self._run("SELECT COALESCE(MAX(id), 0) FROM messages")[0][0])

    # ── the running summary ──
    def get_meta(self, key: str, default: str = "") -> str:
        rows = self._run("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        self._run("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))

    def write_summary_file(self, summary: str) -> Path:
        """Every summary is also kept as Markdown, one file per day."""
        self.summaries.mkdir(parents=True, exist_ok=True)
        path = self.summaries / f"{time.strftime('%Y-%m-%d')}.md"
        path.write_text(f"# Summary of conversations up to {time.strftime('%Y-%m-%d %H:%M')}\n\n"
                        f"{summary.strip()}\n", encoding="utf-8")
        return path

    # ── durable facts ──
    def facts(self) -> str:
        try:
            return self.facts_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def set_facts(self, text: str) -> None:
        paths.write_private(self.facts_path, text)


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
