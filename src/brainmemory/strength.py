"""
连续强度模型

两个基本力：
  - 自适应衰减：有效衰减率 = decay_rate × (2.0 - R)，越弱忘越快
  - 访问强化：new = R + 0.35 × (1 - R)，渐进逼近 1.0

不再使用 L1/L2/L3 分层——直接用连续强度 R（0.0-1.0）表示记忆质量。
"""

from __future__ import annotations

import math
from datetime import datetime

from .models import Memory, utc_now

# 基础衰减率
DECAY_RATE: float = 0.02  # 每天基础衰减 2%

# 每次被使用的即时可回忆强度增益
REINFORCEMENT_GAIN: float = 0.35

# 稳定性学习幅度。实际增益还会随“回忆难度”自适应变化。
STABILITY_GAIN: float = 0.45
STABILITY_MAX_DAYS: float = 730.0
DIFFICULTY_DECAY_WEIGHT: float = 0.25
INTERFERENCE_DECAY_WEIGHT: float = 0.6

# 默认初始强度
INITIAL_STRENGTH: float = 0.6

# 归档阈值：R 低于此值的记忆自动归档
ARCHIVE_THRESHOLD: float = 0.2


def elapsed_days(since: datetime | None, now: datetime | None = None) -> float:
    """计算自某个时间点以来的天数。"""
    if since is None:
        return 0.0
    now = now or utc_now()
    if since.tzinfo is None:
        since = since.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - since).total_seconds() / 86400.0)


def decay_to_stability(decay_rate: float) -> float:
    return math.log(3) / (2.0 * max(0.001, min(0.3, decay_rate)))


def stability_to_decay(stability: float) -> float:
    return max(0.001, min(0.3, math.log(3) / (2.0 * max(0.1, stability))))


def current_strength(
    memory: Memory,
    now: datetime | None = None,
    interference: float = 0.0,
) -> float:
    """计算记忆当前的强度（自适应衰减后）。

    有效衰减率 = decay_rate × (2.0 - R_at_time)
    越弱的记忆忘得越快，形成"富者愈富"的正反馈。
    """
    days = elapsed_days(memory.last_accessed_at or memory.updated_at or memory.created_at, now)
    if days <= 0:
        return memory.strength

    # 自适应衰减：effective_d = decay_rate × (2 - R)
    # R 高（接近 1.0）→ effective_d ≈ decay_rate（慢忘）
    # R 低（接近 0.0）→ effective_d ≈ 2 × decay_rate（快忘）
    s0 = memory.strength
    stability = max(0.1, memory.stability)
    difficulty_factor = 1.0 + DIFFICULTY_DECAY_WEIGHT * max(0.0, min(1.0, memory.difficulty))
    interference_factor = 1.0 + INTERFERENCE_DECAY_WEIGHT * max(0.0, min(1.0, interference))
    d = stability_to_decay(stability) * difficulty_factor * interference_factor
    # 解微分方程 dR/dt = -d * (2-R) * R 的近似：分段数值积分
    # 简化为：effective_d = d * (2 - average_R)
    # 使用中点近似
    # 精确解：R(t) = 2 / (1 + (2/s0 - 1) * exp(2*d*t))
    # 推导：dR/dt = -d * (2-R) * R，分离变量 → 积分
    if s0 >= 2.0:
        return 1.0
    if s0 <= 0.0:
        return 0.0

    coeff = (2.0 / s0) - 1.0
    R = 2.0 / (1.0 + coeff * math.exp(2.0 * d * days))
    return max(0.0, min(1.0, R))


def reinforce(
    memory: Memory,
    now: datetime | None = None,
    probability: float = 1.0,
) -> float:
    """Successful recall strengthens both activation and long-term stability.

    Immediate activation moves 35% toward 1.0. Stability follows the spacing
    effect: massed repetition while R is high produces a small gain, while a
    successful, effortful recall after an interval produces a larger gain.
    """
    R = current_strength(memory, now=now)

    probability = max(0.0, min(1.0, probability))
    new_strength = R + REINFORCEMENT_GAIN * probability * (1.0 - R)
    new_strength = max(0.0, min(1.0, new_strength))

    # For R(0)=1 in our nonlinear curve, half-life is ln(3)/(2d).
    old_S = max(0.1, memory.stability)

    retrieval_effort = (1.0 - R) ** 1.25
    spacing_multiplier = 0.15 + 1.85 * retrieval_effort
    difficulty_multiplier = 0.5 + memory.difficulty
    saturation = max(0.0, 1.0 - old_S / STABILITY_MAX_DAYS)
    stability_growth = (
        STABILITY_GAIN
        * probability
        * spacing_multiplier
        * difficulty_multiplier
        * saturation
    )
    new_S = old_S * (1.0 + stability_growth)
    memory.stability = min(STABILITY_MAX_DAYS, new_S)
    memory.decay_rate = stability_to_decay(memory.stability)
    memory.difficulty = max(
        0.0,
        memory.difficulty - 0.03 * probability * (1.0 - R),
    )

    return new_strength
