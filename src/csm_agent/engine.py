"""
CSM 引擎 — 简化版

核心循环：
  检索 → 语义相似度 × 强度 = 排名
  使用 → 强化（被检索命中的记忆自动强化）
  时间 → 衰减（自然遗忘）
  睡眠 → 归档低强度记忆 + 更新动态分层阈值
"""

from __future__ import annotations

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
    resolve_layer,
    update_dynamic_thresholds,
)


class CSMEngine:
    def __init__(self, db_path: str | Path = "csm_memory.db", embedding: EmbeddingBackend | None = None) -> None:
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
                existing.sensitivity = _max_sensitivity(existing.sensitivity, sensitivity)
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
            target.strength = reinforce(target)
            target.last_accessed_at = utc_now()
            target.access_count += 1
            return self.store.update(target)

        if op == MemoryOp.SUPERSEDE:
            replacement = self.add_memory(
                content=content,
                summary=summary,
                project_id=project_id or target.project_id,
                tags=tags,
                sensitivity=sensitivity,
            )
            inherit_from(replacement, target)  # 继承旧记忆的信任
            self.store.update(replacement)
            target.status = MemoryStatus.SUPERSEDED
            target.superseded_by = replacement.id
            self.store.update(target)
            self.store.add_link(target.id or 0, replacement.id or 0, "superseded_by")
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
        for memory in self.store.list_all():
            if memory.status != MemoryStatus.ACTIVE:
                continue
            if memory.project_id != project_id:
                continue
            if _normalize_memory_text(memory.content) == normalized:
                return memory
            semantic = cosine(query_vec, self.store.embedding.embed(memory.text_for_index))
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
        """睡眠整理：R 绝对值归档 + 更新动态分层阈值。

        FSRS 风格：每条记忆的归档由自身的可回忆概率（R）决定，
        而非与其他记忆比较的百分位阈值。
        默认 R < 0.01（1% 可回忆概率）时归档。
        """
        memories = self.store.list(include_archived=False)
        if not memories:
            return {"total": 0, "archived": 0, "layers": {}}

        # 更新动态阈值（仅用于层级统计/展示）
        strengths = [current_strength(m) for m in memories]
        thresholds = update_dynamic_thresholds(strengths)

        # R 绝对值归档阈值
        R_low = archive_threshold if archive_threshold is not None else 0.01
        archived = 0
        layer_counts: Counter[str] = Counter()
        for memory in memories:
            R = current_strength(memory)
            layer = resolve_layer(R)
            layer_counts[layer] += 1
            if memory.status == MemoryStatus.ACTIVE and R < R_low:
                memory.status = MemoryStatus.ARCHIVED
                memory.strength = R  # 同步衰减后的强度
                memory.last_accessed_at = utc_now()  # 重置衰减时钟，防止二重衰减
                self.store.update(memory)
                archived += 1

        return {
            "total": len(memories),
            "archived": archived,
            "layers": dict(layer_counts),
            "dynamic_thresholds": thresholds,
        }

    def health_report(self) -> dict[str, object]:
        """记忆健康报告。"""
        all_memories = self.store.list(include_archived=True)
        active_memories = [m for m in all_memories if m.status == MemoryStatus.ACTIVE]
        strengths = [current_strength(m) for m in active_memories]
        thresholds = update_dynamic_thresholds(strengths)

        layer_counts: Counter[str] = Counter()
        for m in active_memories:
            layer_counts[resolve_layer(current_strength(m))] += 1

        status_counts: Counter[str] = Counter(m.status.value for m in all_memories)
        tag_freq: Counter[str] = Counter()
        for m in all_memories:
            for tag in (m.tags or "").replace("，", ",").split(","):
                tag = tag.strip()
                if tag:
                    tag_freq[tag] += 1

        return {
            "total": len(all_memories),
            "active": len(active_memories),
            "layers": dict(layer_counts),
            "statuses": dict(status_counts),
            "common_tags": tag_freq.most_common(10),
            "dynamic_thresholds": thresholds,
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


def _max_sensitivity(left: str, right: str) -> str:
    rank = {"normal": 0, "personal": 1, "secret": 2}
    left_rank = rank.get(left, 0)
    right_rank = rank.get(right, 0)
    return left if left_rank >= right_rank else right
