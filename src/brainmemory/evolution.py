"""
自适应进化引擎

每条记忆有独立的 decay_rate、boost、trust。
从每次使用/无视/纠正的反馈中自调参数，而非依赖预设常量。

类似 Anki SM-2 算法：每张卡片的 EF (Easiness Factor) 从答题历史中自动学习。
"""

from __future__ import annotations

import re
import math
from typing import Any

from .embedding import tokenize
from .models import Memory, MemoryStatus, MemoryWrite, MemoryWritePlan, utc_now
from .strength import current_strength, reinforce, stability_to_decay


# ── 自适应参数边界 ──────────────────────────────────────────

DECAY_MIN: float = 0.001    # 不能比这更慢（核心身份）
DECAY_MAX: float = 0.3      # 不能比这更快（比临时对话还快没必要）
BOOST_MIN: float = -0.8     # 最低偏向（被反复纠正的记忆）
BOOST_MAX: float = 1.0      # 最高偏向（被多次验证的核心记忆）
TRUST_MIN: float = 0.05     # 最低信任
TRUST_MAX: float = 0.98     # 最高信任（不给 1.0，留怀疑空间）


# ── 反馈检测 ────────────────────────────────────────────────

# 用户纠正信号
CORRECTION_RE = re.compile(
    r"不对|错了|纠正|改正|改回|应该是|其实是|实际上|不是|别再|不要|千万别|"
    r"wrong|actually|correction|instead|not\b|don'?t|never|mistake",
    re.IGNORECASE,
)


