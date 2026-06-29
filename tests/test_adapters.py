from brainmemory.adapters import AgentEvent, AgentScope, BrainMemoryAdapter, HermesMemoryProvider, OpenClawMemorySidecar, PiAgentMemoryHook
from brainmemory.engine import BrainMemoryEngine
from brainmemory.extractor import JSONMemoryExtractor
from brainmemory.models import MemoryOp


def fake_add_extractor():
    return JSONMemoryExtractor(
        lambda payload: {
            "rationale": "fake LLM extracted a durable memory",
            "writes": [{"op": "ADD", "content": payload["user_input"], "summary": payload["user_input"], "tags": ""}],
        }
    )


def test_piagent_hook_injects_and_commits_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        engine.add_memory("项目依赖管理使用 bun install。", project_id=AgentScope(user_id="u1", project_id="demo").storage_project_id, tags="依赖,bun")
        hook = PiAgentMemoryHook(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        state = hook.before_agent_start("安装依赖用什么命令？", {"user_id": "u1", "project_id": "demo"})

        assert "bun install" in state["brainmemory_memory_context"]
        assert state["brainmemory_memory_ids"]
        assert state["csm_memory_context"] == state["brainmemory_memory_context"]
        assert state["csm_memory_ids"] == state["brainmemory_memory_ids"]

        state["brainmemory_explicit_memories"] = ["用户希望回答先给结论，再给必要步骤。"]
        final_state = hook.agent_end("安装依赖用什么命令？", "使用 bun install。", state)
        assert any(item["action"] == "used" for item in final_state["brainmemory_feedback"])
        assert "ADD" in final_state["brainmemory_write_plan"]
        assert final_state["brainmemory_committed_ids"]
        assert final_state["csm_write_plan"] == final_state["brainmemory_write_plan"]
        assert final_state["csm_committed_ids"] == final_state["brainmemory_committed_ids"]
    finally:
        engine.close()


def test_piagent_hook_accepts_workspace_id_scope(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        hook = PiAgentMemoryHook(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        state = hook.agent_end(
            "这个工作区默认使用 pytest 跑测试。",
            "好的。",
            {"user_id": "u1", "workspace_id": "workspace-demo"},
        )
        assert "ADD" in state["brainmemory_write_plan"]
        results = engine.search("这个工作区默认怎么跑测试？", project_id="workspace-demo")
        assert results
        assert "pytest" in results[0].memory.content
    finally:
        engine.close()


def test_piagent_hook_accepts_legacy_csm_memory_ids(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory("项目依赖管理使用 bun install。", project_id="demo", tags="依赖,bun")
        hook = PiAgentMemoryHook(BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(lambda payload: {"rationale": "noop", "writes": [{"op": "NOOP"}]})))
        state = hook.agent_end(
            "安装依赖用什么命令？",
            "使用 bun install。",
            {"user_id": "u1", "project_id": "demo", "csm_memory_ids": [memory.id]},
        )
        updated = engine.store.get(memory.id or 0)
        assert any(item["action"] == "used" for item in state["brainmemory_feedback"])
        assert updated is not None
        assert updated.access_count >= 1
    finally:
        engine.close()


def test_openclaw_sidecar_payload_flow(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        post = sidecar.handle_post_run({
            "user_id": "u1", "workspace_id": "openclaw-demo",
            "message": "记住这个工作区使用 sqlite-vec。",
        })
        assert post["committed_ids"]

        pre = sidecar.handle_pre_prompt({
            "user_id": "u1", "workspace_id": "openclaw-demo",
            "message": "这个工作区向量后端用什么？",
        })
        assert "sqlite-vec" in pre["memory_context"]
        assert pre["memory_ids"]
        assert "semantic_similarity" in pre["items"][0]
    finally:
        engine.close()


def test_openclaw_post_run_reinforces_used_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory(
            "\u6211\u53eb\u738b\u5bb6\u88d5\u3002",
            project_id="user:u1",
            tags="\u540d\u5b57,\u79f0\u547c",
        )
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        post = sidecar.handle_post_run({
            "user_id": "u1",
            "message": "\u4ee5\u540e\u5e94\u8be5\u600e\u4e48\u79f0\u547c\u6211\uff1f",
            "agent_output": "\u6211\u4f1a\u79f0\u547c\u4f60\u4e3a\u738b\u5bb6\u88d5\u3002",
            "memory_ids": [memory.id],
        })
        updated = engine.store.get(memory.id or 0)
        assert any(item["action"] == "used" for item in post["feedback"])
        assert updated is not None
        assert updated.access_count >= 1
    finally:
        engine.close()


def test_injected_memory_is_only_reinforced_when_answer_uses_it(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        injected = engine.add_memory(
            "项目依赖管理使用 bun install。",
            project_id="demo",
            tags="依赖,bun",
        )
        unrelated = engine.add_memory(
            "项目数据库使用 PostgreSQL。",
            project_id="demo",
            tags="数据库",
        )
        adapter = BrainMemoryAdapter(
            engine,
            extractor=JSONMemoryExtractor(
                lambda payload: {"rationale": "noop", "writes": [{"op": "NOOP"}]}
            ),
        )

        ignored_plan = adapter.observe(AgentEvent(
            user_input="安装依赖用什么命令？",
            agent_output="请查看项目文档。",
            used_memory_ids=[injected.id],
            scope=AgentScope(project_id="demo"),
        ))
        adapter.commit(ignored_plan, AgentScope(project_id="demo"))
        ignored = engine.store.get(injected.id or 0)
        untouched = engine.store.get(unrelated.id or 0)

        assert [write.op for write in ignored_plan.writes] == [MemoryOp.NOOP]
        assert ignored is not None and ignored.access_count == 0 and ignored.boost < 0
        assert untouched is not None and untouched.boost == 0

        used_plan = adapter.observe(AgentEvent(
            user_input="安装依赖用什么命令？",
            agent_output="使用 bun install。",
            used_memory_ids=[injected.id],
            scope=AgentScope(project_id="demo"),
        ))
        adapter.commit(used_plan, AgentScope(project_id="demo"))
        used = engine.store.get(injected.id or 0)

        assert any(item["action"] == "used" for item in used_plan.feedback or [])
        assert used is not None and used.access_count == 1
    finally:
        engine.close()


def test_openclaw_irrelevant_message_does_not_inject_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        engine.add_memory("我叫王家裕。", tags="名字,身份,称呼")
        engine.add_memory("项目依赖管理使用 bun install。", project_id="demo", tags="依赖,bun")
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))

        pre = sidecar.handle_pre_prompt({
            "user_id": "u1",
            "workspace_id": "demo",
            "message": "今天天气怎么样？",
        })

        assert pre["memory_context"] == ""
        assert pre["memory_ids"] == []
        assert pre["items"] == []
    finally:
        engine.close()


def test_openclaw_user_id_does_not_partition_workspace_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "我叫王家裕。",
        })

        own = sidecar.handle_pre_prompt({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "以后应该怎么称呼我？",
        })
        other = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "以后应该怎么称呼我？",
        })

        assert "王家裕" in own["memory_context"]
        assert "王家裕" in other["memory_context"]
        assert other["memory_ids"]
    finally:
        engine.close()


