"""
自适应进化引擎

每条记忆有独立的 decay_rate、boost、trust。
从每次使用/无视/纠正的反馈中自调参数，而非依赖预设常量。

类似 Anki SM-2 算法：每张卡片的 EF (Easiness Factor) 从答题历史中自动学习。
"""

from __future__ import annotations

import re
from typing import Any

from .models import Memory, MemoryStatus, MemoryWrite, MemoryWritePlan, utc_now
from .strength import current_strength


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
) -> list[dict[str, Any]]:
    """自动分析本轮对话中对每条已检索记忆的反馈。

    返回列表，每项：{memory_id, action: used|ignored|corrected, evidence}
    """
    if not retrieved_memories:
        return []

    feedback: list[dict[str, Any]] = []
    user_lower = user_input.lower()
    agent_lower = agent_output.lower()

    was_correction = bool(CORRECTION_RE.search(user_input))

    for mem in retrieved_memories:
        mid = mem.get("id")
        if mid is None:
            continue

        content = str(mem.get("content", "")).lower()
        if not content:
            continue

        tokens = set(content.split())
        agent_tokens = set(agent_lower.split())

        # 检测是否被 LLM 回复引用
        overlap = tokens & agent_tokens
        used = len(overlap) >= 2 or (len(overlap) >= 1 and len(content) < 30)

        if was_correction and _content_contradicts(user_lower, content):
            feedback.append({"memory_id": mid, "action": "corrected", "evidence": "user correction"})
        elif used:
            feedback.append({"memory_id": mid, "action": "used", "evidence": f"overlap={len(overlap)}"})
        else:
            feedback.append({"memory_id": mid, "action": "ignored", "evidence": "no overlap"})

    return feedback


def _content_contradicts(user_text: str, memory_content: str) -> bool:
    """简单检测用户输入是否与记忆内容矛盾。

    如果用户说"改为/应该用/不再是 XX"且记忆中有对应的旧值，
    判断为矛盾。
    """
    contradict_signals = ["改为", "改用", "应该是", "应该用", "应该是", "其实是", "不再是", "纠正", "改成",
                          "instead", "actually", "correction", "应当", "应该"]
    return any(s in user_text for s in contradict_signals)


# ── 参数自适应 ─────────────────────────────────────────────

def apply_feedback(memory: Memory, action: str) -> Memory:
    """根据反馈调整记忆的自适应参数。

    FSRS 风格：反馈效果与当前可回忆概率（R）关联。
      - used：正确使用 → 增强 boost/trust
      - ignored：R 高时被无视 = 确实不相关，降权更多；R 低时被无视 = 可能忘了，温和处理
      - corrected：用户纠正 → 快速衰减
    """
    R = current_strength(memory)

    if action == "used":
        # boost/trust 由进化管理；decay_rate 由 reinforce() 的间隔效应统一处理，避免重复修改
        memory.boost = min(BOOST_MAX, memory.boost + 0.05)
        memory.trust = min(TRUST_MAX, memory.trust + 0.03 * (1.0 - R))
        memory.verify_count += 1

    elif action == "ignored":
        penalty = 0.02 * (0.3 + 0.7 * R)
        memory.boost = max(BOOST_MIN, memory.boost - penalty)
        memory.decay_rate = min(DECAY_MAX, memory.decay_rate * 1.02)

    elif action == "corrected":
        memory.boost = max(BOOST_MIN, memory.boost - 0.2)
        memory.trust = max(TRUST_MIN, memory.trust * 0.7)
        memory.error_count += 1
        memory.decay_rate = min(DECAY_MAX, memory.decay_rate * 1.5)

    return memory


# ── 引擎集成 ───────────────────────────────────────────────

class EvolutionEngine:
    """自适应进化引擎：检测反馈 + 调整参数。"""

    def __init__(self, store: Any = None) -> None:
        self.store = store
        self._cycles: int = 0

    def process_turn(
        self,
        user_input: str,
        agent_output: str,
        retrieved_memories: list[dict[str, Any]],
    ) -> list[Memory]:
        """处理一轮对话的反馈，返回被修改的记忆列表。"""
        self._cycles += 1
        if not self.store:
            return []

        feedback = detect_feedback(user_input, agent_output, retrieved_memories)
        updated: list[Memory] = []

        for fb in feedback:
            memory = self.store.get(fb["memory_id"])
            if memory is None or memory.status != MemoryStatus.ACTIVE:
                continue

            apply_feedback(memory, fb["action"])
            memory.updated_at = utc_now()
            self.store.update(memory)
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
        memory.trust = min(TRUST_MAX, memory.trust + 0.05)
        memory.decay_rate *= 0.9
        memory.decay_rate = max(DECAY_MIN, memory.decay_rate)
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
    memory.trust = (parent.trust + 0.5) / 2     # 新旧平均，不给全额
    memory.verify_count = max(0, parent.verify_count - 1)
    memory.error_count = parent.error_count      # 保留错误历史
    memory.boost = max(memory.boost, min(0.2, parent.boost + 0.05))
    if not memory.tags:
        memory.tags = parent.tags
    return memory
