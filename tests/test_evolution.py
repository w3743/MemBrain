"""自适应进化测试"""
from pathlib import Path

from csm_agent.engine import CSMEngine
from csm_agent.evolution import (
    EvolutionEngine, apply_feedback, detect_feedback, inherit_from,
    DECAY_MIN, DECAY_MAX, BOOST_MIN, BOOST_MAX, TRUST_MAX,
)
from csm_agent.models import Memory, MemoryStatus, MemoryOp


def test_apply_feedback_used() -> None:
    m = Memory(id=1, content="test", decay_rate=0.02, boost=0.0, trust=0.5)
    m = apply_feedback(m, "used")
    assert m.boost > 0.0           # boost increased
    assert m.trust > 0.5           # trust increased
    assert m.verify_count == 1     # verify counted
    assert m.decay_rate == 0.02    # decay_rate 由 reinforce() 统一管理，apply_feedback 不再修改


def test_apply_feedback_ignored() -> None:
    m = Memory(id=1, content="test", decay_rate=0.02, boost=0.1, trust=0.5)
    old_boost = m.boost
    m = apply_feedback(m, "ignored")
    assert m.boost < old_boost     # boost decreased
    assert m.decay_rate > 0.02     # decay increased


def test_apply_feedback_corrected() -> None:
    m = Memory(id=1, content="test", decay_rate=0.02, boost=0.1, trust=0.8)
    m = apply_feedback(m, "corrected")
    assert m.boost < 0.0           # boost went negative
    assert m.trust < 0.8           # trust decreased
    assert m.error_count == 1      # error counted
    assert m.decay_rate > 0.025    # decay significantly increased


def test_bounds() -> None:
    # 参数不会超出边界
    m = Memory(id=1, content="t", decay_rate=DECAY_MIN, boost=BOOST_MAX, trust=TRUST_MAX)
    m = apply_feedback(m, "used")
    assert m.boost <= BOOST_MAX
    assert m.trust <= TRUST_MAX
    assert m.decay_rate >= DECAY_MIN

    m = Memory(id=1, content="t", decay_rate=DECAY_MAX, boost=BOOST_MIN, trust=0.05)
    m = apply_feedback(m, "corrected")
    assert m.boost >= BOOST_MIN
    assert m.decay_rate <= DECAY_MAX


def test_detect_feedback_used() -> None:
    fb = detect_feedback(
        "How do I install dependencies?",
        "You should use bun install. That's the package manager for this project.",
        [{"id": 1, "content": "Project uses bun install for dependencies."}],
    )
    assert len(fb) == 1
    assert fb[0]["action"] == "used"


def test_detect_feedback_correction() -> None:
    fb = detect_feedback(
        "不对，应该用 pnpm 而不是 bun",
        "好的，已纠正为 pnpm。",
        [{"id": 1, "content": "Project uses bun install for dependencies."}],
    )
    assert len(fb) == 1
    assert fb[0]["action"] == "corrected"


def test_detect_feedback_ignored() -> None:
    fb = detect_feedback(
        "今天天气怎么样？",
        "今天晴天，适合出门。",
        [{"id": 1, "content": "Project uses bun install for dependencies."}],
    )
    assert len(fb) == 1
    assert fb[0]["action"] == "ignored"


def test_inherit_from() -> None:
    parent = Memory(id=1, content="old", trust=0.9, verify_count=10, error_count=2)
    child = Memory(id=2, content="new", trust=0.5, verify_count=0, error_count=0)
    child = inherit_from(child, parent)
    assert child.trust == (0.9 + 0.5) / 2   # 新旧平均
    assert child.verify_count == 9           # parent.verify - 1
    assert child.error_count == 2            # 保留错误历史


def test_evolution_engine_flow(tmp_path) -> None:
    engine = CSMEngine(tmp_path / "evolve.db")
    try:
        # 存入一条记忆
        m = engine.add_memory("项目用 bun install", project_id="test", tags="依赖")
        assert m.decay_rate == 0.02
        assert m.boost == 0.0

        # 模拟：检索到 → LLM 用了 → 强化
        engine.evolution.process_turn(
            "怎么装依赖？", "用 bun install 装依赖。",
            [{"id": m.id, "content": "项目用 bun install"}],
        )
        m2 = engine.store.get(m.id)
        assert m2 is not None
        assert m2.verify_count == 1
        assert m2.boost > 0.0
        assert m2.trust > 0.5

        # 模拟：用户纠正
        engine.evolution.process_turn(
            "不对，现在已经改成 pnpm 了", "好的，纠正为 pnpm。",
            [{"id": m.id, "content": "项目用 bun install"}],
        )
        m3 = engine.store.get(m.id)
        assert m3 is not None
        assert m3.error_count == 1
        assert m3.boost < m2.boost
        assert m3.decay_rate > 0.02

        # 手动纠正
        engine.evolution.record_manual_correction(m.id)
        m4 = engine.store.get(m.id)
        assert m4 is not None
        assert m4.error_count == 2

        # 手动验证
        engine.evolution.record_manual_verify(m.id)
        m5 = engine.store.get(m.id)
        assert m5 is not None
        assert m5.verify_count >= 1
    finally:
        engine.close()


def test_supersede_inherits_trust(tmp_path) -> None:
    engine = CSMEngine(tmp_path / "inherit.db")
    try:
        old = engine.add_memory("使用 pnpm", project_id="test", tags="依赖")
        old.trust = 0.9
        old.verify_count = 5
        engine.store.update(old)

        new = engine.apply_operation(
            MemoryOp.SUPERSEDE, target_id=old.id,
            content="使用 bun install", project_id="test",
        )
        assert new is not None
        assert new.trust > 0.5     # 继承了信任
        assert new.verify_count == 4  # parent.verify - 1
    finally:
        engine.close()