def test_openclaw_same_workspace_different_users_share_project_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "这个项目依赖管理使用 bun install。",
        })

        other = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "安装依赖用什么命令？",
        })

        assert "bun install" in other["memory_context"]
        assert other["memory_ids"]
    finally:
        engine.close()


def test_no_workspace_memory_is_global_in_single_user_mode(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "message": "我叫王家裕。",
        })
        stored = engine.store.list_all()[0]
        assert stored.project_id is None

        own = sidecar.handle_pre_prompt({
            "user_id": "u1",
            "message": "以后应该怎么称呼我？",
        })
        other = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "message": "以后应该怎么称呼我？",
        })

        assert "王家裕" in own["memory_context"]
        assert "王家裕" in other["memory_context"]
        assert other["memory_ids"]
    finally:
        engine.close()


def test_no_workspace_project_like_memory_is_global(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "message": "这个项目依赖管理使用 bun install。",
        })
        stored = engine.store.list_all()[0]
        assert stored.project_id is None

        own = sidecar.handle_pre_prompt({
            "user_id": "u1",
            "message": "安装依赖用什么命令？",
        })
        other = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "message": "安装依赖用什么命令？",
        })

        assert "bun install" in own["memory_context"]
        assert "bun install" in other["memory_context"]
        assert other["memory_ids"]
    finally:
        engine.close()


