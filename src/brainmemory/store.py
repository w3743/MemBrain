"""
SQLite 存储层

简化 schema：移除 memory_type, importance, confidence,
volatility, confirm_count, contradict_count, success_count,
activation_count, entities, base_strength, decay_rate。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from .embedding import EmbeddingBackend, build_embedding_backend_from_env
from .models import Memory, MemoryStatus, dt_to_str, utc_now
from .strength import INITIAL_STRENGTH

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str, label: str) -> None:
    """验证 SQL 标识符，防止注入。"""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL identifier {label}: {name!r}")


class MemoryStore:
    def __init__(self, db_path: str | Path, embedding: EmbeddingBackend | None = None) -> None:
        self.db_path = Path(db_path)
        self.embedding = embedding or build_embedding_backend_from_env()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                summary TEXT DEFAULT '',
                strength REAL NOT NULL DEFAULT 0.6,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by INTEGER,
                tags TEXT DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'user',
                project_id TEXT,
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                decay_rate REAL NOT NULL DEFAULT 0.02,
                boost REAL NOT NULL DEFAULT 0.0,
                trust REAL NOT NULL DEFAULT 0.5,
                error_count INTEGER NOT NULL DEFAULT 0,
                verify_count INTEGER NOT NULL DEFAULT 0,
                stability REAL NOT NULL DEFAULT 27.4653072167,
                difficulty REAL NOT NULL DEFAULT 0.5,
                utility REAL NOT NULL DEFAULT 0.5,
                trust_alpha REAL NOT NULL DEFAULT 2.0,
                trust_beta REAL NOT NULL DEFAULT 2.0,
                exposure_count INTEGER NOT NULL DEFAULT 0,
                correction_count INTEGER NOT NULL DEFAULT 0,
                embedding TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, summary, tags
            );

            CREATE TABLE IF NOT EXISTS memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_id, target_id, relation)
            );

            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER,
                action TEXT NOT NULL,
                p_use REAL NOT NULL,
                p_ignore REAL NOT NULL,
                p_correct REAL NOT NULL,
                confidence REAL NOT NULL,
                query TEXT DEFAULT '',
                answer TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_project_status ON memories(project_id, status);
            CREATE INDEX IF NOT EXISTS idx_memories_strength ON memories(strength);
            CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at);
            CREATE INDEX IF NOT EXISTS idx_feedback_memory_created
                ON memory_feedback_events(memory_id, created_at);
        """)
        self.conn.execute("INSERT OR IGNORE INTO memory_meta(key, value) VALUES ('memory_index_version', '0')")
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """为已有数据库添加新字段（兼容旧版 schema）。"""
        migrations = [
            ("decay_rate", "REAL NOT NULL DEFAULT 0.02"),
            ("boost", "REAL NOT NULL DEFAULT 0.0"),
            ("trust", "REAL NOT NULL DEFAULT 0.5"),
            ("error_count", "INTEGER NOT NULL DEFAULT 0"),
            ("verify_count", "INTEGER NOT NULL DEFAULT 0"),
            ("stability", "REAL NOT NULL DEFAULT 27.4653072167"),
            ("difficulty", "REAL NOT NULL DEFAULT 0.5"),
            ("utility", "REAL NOT NULL DEFAULT 0.5"),
            ("trust_alpha", "REAL NOT NULL DEFAULT 2.0"),
            ("trust_beta", "REAL NOT NULL DEFAULT 2.0"),
            ("exposure_count", "INTEGER NOT NULL DEFAULT 0"),
            ("correction_count", "INTEGER NOT NULL DEFAULT 0"),
        ]
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(memories)").fetchall()}
        added: set[str] = set()
        for col, defn in migrations:
            if col not in existing:
                _validate_identifier(col, "column")
                self.conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {defn}")
                added.add(col)
        if "stability" in added:
            self.conn.execute(
                "UPDATE memories SET stability=0.5493061443340549 / "
                "MAX(0.001, MIN(0.3, decay_rate))"
            )
        if "trust_alpha" in added or "trust_beta" in added:
            self.conn.execute(
                "UPDATE memories SET trust_alpha=MAX(0.1, trust*4.0), "
                "trust_beta=MAX(0.1, (1.0-trust)*4.0)"
            )
        self.conn.commit()

    # ── CRUD ──────────────────────────────────────────────────

    def add(self, memory: Memory, *, commit: bool = True) -> Memory:
        memory.ensure_trust_distribution()
        now = utc_now()
        memory.created_at = memory.created_at or now
        memory.updated_at = memory.updated_at or now
        memory.last_accessed_at = memory.last_accessed_at or now
        embedding = json.dumps(self.embedding.embed(memory.text_for_index))
        cur = self.conn.execute(
            """
            INSERT INTO memories (
                content, summary, strength, access_count, last_accessed_at,
                status, superseded_by, tags, scope, project_id,
                sensitivity, decay_rate, boost, trust, error_count, verify_count,
                stability, difficulty, utility, trust_alpha, trust_beta,
                exposure_count, correction_count,
                embedding, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.content,
                memory.summary,
                memory.strength,
                memory.access_count,
                dt_to_str(memory.last_accessed_at),
                memory.status.value,
                memory.superseded_by,
                memory.tags,
                memory.scope,
                memory.project_id,
                memory.sensitivity,
                memory.decay_rate,
                memory.boost,
                memory.trust,
                memory.error_count,
                memory.verify_count,
                memory.stability,
                memory.difficulty,
                memory.utility,
                memory.trust_alpha,
                memory.trust_beta,
                memory.exposure_count,
                memory.correction_count,
                embedding,
                dt_to_str(memory.created_at),
                dt_to_str(memory.updated_at),
            ),
        )
        memory.id = int(cur.lastrowid)
        self._sync_fts(memory.id)
        self._bump_index_version()
        if commit:
            self.conn.commit()
        return memory

    def update(self, memory: Memory) -> Memory:
        memory.ensure_trust_distribution()
        memory.updated_at = utc_now()
        existing = self.conn.execute(
            "SELECT content, summary, tags, embedding FROM memories WHERE id=?",
            (memory.id,),
        ).fetchone()
        index_changed = (
            existing is None
            or existing["content"] != memory.content
            or (existing["summary"] or "") != (memory.summary or "")
            or (existing["tags"] or "") != (memory.tags or "")
        )
        embedding = (
            json.dumps(self.embedding.embed(memory.text_for_index))
            if index_changed
            else existing["embedding"]
        )
        self.conn.execute(
            """
            UPDATE memories SET
                content=?, summary=?, strength=?, access_count=?,
                last_accessed_at=?, status=?, superseded_by=?, tags=?,
                scope=?, project_id=?, sensitivity=?,
                decay_rate=?, boost=?, trust=?, error_count=?, verify_count=?,
                stability=?, difficulty=?, utility=?, trust_alpha=?, trust_beta=?,
                exposure_count=?, correction_count=?,
                embedding=?, updated_at=?
            WHERE id=?
            """,
            (
                memory.content, memory.summary, memory.strength, memory.access_count,
                dt_to_str(memory.last_accessed_at), memory.status.value,
                memory.superseded_by, memory.tags,
                memory.scope, memory.project_id, memory.sensitivity,
                memory.decay_rate, memory.boost, memory.trust,
                memory.error_count, memory.verify_count,
                memory.stability, memory.difficulty, memory.utility,
                memory.trust_alpha, memory.trust_beta,
                memory.exposure_count, memory.correction_count,
                embedding, dt_to_str(memory.updated_at),
                memory.id,
            ),
        )
        if index_changed:
            self._sync_fts(memory.id)
            self._bump_index_version()
        self.conn.commit()
        return memory

    def delete(self, memory_id: int, *, commit: bool = True) -> bool:
        row = self.conn.execute("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute("DELETE FROM memories_fts WHERE rowid=?", (memory_id,))
        self.conn.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?", (memory_id, memory_id))
        self.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self._bump_index_version()
        if commit:
            self.conn.commit()
        return True

    def get(self, memory_id: int) -> Memory | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return Memory.from_row(row) if row else None

    def list(self, include_archived: bool = False) -> list[Memory]:
        statuses = (MemoryStatus.ACTIVE.value, MemoryStatus.SUPERSEDED.value)
        if include_archived:
            statuses = statuses + (MemoryStatus.ARCHIVED.value,)
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE status IN ({','.join('?' for _ in statuses)}) ORDER BY strength DESC",
            statuses,
        ).fetchall()
        return [Memory.from_row(row) for row in rows]

    def list_all(self) -> list[Memory]:
        rows = self.conn.execute("SELECT * FROM memories ORDER BY strength DESC").fetchall()
        return [Memory.from_row(row) for row in rows]

    # ── 检索 ──────────────────────────────────────────────────

    def keyword_search(self, query: str, limit: int = 20) -> dict[int, float]:
        try:
            rows = self.conn.execute(
                "SELECT rowid, bm25(memories_fts) AS rank FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        if not rows:
            return {}
        if len(rows) == 1:
            return {int(rows[0]["rowid"]): 1.0}
        worst = max(abs(row["rank"]) for row in rows) or 1.0
        return {int(row["rowid"]): 1.0 - min(abs(row["rank"]) / worst, 1.0) for row in rows}

    def embedding_for_row(self, row: sqlite3.Row) -> list[float]:
        return json.loads(row["embedding"])

    def reindex_embeddings(self) -> int:
        rows = self.conn.execute("SELECT id, content, summary, tags FROM memories").fetchall()
        for row in rows:
            text = " ".join(part for part in [row["content"], row["summary"] or "", row["tags"] or ""] if part)
            embedding = json.dumps(self.embedding.embed(text))
            self.conn.execute("UPDATE memories SET embedding=?, updated_at=? WHERE id=?",
                              (embedding, dt_to_str(utc_now()), row["id"]))
            self._sync_fts(int(row["id"]))
        if rows:
            self._bump_index_version()
        self.conn.commit()
        return len(rows)

    # ── 辅助 ──────────────────────────────────────────────────

    def _sync_fts(self, memory_id: int) -> None:
        row = self.conn.execute(
            "SELECT id, content, summary, tags FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO memories_fts(rowid, content, summary, tags) VALUES (?, ?, ?, ?)",
            (row["id"], row["content"], row["summary"] or "", row["tags"] or ""),
        )

    def add_link(self, source_id: int, target_id: int, relation: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO memory_links(source_id, target_id, relation, created_at) VALUES (?, ?, ?, ?)",
            (source_id, target_id, relation, dt_to_str(utc_now())),
        )
        self.conn.commit()

    def record_feedback_event(
        self,
        memory_id: int | None,
        action: str,
        p_use: float,
        p_ignore: float,
        p_correct: float,
        confidence: float,
        query: str = "",
        answer: str = "",
        evidence: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_feedback_events(
                memory_id, action, p_use, p_ignore, p_correct,
                confidence, query, answer, evidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id, action, p_use, p_ignore, p_correct,
                confidence, query, answer, evidence, dt_to_str(utc_now()),
            ),
        )
        self.conn.commit()

    def feedback_events(self, memory_id: int | None = None) -> list[sqlite3.Row]:
        if memory_id is None:
            return self.conn.execute(
                "SELECT * FROM memory_feedback_events ORDER BY id"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM memory_feedback_events WHERE memory_id=? ORDER BY id",
            (memory_id,),
        ).fetchall()

    def index_version(self) -> int:
        row = self.conn.execute("SELECT value FROM memory_meta WHERE key='memory_index_version'").fetchone()
        return int(row["value"]) if row else 0

    def _bump_index_version(self) -> None:
        self.conn.execute(
            "INSERT INTO memory_meta(key, value) VALUES ('memory_index_version', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
            "WHERE key='memory_index_version'"
        )
