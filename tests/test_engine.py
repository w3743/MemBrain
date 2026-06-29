import os
from datetime import timedelta
from pathlib import Path

import pytest

from brainmemory.engine import BrainMemoryEngine
from brainmemory.embedding import build_embedding_backend_from_env, embedding_config_from_env
from brainmemory.models import MemoryOp, MemoryStatus, utc_now


class ConstantEmbeddingBackend:
    name = "constant-test"

    def __init__(self, value: float) -> None:
        self.value = value

    def embed(self, text: str) -> list[float]:
        return [self.value, len(text) / 100.0]


def test_add_search_and_reinforce(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory("用户偏好简洁中文回答。", project_id="demo", tags="偏好,简洁")
        results = engine.search("回答风格要简洁吗", project_id="demo")
        assert results
        assert results[0].memory.id == memory.id

        before = results[0].current_strength
        reinforced = engine.reinforce_used(memory.id or 0)
        assert reinforced.access_count >= 1
        assert reinforced.strength >= before
    finally:
        engine.close()


def test_duplicate_add_reinforces_existing_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        first = engine.add_memory("用户偏好简洁中文回答。", project_id="demo", tags="偏好,简洁")
        second = engine.apply_operation(
            MemoryOp.ADD,
            content="用户偏好简洁中文回答。",
            project_id="demo",
            tags="偏好,简洁",
        )

        memories = engine.store.list_all()
        assert second is not None
        assert second.id == first.id
        assert len(memories) == 1
        assert memories[0].access_count == 1
        assert memories[0].strength > first.strength
    finally:
        engine.close()


def test_duplicate_add_stores_sensitive_content_verbatim(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        first = engine.add_memory(
            "api_key = sk_test_1234567890",
            project_id="demo",
            tags="secret",
            sensitivity="secret",
        )
        second = engine.apply_operation(
            MemoryOp.ADD,
            content="api_key = sk_test_1234567890",
            project_id="demo",
            tags="credential",
            sensitivity="normal",
        )

        assert second is not None
        assert second.id == first.id
        assert second.sensitivity == "normal"
        assert "sk_test_1234567890" in second.content
        assert set(second.tags.split(",")) == {"secret", "credential"}
    finally:
        engine.close()


def test_sensitive_memory_can_be_injected_into_answers(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory(
            "api_key = sk_test_1234567890",
            project_id="demo",
            tags="credential",
        )

        assert memory.sensitivity == "normal"
        assert engine.search("api key 是什么", project_id="demo")
    finally:
        engine.close()


def test_sensitive_summary_is_stored_verbatim(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory(
            "credential is configured",
            summary="api_key = sk_summary_secret",
            project_id="demo",
        )

        assert "sk_summary_secret" in memory.summary
        assert memory.sensitivity == "normal"
    finally:
        engine.close()


def test_nearby_but_conflicting_add_does_not_merge(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        first = engine.add_memory("用户偏好简洁中文回答。", project_id="demo", tags="偏好,简洁")
        second = engine.apply_operation(
            MemoryOp.ADD,
            content="用户偏好详细中文回答。",
            project_id="demo",
            tags="偏好,详细",
        )

        assert second is not None
        assert second.id != first.id
        assert len(engine.store.list_all()) == 2
    finally:
        engine.close()


def test_supersede_hides_old_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        old = engine.add_memory("项目使用 pnpm install 安装依赖。", project_id="demo", tags="依赖,pnpm")
        new = engine.apply_operation(MemoryOp.SUPERSEDE, target_id=old.id,
                                      content="项目已改用 bun install 安装依赖。", project_id="demo")
        assert new is not None

        results = engine.search("安装依赖用什么命令", project_id="demo")
        assert results
        assert results[0].memory.id == new.id
        assert "bun" in results[0].memory.content
        assert engine.store.get(old.id or 0) is None
    finally:
        engine.close()


def test_sleep_archives_low_value_cold_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        # 创建足够多的记忆来形成合理的动态阈值分布
        for i in range(10):
            mem = engine.add_memory(f"Test memory {i}.", project_id="demo", tags="test")
            mem.strength = 0.5 + i * 0.03
            engine.store.update(mem)
        # 创建一个极低强度的记忆
        memory = engine.add_memory("一次性临时邮箱 test@example.com。", project_id="demo", tags="临时")
        memory.strength = 0.0001
        memory.utility = 0.2
        memory.created_at = utc_now() - timedelta(days=10)
        engine.store.update(memory)
        engine.store.conn.execute(
            "UPDATE memories SET created_at=? WHERE id=?",
            (memory.created_at.isoformat(), memory.id),
        )
        engine.store.conn.commit()

        report = engine.sleep_consolidate()
        assert report["archived"] >= 1
        assert engine.store.get(memory.id or 0).status.value == "archived"
    finally:
        engine.close()


def test_sleep_deletes_legacy_superseded_records(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        old = engine.add_memory("旧项目约定。", project_id="demo")
        old.status = MemoryStatus.SUPERSEDED
        engine.store.update(old)

        report = engine.sleep_consolidate()

        assert report["deleted_superseded"] == 1
        assert engine.store.get(old.id or 0) is None
    finally:
        engine.close()


def test_store_index_version_changes_on_memory_updates(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        start = engine.store.index_version()
        memory = engine.add_memory("User prefers concise answers.", project_id="demo", tags="preference")
        after_add = engine.store.index_version()
        engine.reinforce_used(memory.id or 0)
        after_update = engine.store.index_version()

        assert after_add > start
        assert after_update == after_add

        memory.content = "User now prefers detailed answers."
        engine.store.update(memory)
        assert engine.store.index_version() > after_update
    finally:
        engine.close()


def test_identity_question_recalls_name_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db", embedding=ConstantEmbeddingBackend(0.0))
    try:
        memory = engine.add_memory("我叫王家裕。", project_id="demo", tags="名字,身份,称呼")
        results = engine.search("以后应该怎么称呼我？", project_id="demo")
        assert results
        assert results[0].memory.id == memory.id
    finally:
        engine.close()


def test_irrelevant_query_returns_no_answer_injection_results(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        engine.add_memory("我叫王家裕。", project_id="demo", tags="名字,身份,称呼")

        results = engine.search("今天天气怎么样？", project_id="demo")

        assert results == []
    finally:
        engine.close()


def test_search_does_not_reinforce_memory_until_used(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory("项目依赖管理使用 bun install。", project_id="demo", tags="依赖,bun")

        results = engine.search("安装依赖用什么命令？", project_id="demo")
        after_search = engine.store.get(memory.id or 0)

        assert results
        assert after_search is not None
        assert after_search.access_count == 0
    finally:
        engine.close()


def test_reinforce_used_records_experience_activation(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory("User name is Wang Jiayu.", project_id="demo", tags="name")
        reinforced = engine.reinforce_used(memory.id or 0)
        assert reinforced.access_count >= 1
        assert reinforced.last_accessed_at is not None
    finally:
        engine.close()


def test_reindex_embeddings_rebuilds_vectors_and_bumps_version(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db", embedding=ConstantEmbeddingBackend(1.0))
    try:
        memory = engine.add_memory("User prefers concise answers.", project_id="demo", tags="preference")
        before_version = engine.store.index_version()
        before = engine.store.embedding_for_row(
            engine.store.conn.execute("SELECT * FROM memories WHERE id=?", (memory.id,)).fetchone()
        )

        engine.store.embedding = ConstantEmbeddingBackend(2.0)
        report = engine.reindex_embeddings()
        after = engine.store.embedding_for_row(
            engine.store.conn.execute("SELECT * FROM memories WHERE id=?", (memory.id,)).fetchone()
        )

        assert report["reindexed"] == 1
        assert report["memory_index_version"] > before_version
        assert before[0] == 1.0
        assert after[0] == 2.0
    finally:
        engine.close()


def test_embedding_config_defaults_to_local_bge() -> None:
    old_backend = os.environ.pop("BRAINMEMORY_EMBEDDING_BACKEND", None)
    old_model = os.environ.pop("BRAINMEMORY_EMBEDDING_MODEL", None)
    try:
        config = embedding_config_from_env()
        assert config["backend"] == "local"
        assert "bge-large-zh-v1.5" in str(config["model"])
        assert Path(str(config["model"])).exists()
        assert "hash" not in config["available"]
    finally:
        if old_backend is not None:
            os.environ["BRAINMEMORY_EMBEDDING_BACKEND"] = old_backend
        if old_model is not None:
            os.environ["BRAINMEMORY_EMBEDDING_MODEL"] = old_model


def test_embedding_backend_rejects_remote_model_id() -> None:
    old_backend = os.environ.get("BRAINMEMORY_EMBEDDING_BACKEND")
    old_model = os.environ.get("BRAINMEMORY_EMBEDDING_MODEL")
    os.environ["BRAINMEMORY_EMBEDDING_BACKEND"] = "local"
    os.environ["BRAINMEMORY_EMBEDDING_MODEL"] = "BAAI/bge-large-zh-v1.5"
    try:
        with pytest.raises(FileNotFoundError):
            build_embedding_backend_from_env()
    finally:
        if old_backend is None:
            os.environ.pop("BRAINMEMORY_EMBEDDING_BACKEND", None)
        else:
            os.environ["BRAINMEMORY_EMBEDDING_BACKEND"] = old_backend
        if old_model is None:
            os.environ.pop("BRAINMEMORY_EMBEDDING_MODEL", None)
        else:
            os.environ["BRAINMEMORY_EMBEDDING_MODEL"] = old_model