def detect_feedback(
    user_input: str,
    agent_output: str,
    retrieved_memories: list[dict[str, Any]],
    explicit_used_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """自动分析本轮对话中对每条已检索记忆的反馈。

    返回概率化反馈：action 可为 used/ignored/corrected/uncertain，并包含
    p_use、p_ignore、p_correct、confidence 与 evidence。
    """
    if not retrieved_memories:
        return []

    feedback: list[dict[str, Any]] = []
    user_lower = user_input.lower()
    agent_lower = agent_output.lower()

    was_correction = bool(CORRECTION_RE.search(user_input))
    explicit = set(explicit_used_ids or [])

    for mem in retrieved_memories:
        mid = mem.get("id")
        if mid is None:
            continue

        content = str(mem.get("content", "")).lower()
        if not content:
            continue

        tokens = _feedback_tokens(content)
        agent_tokens = _feedback_tokens(agent_lower)

        overlap = tokens & agent_tokens
        overlap_ratio = len(overlap) / max(1, min(len(tokens), len(agent_tokens)))
        jaccard = len(overlap) / max(1, len(tokens | agent_tokens))
        entailment_proxy = min(1.0, overlap_ratio * 2.0) if len(overlap) >= 2 else 0.0
        if int(mid) in explicit:
            p_use = 0.98
            evidence = "explicit memory id"
        else:
            p_use = _sigmoid(-2.5 + 3.0 * entailment_proxy + 1.5 * overlap_ratio + jaccard)
            evidence = f"overlap={len(overlap)},ratio={overlap_ratio:.3f}"

        contradicts = was_correction and _content_contradicts(user_lower, content)
        p_correct = 0.95 if contradicts else 0.0
        p_ignore = max(0.0, (1.0 - p_use) * (1.0 - p_correct))
        confidence = _feedback_confidence(p_use, p_ignore, p_correct)

        if p_correct >= 0.7:
            action = "corrected"
            evidence = "topic-matched user correction"
        elif p_use >= 0.75:
            action = "used"
        elif p_ignore >= 0.75:
            action = "ignored"
        else:
            action = "uncertain"

        feedback.append({
            "memory_id": int(mid),
            "action": action,
            "p_use": p_use,
            "p_ignore": p_ignore,
            "p_correct": p_correct,
            "confidence": confidence,
            "evidence": evidence,
        })

    return feedback


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _feedback_confidence(p_use: float, p_ignore: float, p_correct: float) -> float:
    values = [max(0.0, p_use), max(0.0, p_ignore), max(0.0, p_correct)]
    total = sum(values) or 1.0
    probabilities = [value / total for value in values]
    entropy = -sum(p * math.log(p) for p in probabilities if p > 0)
    return max(0.0, min(1.0, 1.0 - entropy / math.log(3)))


def _content_contradicts(user_text: str, memory_content: str) -> bool:
    """简单检测用户输入是否与记忆内容矛盾。

    如果用户说"改为/应该用/不再是 XX"且记忆中有对应的旧值，
    判断为矛盾。
    """
    contradict_signals = ["改为", "改用", "应该是", "应该用", "其实是", "不再是", "纠正", "改成",
                          "instead", "actually", "correction", "应当", "应该"]
    if not any(s in user_text for s in contradict_signals):
        return False

    user_tokens = _feedback_tokens(user_text)
    memory_tokens = _feedback_tokens(memory_content)
    if user_tokens & memory_tokens:
        return True

    domains = (
        {"名字", "姓名", "称呼", "我叫", "name", "call"},
        {"依赖", "安装", "包管理", "bun", "pnpm", "npm", "yarn", "pip", "install"},
        {"数据库", "mysql", "postgresql", "sqlite", "redis", "database"},
        {"回答", "回复", "简洁", "详细", "风格", "answer", "response", "style"},
    )
    return any(
        any(term in user_text for term in domain)
        and any(term in memory_content for term in domain)
        for domain in domains
    )


def _feedback_tokens(text: str) -> set[str]:
    """Keep distinctive terms; single CJK characters create too many false hits."""
    stop = {
        "这个", "那个", "项目", "用户", "使用", "应该", "可以", "已经",
        "the", "a", "an", "is", "are", "use", "uses", "user", "project",
    }
    return {
        token
        for token in tokenize(text)
        if token not in stop and (len(token) >= 2 or token.isascii())
    }


# ── 参数自适应 ─────────────────────────────────────────────

def apply_feedback(
    memory: Memory,
    action: str,
    *,
    p_use: float | None = None,
    p_ignore: float | None = None,
    p_correct: float | None = None,
    confidence: float = 1.0,
    now=None,
) -> Memory:
    """根据反馈调整记忆的自适应参数。

    自适应反馈：反馈效果与当前可回忆概率（R）关联。
      - used：正确使用 → 增强 boost/trust
      - ignored：R 高时被无视 = 确实不相关，降权更多；R 低时被无视 = 可能忘了，温和处理
      - corrected：用户纠正 → 快速衰减
    """
    p_use = float(action == "used") if p_use is None else p_use
    p_ignore = float(action == "ignored") if p_ignore is None else p_ignore
    p_correct = float(action == "corrected") if p_correct is None else p_correct
    R = current_strength(memory, now=now)
    if action == "used":
        memory.boost = min(BOOST_MAX, memory.boost + 0.05 * p_use)
        memory.trust_alpha += 0.1 * p_use
        memory.verify_count += 1
        memory.utility = (1.0 - 0.1) * memory.utility + 0.1 * p_use
        memory.strength = reinforce(memory, now=now, probability=p_use)
        memory.last_accessed_at = now or utc_now()
        memory.access_count += 1

    elif action == "ignored":
        penalty = 0.02 * confidence * p_ignore
        memory.boost = max(BOOST_MIN, memory.boost - penalty)
        memory.stability /= 1.0 + penalty
        memory.decay_rate = stability_to_decay(memory.stability)
        memory.difficulty = min(1.0, memory.difficulty + 0.03 * confidence * p_ignore)
        memory.utility = max(0.0, 0.9 * memory.utility)

    elif action == "corrected":
        memory.boost = max(BOOST_MIN, memory.boost - 0.2 * p_correct)
        memory.trust_beta += 2.0 * p_correct
        memory.error_count += 1
        memory.correction_count += 1
        memory.stability /= 1.0 + 0.5 * p_correct
        memory.decay_rate = stability_to_decay(memory.stability)
        memory.difficulty = min(1.0, memory.difficulty + 0.15 * p_correct)
        memory.utility = max(0.0, memory.utility - 0.2 * p_correct)

    memory.sync_trust()

    return memory


# ── 引擎集成 ───────────────────────────────────────────────

class EvolutionEngine:
    """自适应进化引擎：检测反馈 + 调整参数。"""

    def __init__(self, store: Any = None) -> None:
        self.store = store
        self._cycles: int = 0
        self.last_feedback: list[dict[str, Any]] = []

    def process_turn(
        self,
        user_input: str,
        agent_output: str,
        retrieved_memories: list[dict[str, Any]],
        explicit_used_ids: list[int] | None = None,
    ) -> list[Memory]:
        """处理一轮对话的反馈，返回被修改的记忆列表。"""
        self._cycles += 1
        if not self.store:
            return []

        feedback = detect_feedback(
            user_input,
            agent_output,
            retrieved_memories,
            explicit_used_ids=explicit_used_ids,
        )
        self.last_feedback = feedback
        updated: list[Memory] = []

        for fb in feedback:
            memory = self.store.get(fb["memory_id"])
            if memory is None or memory.status != MemoryStatus.ACTIVE:
                continue

            apply_feedback(
                memory,
                fb["action"],
                p_use=fb["p_use"],
                p_ignore=fb["p_ignore"],
                p_correct=fb["p_correct"],
                confidence=fb["confidence"],
            )
            memory.updated_at = utc_now()
            self.store.update(memory)
            self.store.record_feedback_event(
                memory.id,
                fb["action"],
                fb["p_use"],
                fb["p_ignore"],
                fb["p_correct"],
                fb["confidence"],
                query=user_input,
                answer=agent_output,
                evidence=fb["evidence"],
            )
            updated.append(memory)

        return updated

    def record_manual_correction(self, memory_id: int) -> Memory | None:
        """手动标记一条记忆为错误。"""
        if not self.store:
            return None
        memory = self.store.get(memory_id)
        if memory is None:
            return None
        apply_feedback(memory, "corrected")
        memory.updated_at = utc_now()
        self.store.update(memory)
        return memory

    def record_manual_verify(self, memory_id: int) -> Memory | None:
        """手动标记一条记忆为已验证。"""
        if not self.store:
            return None
        memory = self.store.get(memory_id)
        if memory is None:
            return None
        memory.verify_count += 1
        memory.trust_alpha += 1.0
        memory.sync_trust()
        memory.stability *= 1.1
        memory.decay_rate = stability_to_decay(memory.stability)
        memory.updated_at = utc_now()
        self.store.update(memory)
        return memory


# ── 继承信任 ────────────────────────────────────────────────

def inherit_from(memory: Memory, parent: Memory | None) -> Memory:
    """新记忆从被替代的旧记忆继承信任度。

    Supersede 时调用：新记忆应该继承旧记忆的信任，
    而不是从零开始——因为纠正本身就是"从错误中学习"。
    """
    if parent is None:
        return memory
    parent.ensure_trust_distribution()
    memory.trust_alpha = 2.0 + 0.25 * parent.trust_alpha
    memory.trust_beta = 2.0 + 0.5 * parent.trust_beta
    memory.sync_trust()
    memory.verify_count = max(0, parent.verify_count - 1)
    memory.error_count = parent.error_count      # 保留错误历史
    memory.correction_count = parent.correction_count
    memory.stability = 0.5 * memory.stability + 0.5 * parent.stability
    memory.decay_rate = stability_to_decay(memory.stability)
    memory.difficulty = min(1.0, 0.5 * memory.difficulty + 0.5 * parent.difficulty)
    memory.utility = 0.5 * memory.utility + 0.5 * parent.utility
    memory.boost = max(memory.boost, min(0.2, parent.boost + 0.05))
    if not memory.tags:
        memory.tags = parent.tags
    return memory
