from datetime import timedelta

from csm_agent.models import Memory, MemoryStatus, utc_now
from csm_agent.strength import current_strength, reinforce, resolve_layer, compute_layer_thresholds, update_dynamic_thresholds


def test_strength_decays_and_reinforces() -> None:
    memory = Memory(
        id=1, content="project uses bun", summary="project uses bun",
        strength=0.8,
        last_accessed_at=utc_now() - timedelta(days=30),
    )
    decayed = current_strength(memory)
    assert 0.3 < decayed < 0.8  # 30 days * 0.02 decay → ~0.44
    assert reinforce(memory) > decayed


def test_layer_thresholds() -> None:
    # 动态百分位阈值
    strengths = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08, 0.05, 0.03, 0.02, 0.01, 0.005, 0.001]
    thresholds = compute_layer_thresholds(strengths)
    assert thresholds["L1"] >= thresholds["L2"] >= thresholds["L3"]

    update_dynamic_thresholds(strengths)
    assert resolve_layer(0.95) == "L1"
    # 0.5 >= L2 阈值（前 60%）
    assert resolve_layer(0.5) == "L2"
    # 0.03 < L2 阈值但 >= L3 阈值（前 90%）
    assert resolve_layer(0.03) == "L3"
    # 0.0005 < L3 阈值
    assert resolve_layer(0.0005) == "COLD"


def test_unified_decay() -> None:
    # 所有记忆使用统一衰减率
    from csm_agent.strength import DECAY_RATE, INITIAL_STRENGTH, REINFORCEMENT_GAIN
    assert 0 < DECAY_RATE < 0.1
    assert 0.5 < INITIAL_STRENGTH <= 1.0  # 提高到 0.6，新记忆不会 COLD
    assert 0 < REINFORCEMENT_GAIN < 1.0
