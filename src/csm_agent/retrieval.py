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
from typing import Iterable

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


class RetrievalMode(StrEnum):
    ANSWER_INJECTION = "answer_injection"     # 注入到回答上下文（只返回活跃记忆）
    WRITE_ARBITRATION = "write_arbitration"  # 写入仲裁（可返回已归档记忆供对比）


class HybridRetriever:
    """混合检索器 — 语义为主，关键词为辅。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

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

        # 收集候选记忆
        candidates: dict[int, tuple[Memory, float]] = {}
        for row in self._candidate_rows(project_id, mode):
            memory = Memory.from_row(row)
            semantic = cosine(query_vec, self.store.embedding_for_row(row))
            candidates[int(row["id"])] = (memory, semantic)

        # 计算最终得分：语义相似度 × 强度
        results: list[SearchResult] = []
        for memory, semantic in candidates.values():
            # 回答注入模式下跳过非活跃记忆
            if mode == RetrievalMode.ANSWER_INJECTION and memory.status != MemoryStatus.ACTIVE:
                continue

            strength = current_strength(memory, now)
            keyword = max(keyword_scores.get(memory.id or -1, 0.0), _lexical_overlap(query, memory.text_for_index))

            # 核心公式：语义相似度 × 强度 × (1 + 经验偏置)
            # 当语义匹配很低时，关键词匹配提供微弱信号
            final = semantic * strength * (1.0 + memory.boost)
            if keyword > 0:
                # 关键词匹配时适当提升
                final = max(final, 0.3 * keyword * strength * (1.0 + memory.boost))

            # 已替代/归档记忆降权（写入仲裁模式下仍可见）
            status_penalty = 1.0
            if memory.status in {MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED}:
                status_penalty = 0.3
            final *= status_penalty

            if mode == RetrievalMode.ANSWER_INJECTION and not _passes_answer_injection_gate(final, semantic, keyword):
                continue

            results.append(SearchResult(
                memory,
                final,
                semantic,
                keyword,
                strength,
            ))

        results.sort(key=lambda item: item.final_score, reverse=True)
        return _dedupe_results(results)[:limit]

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
