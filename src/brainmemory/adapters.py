"""
集成适配层 — PiAgent / OpenClaw / Hermes
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .engine import BrainMemoryEngine
from .extractor import MemoryExtractor, build_default_extractor
from .embedding import tokenize
from .models import Memory, MemoryOp, MemoryWrite, MemoryWritePlan
from .retrieval import RetrievalMode, SearchResult
from .strength import current_strength


@dataclass(slots=True)
class AgentScope:
    user_id: str = "default"
    project_id: str | None = None
    channel: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def storage_project_id(self) -> str | None:
        return self.project_id

@dataclass(slots=True)
class MemoryContext:
    text: str
    memory_ids: list[int]
    items: list[dict[str, Any]]


@dataclass(slots=True)
class AgentEvent:
    user_input: str
    agent_output: str = ""
    tool_results: list[str] = field(default_factory=list)
    explicit_memories: list[str] = field(default_factory=list)
    used_memory_ids: list[int] = field(default_factory=list)
    explicit_used_memory_ids: list[int] = field(default_factory=list)
    scope: AgentScope = field(default_factory=AgentScope)


class BrainMemoryAdapter:
    """框架无关的 sidecar API。"""

    def __init__(
        self,
        engine: BrainMemoryEngine,
        default_budget_chars: int = 1400,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        self.engine = engine
        self.default_budget_chars = default_budget_chars
        self.extractor = extractor or build_default_extractor()

    def retrieve(self, query: str, scope: AgentScope, budget_chars: int | None = None, limit: int = 8) -> MemoryContext:
        budget = budget_chars or self.default_budget_chars
        results = self.engine.search(
            query,
            project_id=scope.project_id,
            limit=limit,
            mode=RetrievalMode.ANSWER_INJECTION,
        )
        lines: list[str] = []
        ids: list[int] = []
        items: list[dict[str, Any]] = []
        used_chars = 0
        for result in results:
            memory = result.memory
            if memory.id is None:
                continue
            prompt_text = _prompt_memory_text(memory)
            line = f"- [#{memory.id} R={result.current_strength:.3f}] {prompt_text}"
            if used_chars + len(line) > budget:
                break
            used_chars += len(line)
            lines.append(line)
            ids.append(memory.id)
            memory.exposure_count += 1
            self.engine.store.update(memory)
            items.append({
                "id": memory.id,
                "score": round(result.final_score, 4),
                "semantic_similarity": round(result.semantic_similarity, 4),
                "keyword_score": round(result.keyword_score, 4),
                "strength": round(result.current_strength, 4),
                "stability": round(memory.stability, 4),
                "difficulty": round(memory.difficulty, 4),
                "utility": round(memory.utility, 4),
                "trust": round(memory.trust_mean, 4),
                "interference": round(result.interference, 4),
                "content": memory.content,
                "summary": memory.summary,
                "tags": memory.tags,
                "status": memory.status.value,
            })
        text = "Relevant long-term memory:\n" + "\n".join(lines) if lines else ""
        return MemoryContext(text=text, memory_ids=ids, items=items)

    def observe(self, event: AgentEvent) -> MemoryWritePlan:
        writes: list[MemoryWrite] = []
        is_correction = _looks_like_correction(event.user_input)
        is_delete_request = _looks_like_delete_request(event.user_input)
        feedback_memories = _memories_for_feedback(self.engine, event.used_memory_ids, event.scope)
        if not is_delete_request:
            for memory_id in event.used_memory_ids:
                memory = self.engine.store.get(memory_id)
                if memory is None:
                    continue
                if is_correction:
                    if not _correction_applies_to_memory(event.user_input, memory):
                        continue
                    writes.append(MemoryWrite(op=MemoryOp.SUPERSEDE, target_id=memory_id, content=event.user_input, summary=event.user_input[:120]))
        for content in event.explicit_memories:
            clean = content.strip()
            if clean:
                writes.append(MemoryWrite(op=MemoryOp.ADD, content=clean))

        arbitration_results = self.engine.search(
            event.user_input,
            project_id=event.scope.project_id,
            limit=5,
            mode=RetrievalMode.WRITE_ARBITRATION,
        )
        retrieved = _retrieved_for_arbitration(arbitration_results)
        extracted = self.extractor.extract(
            user_input=event.user_input,
            agent_output=event.agent_output,
            tool_results=event.tool_results,
            project_id=event.scope.storage_project_id,
            retrieved_memories=retrieved,
        )
        writes.extend(w for w in extracted.writes if w.op != MemoryOp.NOOP)

        # 自动反馈：检测本条对话对检索记忆的影响
        feedback: list[dict[str, Any]] = []
        try:
            self.engine.evolution.process_turn(
                event.user_input,
                event.agent_output,
                feedback_memories,
                explicit_used_ids=event.explicit_used_memory_ids,
            )
            feedback = self.engine.evolution.last_feedback
        except Exception:
            pass  # 反馈检测失败不阻塞主流程

        writes = _normalize_writes(writes)
        if not writes:
            return MemoryWritePlan(
                writes=[MemoryWrite(op=MemoryOp.NOOP)],
                rationale="No reusable memory found.",
                feedback=feedback,
            )
        return MemoryWritePlan(
            writes=writes,
            rationale="Evidence feedback + extracted memories.",
            feedback=feedback,
        )

    def commit(self, plan: MemoryWritePlan, scope: AgentScope) -> list[Memory]:
        committed: list[Memory] = []
        for write in plan.writes:
            try:
                write_project_id = _project_id_for_write(write, scope)
                result = self.engine.apply_operation(
                    write.op,
                    content=write.content,
                    target_id=write.target_id,
                    project_id=write_project_id,
                    summary=write.summary,
                    tags=write.tags,
                    sensitivity=write.sensitivity,
                )
                if result is not None:
                    committed.append(result)
            except ValueError:
                # LLM 幻觉了一个不存在的 target_id，跳过
                pass
        return committed

    def consolidate(self, scope: AgentScope | None = None) -> dict[str, object]:
        return self.engine.sleep_consolidate()


class PiAgentMemoryHook:
    def __init__(self, adapter: BrainMemoryAdapter) -> None:
        self.adapter = adapter

    def before_agent_start(self, user_input: str, state: dict[str, Any]) -> dict[str, Any]:
        scope = _scope_from_state(state)
        context = self.adapter.retrieve(user_input, scope)
        state = dict(state)
        state["brainmemory_memory_context"] = context.text
        state["brainmemory_memory_ids"] = context.memory_ids
        state["csm_memory_context"] = context.text
        state["csm_memory_ids"] = context.memory_ids
        return state

    def agent_end(self, user_input: str, agent_output: str, state: dict[str, Any]) -> dict[str, Any]:
        scope = _scope_from_state(state)
        event = AgentEvent(
            user_input=user_input,
            agent_output=agent_output,
            used_memory_ids=list(state.get("brainmemory_memory_ids", state.get("csm_memory_ids", []))),
            explicit_used_memory_ids=list(state.get("brainmemory_used_memory_ids", [])),
            explicit_memories=list(state.get("brainmemory_explicit_memories", state.get("csm_explicit_memories", []))),
            scope=scope,
        )
        plan = self.adapter.observe(event)
        committed = self.adapter.commit(plan, scope)
        state = dict(state)
        write_plan = [w.op.value for w in plan.writes]
        committed_ids = [m.id for m in committed]
        state["brainmemory_write_plan"] = write_plan
        state["brainmemory_committed_ids"] = committed_ids
        state["brainmemory_feedback"] = plan.feedback or []
        state["csm_write_plan"] = write_plan
        state["csm_committed_ids"] = committed_ids
        return state


class OpenClawMemorySidecar:
    def __init__(self, adapter: BrainMemoryAdapter) -> None:
        self.adapter = adapter

    def handle_pre_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = _scope_from_payload(payload)
        query = str(payload.get("message") or payload.get("query") or "")
        context = self.adapter.retrieve(query, scope, budget_chars=int(payload.get("budget_chars", 1400)))
        return {"memory_context": context.text, "memory_ids": context.memory_ids, "items": context.items}

    def handle_post_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = _scope_from_payload(payload)
        event = AgentEvent(
            user_input=str(payload.get("message") or ""),
            agent_output=str(payload.get("agent_output") or ""),
            tool_results=[str(item) for item in payload.get("tool_results", [])],
            explicit_memories=[str(item) for item in payload.get("explicit_memories", [])],
            used_memory_ids=[int(item) for item in payload.get("memory_ids", [])],
            explicit_used_memory_ids=[int(item) for item in payload.get("used_memory_ids", [])],
            scope=scope,
        )
        plan = self.adapter.observe(event)
        committed = self.adapter.commit(plan, scope)
        return {
            "write_plan": [w.op.value for w in plan.writes],
            "committed_ids": [m.id for m in committed],
            "rationale": plan.rationale,
            "feedback": plan.feedback or [],
        }


class HermesMemoryProvider:
    def __init__(self, adapter: BrainMemoryAdapter) -> None:
        self.adapter = adapter

    def get_context(self, prompt: str, user_id: str = "default", project_id: str | None = None) -> str:
        return self.adapter.retrieve(prompt, AgentScope(user_id=user_id, project_id=project_id)).text

    def remember(self, content: str, user_id: str = "default", project_id: str | None = None) -> int | None:
        scope = AgentScope(user_id=user_id, project_id=project_id)
        plan = MemoryWritePlan(writes=[MemoryWrite(op=MemoryOp.ADD, content=content)], rationale="Provider remember call.")
        committed = self.adapter.commit(plan, scope)
        return committed[0].id if committed else None

    def sleep(self) -> dict[str, object]:
        return self.adapter.consolidate()


def _scope_from_state(state: dict[str, Any]) -> AgentScope:
    return AgentScope(
        user_id=str(state.get("user_id", "default")),
        project_id=state.get("project_id") or state.get("workspace_id"),
        channel=state.get("channel"),
        session_id=state.get("session_id"),
        metadata=dict(state.get("metadata", {})),
    )


def _scope_from_payload(payload: dict[str, Any]) -> AgentScope:
    return AgentScope(
        user_id=str(payload.get("user_id", "default")),
        project_id=payload.get("project_id") or payload.get("workspace_id"),
        channel=payload.get("channel"),
        session_id=payload.get("session_id"),
        metadata=dict(payload.get("metadata", {})),
    )


def _looks_like_correction(text: str) -> bool:
    lowered = text.lower()
    return any(
        signal in lowered
        for signal in ["纠正", "不对", "错了", "记错", "改用", "改成", "不是", "actually", "correction", "instead"]
    )


def _looks_like_delete_request(text: str) -> bool:
    lowered = text.lower()
    return any(
        signal in lowered
        for signal in ["忘记", "删除", "移除", "不要再记", "别再记", "forget", "delete", "remove"]
    )


def _project_id_for_write(write: MemoryWrite, scope: AgentScope) -> str | None:
    """Single-user mode: new memories follow the current project."""
    if write.op != MemoryOp.ADD:
        return None
    return scope.project_id


def _looks_like_project_memory(content: str, tags: str = "") -> bool:
    text = f"{content} {tags}".lower()
    strong_project_terms = [
        "项目", "workspace", "工作区", "仓库", "依赖", "部署", "数据库", "缓存",
        "docker", "compose", "bun", "pnpm", "npm", "yarn", "pytest", "fastapi",
        "react", "typescript", "sqlite", "postgresql", "redis",
        "project", "repo", "repository", "codebase", "team", "dependency", "deploy",
        "database", "cache",
    ]
    anchored_project_terms = [
        "命令", "流程", "团队", "代码", "规范", "约定", "代码风格",
        "command", "workflow", "convention", "coding style",
        "偏好", "喜欢", "风格", "回答",
        "preference", "prefer", "prefers", "style", "answer", "answers",
    ]
    if _has_project_anchor(text):
        return any(term in text for term in strong_project_terms + anchored_project_terms)
    if _looks_like_personal_memory(content, tags):
        return False
    return any(term in text for term in strong_project_terms)


def _looks_like_personal_memory(content: str, tags: str = "") -> bool:
    text = f"{content} {tags}".lower()
    personal_terms = [
        "我叫", "我的名字", "称呼我", "叫我", "姓名", "名字", "邮箱", "电话",
        "api_key", "api key", "token", "password", "密码", "偏好", "喜欢", "不喜欢",
        "身份", "联系", "wechat", "微信", "qq",
        "i prefer", "my preference", "call me", "my name", "email", "phone",
        "secret", "like", "dislike",
    ]
    return any(term in text for term in personal_terms)


def _has_project_anchor(text: str) -> bool:
    return any(term in text for term in [
        "这个项目", "本项目", "项目", "workspace", "工作区", "仓库", "团队",
        "this project", "the project", "project", "repo", "repository", "codebase", "team",
    ])


def _correction_applies_to_memory(text: str, memory: Memory) -> bool:
    memory_text = memory.text_for_index.lower()
    lowered = text.lower()
    name_terms = {"名字", "姓名", "称呼", "叫我", "我叫", "name", "call me"}
    if any(term in lowered for term in name_terms):
        return any(term in memory_text for term in name_terms | {"身份"})

    query_tokens = _meaningful_tokens(text)
    memory_tokens = _meaningful_tokens(memory.text_for_index)
    if not query_tokens or not memory_tokens:
        return False
    overlap = query_tokens & memory_tokens
    return len(overlap) / max(1, min(len(query_tokens), len(memory_tokens))) >= 0.18


def _meaningful_tokens(text: str) -> set[str]:
    stop = {"我", "你", "的", "了", "是", "在", "这", "这个", "一下", "之前"}
    return {token for token in tokenize(text) if token not in stop and len(token.strip()) > 0}


def _normalize_writes(writes: list[MemoryWrite]) -> list[MemoryWrite]:
    priority = {
        MemoryOp.UPDATE: 1,
        MemoryOp.ARCHIVE: 2,
        MemoryOp.SUPERSEDE: 3,
        MemoryOp.DELETE: 4,
    }
    result: list[MemoryWrite] = []
    best_by_target: dict[int, MemoryWrite] = {}
    for write in writes:
        if write.op == MemoryOp.NOOP:
            continue
        if write.target_id is None or write.op == MemoryOp.ADD:
            result.append(write)
            continue
        existing = best_by_target.get(write.target_id)
        if existing is None or priority.get(write.op, 0) >= priority.get(existing.op, 0):
            best_by_target[write.target_id] = write
    result.extend(best_by_target[target_id] for target_id in sorted(best_by_target))
    return result


def _merge_search_results(*groups: list[SearchResult]) -> list[SearchResult]:
    by_id: dict[int, SearchResult] = {}
    anonymous: list[SearchResult] = []
    for group in groups:
        for result in group:
            memory_id = result.memory.id
            if memory_id is None:
                anonymous.append(result)
                continue
            existing = by_id.get(memory_id)
            if existing is None or result.final_score > existing.final_score:
                by_id[memory_id] = result
    merged = list(by_id.values()) + anonymous
    merged.sort(key=lambda item: item.final_score, reverse=True)
    return merged


def filter_scoped_results(results: list[SearchResult], scope: AgentScope) -> list[SearchResult]:
    """Compatibility helper; user identity does not filter results."""
    return results


def _memories_for_feedback(
    engine: BrainMemoryEngine,
    memory_ids: list[int],
    scope: AgentScope,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for memory_id in dict.fromkeys(memory_ids):
        memory = engine.store.get(memory_id)
        if memory is None:
            continue
        items.append({
            "id": memory.id,
            "content": memory.content,
            "summary": memory.summary,
            "tags": memory.tags,
            "status": memory.status.value,
        })
    return items


def _prompt_memory_text(memory: Memory) -> str:
    """Return compact answer-injection text while preserving original memory content."""
    return _positive_injection_text(memory.summary or memory.content)


def _positive_injection_text(text: str) -> str:
    patterns = [
        re.compile(r"不要用[^，。；;]+[，。；;]\s*(这个项目)?只用(?P<keep>[^。；;]+)"),
        re.compile(r"不用[^，。；;]+[，。；;]\s*(这个项目)?只用(?P<keep>[^。；;]+)"),
        re.compile(r"不要使用[^，。；;]+[，。；;]\s*(这个项目)?使用(?P<keep>[^。；;]+)"),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            keep = match.group("keep").strip()
            if keep:
                return f"这个项目使用 {keep}。"
    return text


def _retrieved_for_arbitration(results: list[SearchResult]) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    for result in results:
        memory = result.memory
        if memory.id is None:
            continue
        memories.append({
            "id": memory.id, "content": memory.content,
            "summary": memory.summary, "tags": memory.tags,
            "status": memory.status.value,
        })
    return memories
