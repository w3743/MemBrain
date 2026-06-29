from __future__ import annotations

import sqlite3
from datetime import timedelta

from brainmemory.adapters import AgentScope, BrainMemoryAdapter
from brainmemory.engine import BrainMemoryEngine
from brainmemory.extractor import NullMemoryExtractor
from brainmemory.evolution import apply_feedback, detect_feedback
from brainmemory.models import Memory, utc_now
from brainmemory.retrieval import RetrievalMode
from brainmemory.strength import current_strength
from brainmemory.store import MemoryStore


class KeywordEmbedding:
    name = "easm-test"

    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if "database" in lowered or "数据库" in lowered:
            return [0.0, 1.0, 0.0]
        if "unrelated" in lowered:
            return [0.0, 0.0, 1.0]
        return [1.0, 0.0, 0.0]


class MmrEmbedding:
    name = "mmr-test"

    def embed(self, text: str) -> list[float]:
        if "diverse" in text:
            return [0.9, 0.4358899]
        return [1.0, 0.0]


def test_explicit_use_is_probabilistic_and_persisted(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "easm.db", embedding=KeywordEmbedding())
    try:
        memory = engine.add_memory("项目使用 bun install。", project_id="demo")
        before_stability = memory.stability

        updated = engine.evolution.process_turn(
            "如何安装？",
            "请查看项目文档。",
            [{"id": memory.id, "content": memory.content}],
            explicit_used_ids=[memory.id or 0],
        )

        assert updated[0].access_count == 1
        assert updated[0].stability > before_stability
        event = engine.store.feedback_events(memory.id)[0]
        assert event["action"] == "used"
        assert event["p_use"] == 0.98
        assert event["confidence"] > 0.8
    finally:
        engine.close()


def test_uncertain_feedback_does_not_penalize_memory() -> None:
    memory = Memory(id=1, content="项目使用 bun install。", boost=0.2)
    feedback = detect_feedback(
        "安装依赖？",
        "使用 bun。",
        [{"id": 1, "content": memory.content}],
    )[0]

    assert feedback["action"] == "uncertain"
    before = memory.boost
    apply_feedback(
        memory,
        feedback["action"],
        p_use=feedback["p_use"],
        p_ignore=feedback["p_ignore"],
        p_correct=feedback["p_correct"],
        confidence=feedback["confidence"],
    )
    assert memory.boost == before


def test_conflicting_new_memory_increases_old_memory_decay(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "interference.db", embedding=KeywordEmbedding())
    try:
        old = engine.add_memory("项目使用 pnpm install。", project_id="demo")
        old.created_at = utc_now() - timedelta(days=10)
        engine.store.conn.execute(
            "UPDATE memories SET created_at=? WHERE id=?",
            (old.created_at.isoformat(), old.id),
        )
        engine.store.conn.commit()
        engine.add_memory("项目现在改用 bun install。", project_id="demo")

        results = engine.search("项目 install", project_id="demo", limit=5)
        old_result = next(item for item in results if item.memory.id == old.id)
        baseline = current_strength(old_result.memory)

        assert old_result.interference > 0
        assert old_result.current_strength < baseline
    finally:
        engine.close()


def test_consolidation_uses_age_and_utility_guards(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "archive.db", embedding=KeywordEmbedding())
    try:
        now = utc_now()
        useful = engine.add_memory("useful", project_id="demo")
        weak = engine.add_memory("weak", project_id="demo")
        for memory, utility in ((useful, 1.0), (weak, 0.1)):
            memory.strength = 0.05
            memory.utility = utility
            created = now - timedelta(days=30)
            engine.store.update(memory)
            engine.store.conn.execute(
                "UPDATE memories SET created_at=? WHERE id=?",
                (created.isoformat(), memory.id),
            )
        engine.store.conn.commit()

        report = engine.sleep_consolidate()

        assert report["archived"] == 1
        assert engine.store.get(useful.id or 0).status.value == "active"
        assert engine.store.get(weak.id or 0).status.value == "archived"
    finally:
        engine.close()


def test_memory_state_round_trips_new_easm_fields(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "roundtrip.db", embedding=KeywordEmbedding())
    try:
        memory = engine.add_memory("state", project_id="demo")
        memory.stability = 42.0
        memory.difficulty = 0.7
        memory.utility = 0.8
        memory.trust_alpha = 7.0
        memory.trust_beta = 3.0
        memory.exposure_count = 4
        memory.correction_count = 2
        memory.sync_trust()
        engine.store.update(memory)

        loaded = engine.store.get(memory.id or 0)
        assert loaded is not None
        assert loaded.stability == 42.0
        assert loaded.difficulty == 0.7
        assert loaded.utility == 0.8
        assert loaded.trust_mean == 0.7
        assert loaded.exposure_count == 4
        assert loaded.correction_count == 2
    finally:
        engine.close()


def test_prompt_injection_records_exposure_without_reinforcement(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "exposure.db", embedding=KeywordEmbedding())
    try:
        memory = engine.add_memory("项目使用 bun install。", project_id="demo")
        adapter = BrainMemoryAdapter(engine, extractor=NullMemoryExtractor())

        context = adapter.retrieve("如何 install", AgentScope(project_id="demo"))
        loaded = engine.store.get(memory.id or 0)

        assert context.memory_ids == [memory.id]
        assert loaded is not None
        assert loaded.exposure_count == 1
        assert loaded.access_count == 0
        assert context.items[0]["stability"] == round(memory.stability, 4)
    finally:
        engine.close()


def test_mmr_prevents_near_duplicate_candidates_from_filling_context(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mmr.db", embedding=MmrEmbedding())
    try:
        first = engine.add_memory("duplicate one", project_id="demo")
        second = engine.add_memory("duplicate two", project_id="demo")
        diverse = engine.add_memory("diverse useful memory", project_id="demo")
        diverse.utility = 1.0
        engine.store.update(diverse)

        results = engine.search(
            "query",
            project_id="demo",
            limit=2,
            mode=RetrievalMode.WRITE_ARBITRATION,
        )
        ids = {result.memory.id for result in results}

        assert diverse.id in ids
        assert not ({first.id, second.id} <= ids)
    finally:
        engine.close()


def test_legacy_database_migrates_decay_and_trust_to_easm_state(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
            summary TEXT DEFAULT '', strength REAL NOT NULL DEFAULT 0.6,
            access_count INTEGER NOT NULL DEFAULT 0, last_accessed_at TEXT,
            status TEXT NOT NULL DEFAULT 'active', superseded_by INTEGER,
            tags TEXT DEFAULT '', scope TEXT NOT NULL DEFAULT 'user',
            project_id TEXT, sensitivity TEXT NOT NULL DEFAULT 'normal',
            decay_rate REAL NOT NULL DEFAULT 0.02, boost REAL NOT NULL DEFAULT 0,
            trust REAL NOT NULL DEFAULT 0.5, error_count INTEGER NOT NULL DEFAULT 0,
            verify_count INTEGER NOT NULL DEFAULT 0, embedding TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    now = utc_now().isoformat()
    conn.execute(
        """
        INSERT INTO memories(
            content, strength, status, decay_rate, trust, embedding,
            created_at, updated_at
        ) VALUES ('legacy', 0.6, 'active', 0.04, 0.8, '[1,0,0]', ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db, embedding=KeywordEmbedding())
    try:
        memory = store.get(1)
        assert memory is not None
        assert 13.7 < memory.stability < 13.8
        assert abs(memory.trust_mean - 0.8) < 0.001
        assert store.feedback_events() == []
    finally:
        store.close()