def test_no_workspace_environment_fact_is_global(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "message": "本机默认 Python 命令是 py -3.11。",
        })
        stored = engine.store.list_all()[0]
        assert stored.project_id is None

        other = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "another-workspace",
            "message": "本机默认 Python 命令是什么？",
        })

        assert "py -3.11" in other["memory_context"]
        assert other["memory_ids"]
    finally:
        engine.close()


def test_user_id_does_not_restrict_memory_operations(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory(
            "我叫王家裕。",
            project_id="user:u1",
            tags="名字,身份,称呼",
        )
        adapter = BrainMemoryAdapter(
            engine,
            extractor=JSONMemoryExtractor(
                lambda payload: {
                    "rationale": "forged target",
                    "writes": [{
                        "op": "DELETE",
                        "target_id": memory.id,
                    }],
                }
            ),
        )

        plan = adapter.observe(AgentEvent(user_input="忘记名字", scope=AgentScope(user_id="u2")))
        committed = adapter.commit(plan, AgentScope(user_id="u2"))

        assert [item.id for item in committed] == [memory.id]
        assert engine.store.get(memory.id or 0) is None
    finally:
        engine.close()


def test_project_contact_information_uses_project_scope(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        adapter = BrainMemoryAdapter(engine, extractor=fake_add_extractor())
        scope = AgentScope(user_id="u1", project_id="demo")
        plan = adapter.observe(AgentEvent(
            user_input="项目联系人邮箱是 owner@example.com。",
            scope=scope,
        ))
        committed = adapter.commit(plan, scope)

        assert committed[0].project_id == "demo"
    finally:
        engine.close()


def test_all_workspace_preferences_are_shared_in_single_user_mode(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "我偏好回答简洁一点。",
        })
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "这个项目偏好回答代码问题时先给结论。",
        })

        other_personal = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "我的回答偏好是什么？",
        })
        other_project = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "这个项目回答代码问题有什么偏好？",
        })

        assert "简洁" in other_personal["memory_context"]
        assert "先给结论" in other_project["memory_context"]
    finally:
        engine.close()


def test_english_workspace_preferences_ignore_user_id(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "I prefer concise answers.",
        })
        sidecar.handle_post_run({
            "user_id": "u1",
            "workspace_id": "shared-workspace",
            "message": "This project prefers answers with a conclusion first.",
        })

        other_personal = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "What is my answer preference?",
        })
        other_project = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "What answer style does this project prefer?",
        })

        assert "concise answers" in other_personal["memory_context"]
        assert "conclusion first" in other_project["memory_context"]
    finally:
        engine.close()


def test_project_retrieval_includes_global_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        engine.add_memory("我叫王家裕。", tags="名字,身份,称呼")
        engine.add_memory("全局项目约定：回答代码问题先给结论。", tags="项目,回答风格")
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))

        identity = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "我叫什么名字？",
        })
        project_style = sidecar.handle_pre_prompt({
            "user_id": "u2",
            "workspace_id": "shared-workspace",
            "message": "回答代码问题有什么全局约定？",
        })

        assert "王家裕" in identity["memory_context"]
        assert any("王家裕" in item["content"] for item in identity["items"])
        assert "先给结论" in project_style["memory_context"]
    finally:
        engine.close()


