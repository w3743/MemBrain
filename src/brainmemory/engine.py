"""
CSM 引擎 — 简化版

核心循环：
  检索 → 语义相似度 × 强度 = 排名
  使用 → 强化（被检索命中的记忆自动强化）
  时间 → 衰减（自然遗忘）
  睡眠 → 归档低强度记忆 + 更新动态分层阈值
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .embedding import EmbeddingBackend, cosine, tokenize
from .evolution import EvolutionEngine, inherit_from
from .models import Memory, MemoryOp, MemoryStatus, MemoryWrite, utc_now
from .retrieval import HybridRetriever, RetrievalMode, SearchResult
from .store import MemoryStore
from .strength import (
    INITIAL_STRENGTH,
    current_strength,
    reinforce,
    ARCHIVE_THRESHOLD,
)


class BrainMemoryEngine:
    def __init__(self, db_path: str | Path = "brainmemory.db", embedding: EmbeddingBackend | None = None) -> None:
        self.store = MemoryStore(db_path, embedding=embedding)
        self.retriever = HybridRetriever(self.store)
        self.evolution = EvolutionEngine(self.store)

    def close(self) -> None:
        self.store.close()

    # ── 添加记忆 ──────────────────────────────────────────────

    def add_memory(
        self,
        content: str,
        summary: str = "",
        project_id: str | None = None,
        tags: str = "",
        sensitivity: str = "normal",
    ) -> Memory:
        """添加一条新记忆。strength 从 INITIAL_STRENGTH 开始。"""
        memory = Memory(
            id=None,
            content=content,
            summary=summary or content[:120],
            strength=INITIAL_STRENGTH,
            project_id=project_id,
            tags=tags,
            sensitivity=sensitivity,
        )
        return self.store.add(memory)

    # ── 执行操作 ──────────────────────────────────────────────

    def apply_operation(
        self,
        op: MemoryOp,
        content: str = "",
        target_id: int | None = None,
        project_id: str | None = None,
        summary: str = "",
        tags: str = "",
        sensitivity: str = "normal",
    ) -> Memory | None:
        """执行单条 MemoryWrite 对应的操作。"""
        if op == MemoryOp.NOOP:
            return None

        if op == MemoryOp.ADD:
            existing = self._find_duplicate_memory(content, project_id)
            if existing is not None:
                existing.summary = summary or existing.summary
                existing.tags = _merge_tags(existing.tags, tags)
                existing.sensitivity = sensitivity
                existing.strength = reinforce(existing)
                existing.last_accessed_at = utc_now()
                existing.access_count += 1
                return self.store.update(existing)
            return self.add_memory(
                content=content,
                summary=summary,
                project_id=project_id,
                tags=tags,
                sensitivity=sensitivity,
            )

        if target_id is None:
            raise ValueError(f"{op} requires target_id")

        target = self.store.get(target_id)
        if target is None:
            raise ValueError(f"memory {target_id} not found")

        if op == MemoryOp.UPDATE:
            target.content = content or target.content
            target.summary = summary or target.summary
            target.tags = tags or target.tags
            target.sensitivity = sensitivity or target.sensitivity
            target.strength = reinforce(target)
            target.last_accessed_at = utc_now()
            target.access_count += 1
            return self.store.update(target)

        if op == MemoryOp.SUPERSEDE:
            replacement = Memory(
                id=None,
                content=content,
                summary=summary or content[:120],
                strength=INITIAL_STRENGTH,
                project_id=project_id or target.project_id,
                tags=tags,
                sensitivity=sensitivity,
            )
            inherit_from(replacement, target)  # 继承旧记忆的信任
            try:
                self.store.conn.execute("BEGIN")
                self.store.add(replacement, commit=False)
                self.store.delete(target_id, commit=False)
                self.store.conn.commit()
            except Exception:
                self.store.conn.rollback()
                raise
            return replacement

        if op == MemoryOp.ARCHIVE:
            target.status = MemoryStatus.ARCHIVED
            return self.store.update(target)

        if op == MemoryOp.DELETE:
            self.store.delete(target_id)
            target.status = MemoryStatus.DELETED
            return target

        raise ValueError(f"unsupported operation: {op}")

    def _find_duplicate_memory(self, content: str, project_id: str | None = None) -> Memory | None:
        normalized = _normalize_memory_text(content)
        if not normalized:
            return None
        query_vec = self.store.embedding.embed(content)
        best: tuple[float, Memory] | None = None
        rows = self.store.conn.execute(
            "SELECT * FROM memories WHERE status='active' AND project_id IS ?",
            (project_id,),
        ).fetchall()
        for row in rows:
            memory = Memory.from_row(row)
            if memory.status != MemoryStatus.ACTIVE:
                continue
            if _normalize_memory_text(memory.content) == normalized:
                return memory
            semantic = cosine(query_vec, self.store.embedding_for_row(row))
            lexical = _token_overlap(content, memory.text_for_index)
            if semantic >= 0.92 and lexical >= 0.45:
                score = semantic + lexical
                if best is None or score > best[0]:
                    best = (score, memory)
        return best[1] if best else None

    # ── 检索 ──────────────────────────────────────────────────

    def search(
        self,
        query: str,
        project_id: str | None = None,
        limit: int = 8,
        mode: RetrievalMode = RetrievalMode.ANSWER_INJECTION,
    ) -> list[SearchResult]:
        """检索记忆。检索本身只读，使用反馈由 post_run 或显式强化记录。"""
        return self.retriever.search(query, project_id=project_id, limit=limit, mode=mode)

    # ── 显式强化 ──────────────────────────────────────────────

    def reinforce_used(self, memory_id: int) -> Memory:
        """手动强化一条记忆（例如用户明确使用了它）。"""
        memory = self.store.get(memory_id)
        if not memory:
            raise ValueError(f"memory {memory_id} not found")
        memory.strength = reinforce(memory)
        memory.last_accessed_at = utc_now()
        memory.access_count += 1
        return self.store.update(memory)

    # ── 睡眠整理 ──────────────────────────────────────────────

    def sleep_consolidate(self, archive_threshold: float | None = None) -> dict[str, object]:
        """Archive weak memories and physically remove superseded records."""
        memories = self.store.list(include_archived=False)
        if not memories:
            return {"total": 0, "archived": 0, "deleted_superseded": 0}

        R_low = archive_threshold if archive_threshold is not None else ARCHIVE_THRESHOLD
        archived = 0
        deleted_superseded = 0
        for memory in memories:
            if memory.status == MemoryStatus.SUPERSEDED:
                if memory.id is not None and self.store.delete(memory.id):
                    deleted_superseded += 1
                continue
            R = current_strength(
                memory,
                interference=self.retriever.interference_for(memory),
            )
            age_days = (
                (utc_now() - memory.created_at).total_seconds() / 86400.0
                if memory.created_at else 0.0
            )
            effective_utility = memory.utility * math.exp(-age_days / 90.0)
            if (
                memory.status == MemoryStatus.ACTIVE
                and R < R_low
                and age_days > 7.0
                and effective_utility < 0.4
            ):
                memory.status = MemoryStatus.ARCHIVED
                memory.strength = R
                memory.last_accessed_at = utc_now()
                self.store.update(memory)
                archived += 1

        return {
            "total": len(memories),
            "archived": archived,
            "deleted_superseded": deleted_superseded,
        }

    def health_report(self) -> dict[str, object]:
        """Memory health report."""
        all_memories = self.store.list(include_archived=True)
        active_memories = [m for m in all_memories if m.status == MemoryStatus.ACTIVE]

        status_counts: Counter[str] = Counter(m.status.value for m in all_memories)
        tag_freq: Counter[str] = Counter()
        for m in all_memories:
            for tag in (m.tags or "").replace("，", ",").split(","):
                tag = tag.strip()
                if tag:
                    tag_freq[tag] += 1

        # Strength distribution stats
        strengths = [
            current_strength(m, interference=self.retriever.interference_for(m))
            for m in active_memories
        ]
        avg_strength = sum(strengths) / len(strengths) if strengths else 0.0
        max_strength = max(strengths) if strengths else 0.0
        min_strength = min(strengths) if strengths else 0.0
        avg_stability = (
            sum(m.stability for m in active_memories) / len(active_memories)
            if active_memories else 0.0
        )
        avg_difficulty = (
            sum(m.difficulty for m in active_memories) / len(active_memories)
            if active_memories else 0.0
        )
        avg_utility = (
            sum(m.utility for m in active_memories) / len(active_memories)
            if active_memories else 0.0
        )
        feedback_count = int(
            self.store.conn.execute("SELECT COUNT(*) FROM memory_feedback_events").fetchone()[0]
        )

        return {
            "total": len(all_memories),
            "active": len(active_memories),
            "statuses": dict(status_counts),
            "common_tags": tag_freq.most_common(10),
            "avg_strength": round(avg_strength, 4),
            "max_strength": round(max_strength, 4),
            "min_strength": round(min_strength, 4),
            "avg_stability": round(avg_stability, 4),
            "avg_difficulty": round(avg_difficulty, 4),
            "avg_utility": round(avg_utility, 4),
            "feedback_events": feedback_count,
        }

    def reindex_embeddings(self) -> dict[str, object]:
        count = self.store.reindex_embeddings()
        return {
            "reindexed": count,
            "embedding_backend": getattr(self.store.embedding, "name", self.store.embedding.__class__.__name__),
            "memory_index_version": self.store.index_version(),
        }


def _normalize_memory_text(text: str) -> str:
    return "".join(tokenize(text))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _merge_tags(left: str, right: str) -> str:
    tags: list[str] = []
    seen: set[str] = set()
    for raw_group in (left or "", right or ""):
        for raw in raw_group.replace("，", ",").split(","):
            tag = raw.strip()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return ",".join(tags)
