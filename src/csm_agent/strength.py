"""
简化强度模型

两个基本力：
  - 时间衰减：strength *= exp(-DECAY_RATE * elapsed_days)
  - 访问强化：strength += (1 - strength) * REINFORCEMENT_GAIN

分层阈值动态计算（百分位）：
  - L1: 前 20%
  - L2: 20% ~ 60%
  - L3: 60% ~ 90%
  - COLD: 后 10%
"""

from __future__ import annotations

import math
import threading
from datetime import datetime

from .models import Memory, utc_now

# 统一衰减率（所有记忆相同，重要性由访问频率涌现）
DECAY_RATE: float = 0.02  # 每天衰减 2%

# 每次被使用的强化增益
REINFORCEMENT_GAIN: float = 0.15

# 默认初始强度
INITIAL_STRENGTH: float = 0.6


def elapsed_days(since: datetime | None, now: datetime | None = None) -> float:
    """计算自某个时间点以来的天数。"""
    if since is None:
        return 0.0
    now = now or utc_now()
    if since.tzinfo is None:
        since = since.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - since).total_seconds() / 86400.0)


def current_strength(memory: Memory, now: datetime | None = None) -> float:
    """计算记忆当前的强度（衰减后）。使用记忆自身的 decay_rate。"""
    days = elapsed_days(memory.last_accessed_at or memory.updated_at or memory.created_at, now)
    return max(0.0, min(1.0, memory.strength * math.exp(-memory.decay_rate * days)))


def reinforce(memory: Memory) -> float:
    """FSRS 风格强化：间隔效应 + 稳定性对数增长。

    核心洞见：复习越晚 (R 越低)，稳定性增益越大。
    R=1.0（刚用过）→ 增益 ≈ 0（无需重复强化）
    R=0.5（半忘状态）→ 增益最大
    R=0.1（几乎遗忘）→ 增益大但需重建（由 evolution.corrected 处理）

    同时更新 memory.decay_rate（稳定性 S 的对数增长），替代无界乘法累积。
    """
    R = current_strength(memory)

    # 间隔效应因子：(1 - R)^1.4，复习越晚增益越大
    spacing_factor = (1.0 - R) ** 1.4

    # 难度因子：trust 高（简单）→ 少涨，trust 低（困难）→ 多涨
    difficulty = max(0.05, 1.0 - memory.trust)

    gain = min(1.0, REINFORCEMENT_GAIN * spacing_factor * difficulty)

    # Stability 对数增长（有自然上限，不会无限趋近 0）
    if memory.decay_rate > 0:
        old_S = math.log(2) / memory.decay_rate  # 当前半衰期
    else:
        old_S = 30.0
    new_S = old_S * (1.0 + gain)
    memory.decay_rate = max(0.001, min(0.3, math.log(2) / new_S))

    return 1.0  # 刚强化后 R 重置为最大值


def compute_layer_thresholds(strengths: list[float]) -> dict[str, float]:
    """从当前强度分布动态计算分层阈值（百分位法）。

    样本不足时使用合理的固定阈值，确保新记忆不会被误判为 COLD。
    """
    if not strengths:
        return {"L1": 0.7, "L2": 0.4, "L3": 0.15}

    n = len(strengths)
    if n < 5:
        # 样本太少，百分位无意义，用固定阈值
        return {"L1": 0.7, "L2": 0.4, "L3": 0.15}

    sorted_s = sorted(strengths, reverse=True)

    def percentile(pct: float) -> float:
        idx = int(n * pct)
        return sorted_s[min(idx, n - 1)]

    l1 = percentile(0.20)
    l2 = percentile(0.60)
    l3 = percentile(0.90)
    # L1 上限保护：即使分布极端，0.85+ 的记忆永远是 L1
    l1 = min(l1, 0.85)
    # L3 阈值上限保护：新记忆(0.6)至少是 L3
    l3 = min(l3, 0.5)
    return {"L1": l1, "L2": l2, "L3": l3}


# 全局缓存的动态阈值（每次睡眠整理时更新）
_dynamic_thresholds: dict[str, float] = {"L1": 0.8, "L2": 0.4, "L3": 0.1}
_thresholds_lock = threading.Lock()


def resolve_layer(strength: float) -> str:
    """使用动态阈值解析记忆层级（线程安全）。"""
    with _thresholds_lock:
        L1, L2, L3 = _dynamic_thresholds["L1"], _dynamic_thresholds["L2"], _dynamic_thresholds["L3"]
    if strength >= L1:
        return "L1"
    if strength >= L2:
        return "L2"
    if strength >= L3:
        return "L3"
    return "COLD"


def update_dynamic_thresholds(strengths: list[float]) -> dict[str, float]:
    """更新全局动态阈值（线程安全）。"""
    global _dynamic_thresholds
    new_thresholds = compute_layer_thresholds(strengths)
    with _thresholds_lock:
        _dynamic_thresholds = new_thresholds
    return dict(new_thresholds)
