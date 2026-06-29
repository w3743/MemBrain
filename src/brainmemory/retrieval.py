"""
简化检索器

核心理念：语义相近 + 经常被访问 = 排名靠前。

检索公式：
  score = semantic_similarity(query, memory) × current_strength(memory)

不需要手工权重、不需要类型映射、不需要关键词规则。
FTS5 关键词搜索仅作为语义匹配的补充（当纯语义相似度太低时介入）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import math

from .embedding import cosine, tokenize
from .models import Memory, MemoryStatus
from .store import MemoryStore
from .strength import current_strength

MIN_ANSWER_FINAL_SCORE = 0.05
MIN_ANSWER_SEMANTIC = 0.35
MIN_ANSWER_KEYWORD = 0.10


@dataclass(slots=True)
class SearchResult:
    memory: Memory
    final_score: float
    semantic_similarity: float
    keyword_score: float
    current_strength: float
    trust_score: float = 0.5
    utility_score: float = 0.5
    interference: float = 0.0


class RetrievalMode(StrEnum):
    ANSWER_INJECTION = "answer_injection"     # 注入到回答上下文（只返回活跃记忆）
    WRITE_ARBITRATION = "write_arbitration"  # 写入仲裁（可返回已归档记忆供对比）


class HybridRetriever:
    """混合检索器 — 语义为主，关键词为辅。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._vector_cache_version = -1
        self._vector_cache: dict[int, list[float]] = {}
        self._vector_positions: dict[int, int] = {}
        self._vector_matrix = None

    def search(
        self,
        query: str,
        project_id: str | None = None,
        limit: int = 8,
        now: datetime | None = None,
        mode: RetrievalMode = RetrievalMode.ANSWER_INJECTION,
    ) -> list[SearchResult]:
        query_vec = self.store.embedding.embed(query)

        # FTS5 关键词搜索（作为语义的补充信号）
        keyword_scores = self.store.keyword_search(query, limit=50)

        # Dense top-k + sparse top-k + high-utility candidates.
        rows = self._candidate_rows(project_id, mode)
        semantic_scores = self._semantic_scores(query_vec, rows)
        dense_ids = {
            memory_id
            for memory_id, _ in sorted(
                semantic_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:100]
        }
        utility_ids = {
            int(row["id"])
            for row in sorted(
                rows,
                key=lambda row: float(row["utility"]),
                reverse=True,
            )[:30]
        }
        candidate_ids = dense_ids | set(keyword_scores) | utility_ids
        candidates: dict[int, tuple[Memory, float]] = {}
        for row in rows:
            if int(row["id"]) not in candidate_ids:
                continue
            memory = Memory.from_row(row)
            semantic = semantic_scores.get(int(row["id"]), 0.0)
            candidates[int(row["id"])] = (memory, semantic)

        # 计算最终得分：语义相似度 × 强度
        results: list[SearchResult] = []
        for memory, semantic in candidates.values():
            # 回答注入模式下跳过非活跃记忆
            if mode == RetrievalMode.ANSWER_INJECTION and memory.status != MemoryStatus.ACTIVE:
                continue
            interference = self._interference(memory, rows, now)
            strength = current_strength(memory, now, interference=interference)
            keyword = max(keyword_scores.get(memory.id or -1, 0.0), _lexical_overlap(query, memory.text_for_index))
            conflict_risk = interference
            if memory.status in {MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED}:
                conflict_risk = min(1.0, conflict_risk + 0.7)
            z = (
                -2.0
                + 3.0 * semantic
                + 1.2 * keyword
                + 1.2 * strength
                + 0.5 * memory.trust_mean
                + 0.8 * memory.utility
                + 0.4 * memory.boost
                - 1.5 * conflict_risk
            )
            final = 1.0 / (1.0 + math.exp(-z))

            if mode == RetrievalMode.ANSWER_INJECTION and not _passes_answer_injection_gate(final, semantic, keyword):
                continue

            results.append(SearchResult(
                memory,
                final,
                semantic,
                keyword,
                strength,
                memory.trust_mean,
                memory.utility,
                interference,
            ))

        results.sort(key=lambda item: item.final_score, reverse=True)
        return self._mmr_select(_dedupe_results(results), limit)

    def _interference(self, memory: Memory, rows: list, now: datetime | None) -> float:
        if memory.id is None:
            return 0.0
        source = self._vector_cache.get(memory.id, [])
        if not source:
            return 0.0
        reference_time = now or datetime.now(memory.created_at.tzinfo if memory.created_at else None)
        total = 0.0
        for row in rows:
            other_id = int(row["id"])
            if other_id == memory.id or row["status"] != MemoryStatus.ACTIVE.value:
                continue
            other = Memory.from_row(row)
            if not other.created_at or not memory.created_at or other.created_at <= memory.created_at:
                continue
            conflict = _conflict_probability(memory.content, other.content)
            if conflict <= 0:
                continue
            similarity = cosine(source, self._vector_cache.get(other_id, []))
            age_days = max(0.0, (reference_time - other.created_at).total_seconds() / 86400.0)
            total += similarity * similarity * conflict * math.exp(-age_days / 30.0)
        return min(1.0, total)

    def interference_for(self, memory: Memory, now: datetime | None = None) -> float:
        rows = self._candidate_rows(memory.project_id, RetrievalMode.WRITE_ARBITRATION)
        if memory.id is None:
            return 0.0
        vector = self.store.conn.execute(
            "SELECT embedding FROM memories WHERE id=?",
            (memory.id,),
        ).fetchone()
        if vector is None:
            return 0.0
        source = self.store.embedding_for_row(vector)
        self._semantic_scores(source, rows)
        return self._interference(memory, rows, now)

    def _mmr_select(self, results: list[SearchResult], limit: int) -> list[SearchResult]:
        selected: list[SearchResult] = []
        remaining = list(results)
        while remaining and len(selected) < limit:
            best = max(
                remaining,
                key=lambda item: item.final_score - 0.25 * max(
                    (
                        cosine(
                            self._vector_cache.get(item.memory.id or -1, []),
                            self._vector_cache.get(chosen.memory.id or -1, []),
                        )
                        for chosen in selected
                    ),
                    default=0.0,
                ),
            )
            selected.append(best)
            remaining.remove(best)
        return selected

    def _semantic_scores(self, query_vec: list[float], rows: list) -> dict[int, float]:
        """Vectorized cosine scoring with an index-versioned embedding cache."""
        version = self.store.index_version()
        if version != self._vector_cache_version:
            all_rows = self.store.conn.execute("SELECT id, embedding FROM memories").fetchall()
            self._vector_cache = {
                int(row["id"]): self.store.embedding_for_row(row)
                for row in all_rows
            }
            self._vector_positions = {
                memory_id: position
                for position, memory_id in enumerate(self._vector_cache)
            }
            try:
                import numpy as np

                self._vector_matrix = np.asarray(
                    list(self._vector_cache.values()),
                    dtype=np.float32,
                )
            except (ImportError, TypeError, ValueError):
                self._vector_matrix = None
            self._vector_cache_version = version

        ids = [int(row["id"]) for row in rows]
        vectors = [self._vector_cache.get(memory_id, []) for memory_id in ids]
        if not ids or not query_vec:
            return {}

        try:
            import numpy as np

            if self._vector_matrix is None:
                raise ValueError("vector matrix is unavailable")
            positions = [self._vector_positions[memory_id] for memory_id in ids]
            matrix = self._vector_matrix[positions]
            query = np.asarray(query_vec, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[1] != query.shape[0]:
                raise ValueError("embedding dimensions do not match")
            scores = np.maximum(0.0, matrix @ query)
            return {memory_id: float(score) for memory_id, score in zip(ids, scores)}
        except (ImportError, TypeError, ValueError):
            return {
                memory_id: cosine(query_vec, vector)
                for memory_id, vector in zip(ids, vectors)
            }

    def _candidate_rows(self, project_id: str | None, mode: RetrievalMode) -> list:
        """获取候选记忆行。写入仲裁模式下扩大候选范围。"""
        if mode == RetrievalMode.WRITE_ARBITRATION:
            # 写入仲裁：包含已归档/已替代的记忆，供 LLM 对比
            if project_id:
                return self.store.conn.execute(
                    "SELECT * FROM memories WHERE status!='deleted' AND (project_id=? OR project_id IS NULL) ORDER BY access_count DESC",
                    (project_id,),
                ).fetchall()
            return self.store.conn.execute(
                "SELECT * FROM memories WHERE status!='deleted' ORDER BY access_count DESC"
            ).fetchall()
        else:
            # 回答注入：仅活跃记忆
            if project_id:
                return self.store.conn.execute(
                    "SELECT * FROM memories WHERE status='active' AND (project_id=? OR project_id IS NULL)",
                    (project_id,),
                ).fetchall()
            return self.store.conn.execute(
                "SELECT * FROM memories WHERE status='active'"
            ).fetchall()


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """去重：相同内容前缀的记忆只保留得分最高的。"""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = (result.memory.content or result.memory.summary)[:160].strip()
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _lexical_overlap(query: str, memory_text: str) -> float:
    query_tokens = _expanded_query_tokens(query)
    memory_tokens = set(tokenize(memory_text))
    if not query_tokens or not memory_tokens:
        return 0.0
    overlap = query_tokens & memory_tokens
    if not overlap:
        return 0.0
    return min(1.0, len(overlap) / max(1, min(len(query_tokens), len(memory_tokens))))


def _passes_answer_injection_gate(final_score: float, semantic: float, keyword: float) -> bool:
    """Only inject memory into an answer when it is clearly related.

    This gate is intentionally applied only to answer injection. Write arbitration
    may still inspect weak or archived candidates so the LLM can compare and
    decide whether to update, supersede, or ignore them.
    """
    if keyword >= MIN_ANSWER_KEYWORD and final_score >= 0.02:
        return True
    if semantic >= MIN_ANSWER_SEMANTIC and final_score >= MIN_ANSWER_FINAL_SCORE:
        return True
    return False


def _expanded_query_tokens(query: str) -> set[str]:
    tokens = set(tokenize(query))
    text = query.lower()
    if any(term in text for term in ["称呼", "名字", "姓名", "叫我", "叫你", "name", "call me"]):
        tokens.update({"称", "呼", "称呼", "名", "字", "名字", "叫", "我", "你"})
    if any(term in text for term in ["偏好", "习惯", "风格", "preference", "style"]):
        tokens.update({"偏", "好", "偏好", "风", "格", "风格", "回答", "回复"})
    return tokens


def _conflict_probability(left: str, right: str) -> float:
    left_lower = left.lower()
    right_lower = right.lower()
    domains = (
        {"bun", "pnpm", "npm", "yarn", "pip"},
        {"mysql", "postgresql", "sqlite", "redis"},
        {"简洁", "详细", "concise", "detailed"},
    )
    for domain in domains:
        left_values = {value for value in domain if value in left_lower}
        right_values = {value for value in domain if value in right_lower}
        if left_values and right_values and left_values != right_values:
            return 0.9
    correction_terms = ("改用", "改成", "不再", "instead", "replaced", "now uses")
    if any(term in right_lower for term in correction_terms):
        overlap = set(tokenize(left_lower)) & set(tokenize(right_lower))
        if overlap:
            return 0.7
    return 0.0