def test_negative_memory_injection_uses_positive_actionable_text(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        memory = engine.add_memory(
            "不要用 MySQL，这个项目只用 PostgreSQL。",
            project_id=AgentScope(user_id="u1", project_id="demo").storage_project_id,
            tags="数据库,PostgreSQL",
        )
        sidecar = OpenClawMemorySidecar(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))

        pre = sidecar.handle_pre_prompt({
            "user_id": "u1",
            "workspace_id": "demo",
            "message": "数据库用的是什么？",
        })

        assert "PostgreSQL" in pre["memory_context"]
        assert "MySQL" not in pre["memory_context"]
        assert engine.store.get(memory.id or 0).content == "不要用 MySQL，这个项目只用 PostgreSQL。"
    finally:
        engine.close()


def test_hermes_provider_facade(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        provider = HermesMemoryProvider(BrainMemoryAdapter(engine))
        memory_id = provider.remember("Hermes 项目回答风格：简洁，避免无关解释。", user_id="u1", project_id="hermes")
        assert memory_id is not None

        context = provider.get_context("回答风格是什么？", user_id="u1", project_id="hermes")
        assert "简洁" in context

        health = provider.sleep()
        assert health["total"] >= 1
    finally:
        engine.close()


def test_adapter_observe_extracts_memory_without_explicit_list(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        hook = PiAgentMemoryHook(BrainMemoryAdapter(engine, extractor=fake_add_extractor()))
        state = hook.agent_end("以后默认用 pytest 跑这个项目的测试。", "好的，之后会使用 pytest。",
                               {"user_id": "u1", "project_id": "demo"})
        assert "ADD" in state["csm_write_plan"]
        assert state["csm_committed_ids"]
        results = engine.search("这个项目默认怎么跑测试？", project_id="demo")
        assert results
        assert "pytest" in results[0].memory.content
    finally:
        engine.close()


def test_adapter_duplicate_add_reinforces_instead_of_growing_store(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        adapter = BrainMemoryAdapter(engine, extractor=fake_add_extractor())
        scope = AgentScope(project_id="demo")
        event = AgentEvent(user_input="用户偏好简洁中文回答。", scope=scope)

        first = adapter.commit(adapter.observe(event), scope)
        second = adapter.commit(adapter.observe(event), scope)
        memories = engine.store.list_all()

        assert first[0].id == second[0].id
        assert len(memories) == 1
        assert memories[0].access_count == 1
    finally:
        engine.close()


def test_adapter_passes_retrieved_memories_to_extractor_for_arbitration(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    seen_payloads = []

    def generator(payload):
        seen_payloads.append(payload)
        target_id = payload["retrieved_memories"][0]["id"]
        return {
            "rationale": "update existing naming preference",
            "writes": [{"op": "UPDATE", "target_id": target_id, "content": "User prefers Mr. Jiang.", "tags": "name"}],
        }

    try:
        old = engine.add_memory("User prefers to be called Jiayu.", project_id=AgentScope(project_id="demo").storage_project_id, tags="name,preference")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(generator))
        plan = adapter.observe(AgentEvent(
            user_input="Do not call me Jiayu anymore; call me Mr. Jiang.",
            scope=AgentScope(project_id="demo"),
        ))
        assert seen_payloads
        assert seen_payloads[0]["retrieved_memories"][0]["id"] == old.id
        assert plan.writes[0].target_id == old.id
    finally:
        engine.close()


def test_correction_only_supersedes_relevant_used_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        name = engine.add_memory("我的名字叫王家裕。", project_id="demo", tags="名字,身份,称呼")
        dependency = engine.add_memory("项目依赖管理使用 bun install。", project_id="demo", tags="依赖,bun")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(lambda payload: {"rationale": "noop", "writes": [{"op": "NOOP"}]}))

        plan = adapter.observe(AgentEvent(
            user_input="纠正一下，我叫江家裕，之前名字记错了。",
            used_memory_ids=[name.id, dependency.id],
            scope=AgentScope(project_id="demo"),
        ))
        committed = adapter.commit(plan, AgentScope(project_id="demo"))

        updated_name = engine.store.get(name.id or 0)
        updated_dependency = engine.store.get(dependency.id or 0)
        assert [memory.content for memory in committed] == ["纠正一下，我叫江家裕，之前名字记错了。"]
        assert updated_name is None
        assert updated_dependency is not None
        assert updated_dependency.status.value == "active"
    finally:
        engine.close()


def test_project_correction_still_supersedes_matching_memory(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    try:
        dependency = engine.add_memory("项目依赖管理使用 pnpm install。", project_id="demo", tags="依赖,pnpm")
        style = engine.add_memory("回答技术问题时先给结论。", project_id="demo", tags="偏好,回答风格")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(lambda payload: {"rationale": "noop", "writes": [{"op": "NOOP"}]}))

        plan = adapter.observe(AgentEvent(
            user_input="纠正一下，项目依赖管理已改用 bun install。",
            used_memory_ids=[dependency.id, style.id],
            scope=AgentScope(project_id="demo"),
        ))
        adapter.commit(plan, AgentScope(project_id="demo"))

        updated_dependency = engine.store.get(dependency.id or 0)
        updated_style = engine.store.get(style.id or 0)
        assert updated_dependency is None
        assert updated_style is not None
        assert updated_style.status.value == "active"
    finally:
        engine.close()


def test_delete_request_does_not_update_memory_before_delete(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")
    seen_payloads = []

    def generator(payload):
        seen_payloads.append(payload)
        return {
            "rationale": "user explicitly asked to forget the memory",
            "writes": [{"op": "DELETE", "target_id": payload["retrieved_memories"][0]["id"]}],
        }

    try:
        memory = engine.add_memory("我的名字叫王家裕。", project_id=AgentScope(project_id="demo").storage_project_id, tags="名字,身份,称呼")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(generator))

        plan = adapter.observe(AgentEvent(
            user_input="忘记我的名字，不要再记住这个称呼。",
            used_memory_ids=[memory.id],
            scope=AgentScope(project_id="demo"),
        ))
        assert [write.op for write in plan.writes] == [MemoryOp.DELETE]

        committed = adapter.commit(plan, AgentScope(project_id="demo"))
        assert [item.id for item in committed] == [memory.id]
        assert engine.store.get(memory.id or 0) is None
        assert engine.search("以后应该怎么称呼我？", project_id="demo") == []
    finally:
        engine.close()


def test_conflicting_llm_writes_keep_delete_over_update(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")

    def generator(payload):
        target_id = payload["retrieved_memories"][0]["id"]
        return {
            "rationale": "conflicting model output",
            "writes": [
                {"op": "UPDATE", "target_id": target_id, "content": "用户名字叫王家裕。"},
                {"op": "DELETE", "target_id": target_id},
            ],
        }

    try:
        memory = engine.add_memory("用户名字叫王家裕。", project_id=AgentScope(project_id="demo").storage_project_id, tags="名字,身份")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(generator))

        plan = adapter.observe(AgentEvent(
            user_input="请忘记我的名字。",
            scope=AgentScope(project_id="demo"),
        ))

        assert [write.op for write in plan.writes] == [MemoryOp.DELETE]
        adapter.commit(plan, AgentScope(project_id="demo"))
        assert engine.store.get(memory.id or 0) is None
    finally:
        engine.close()


def test_conflicting_llm_writes_keep_supersede_over_update(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "mem.db")

    def generator(payload):
        target_id = payload["retrieved_memories"][0]["id"]
        return {
            "rationale": "conflicting model output",
            "writes": [
                {"op": "UPDATE", "target_id": target_id, "content": "项目依赖管理使用 pnpm install。"},
                {"op": "SUPERSEDE", "target_id": target_id, "content": "项目依赖管理使用 bun install。"},
            ],
        }

    try:
        memory = engine.add_memory("项目依赖管理使用 pnpm install。", project_id=AgentScope(project_id="demo").storage_project_id, tags="依赖,pnpm")
        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(generator))

        plan = adapter.observe(AgentEvent(
            user_input="项目依赖管理已改用 bun install。",
            scope=AgentScope(project_id="demo"),
        ))

        assert [write.op for write in plan.writes] == [MemoryOp.SUPERSEDE]
        committed = adapter.commit(plan, AgentScope(project_id="demo"))
        assert committed[0].content == "项目依赖管理使用 bun install。"
        assert engine.store.get(memory.id or 0) is None
    finally:
        engine.close()
