"""
LLM 记忆提取器 — 简化版

核心理念：不告诉 LLM "怎么判断"，只告诉它"你需要做什么"。
让 LLM 自由决定什么值得记住，而不是遵循预设规则。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .llm_config import load_llm_config
from .models import MemoryOp, MemoryWrite, MemoryWritePlan
from .security import MemorySecurityPolicy


# ═══════════════════════════════════════════════════════════════════
# 简洁提示词（替代原有的长篇操作规则）
# ═══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """You are the memory system for an AI coding agent.

Your job: observe conversations and decide what is worth remembering for the future.

Do not summarize the current conversation. Extract only durable information that will remain useful across sessions:
- User preferences, naming conventions, coding style
- Project decisions, constraints, dependencies
- Facts that were corrected or superseded
- Reusable commands, procedures, or patterns

Ignore: small talk, temporary states, one-off questions, content already represented in existing memories.

When existing memories are provided as context, you may update or supersede them if the new information contradicts or refines them.

Output JSON only. No explanations outside JSON."""

_SCHEMA_PROMPT = """Output format:
{
  "rationale": "brief explanation of your decisions",
  "writes": [
    {
      "op": "ADD",
      "content": "the memory content",
      "summary": "short summary (optional)",
      "tags": "comma-separated free-form tags (optional)",
      "target_id": null
    }
  ]
}

Available operations: ADD, UPDATE, SUPERSEDE, ARCHIVE, DELETE, NOOP.
- target_id is required for UPDATE, SUPERSEDE, ARCHIVE, and DELETE. It MUST be one of the "id" values from retrieved_memories. Do not invent IDs.
- tags are free-form: use whatever labels feel natural (e.g. "preference, coding-style", "project, dependencies").
- For ADD and SUPERSEDE, content is required and should be a self-contained memory (not a reply to the user). SUPRESSEDE creates a NEW memory and marks the old one as superseded.
- If nothing in the conversation is worth remembering long-term, return a single NOOP."""


# ═══════════════════════════════════════════════════════════════════
# Extractor 接口
# ═══════════════════════════════════════════════════════════════════

class MemoryExtractor(Protocol):
    def extract(
        self,
        user_input: str,
        agent_output: str = "",
        tool_results: list[str] | None = None,
        project_id: str | None = None,
        retrieved_memories: list[dict[str, Any]] | None = None,
    ) -> MemoryWritePlan:
        ...


class LLMExtractorNotConfigured(RuntimeError):
    pass


@dataclass(slots=True)
class LLMUsageEstimate:
    input_chars: int
    approx_input_tokens: int
    max_output_tokens: int
    will_call_api: bool
    reason: str


# ═══════════════════════════════════════════════════════════════════
# Null / JSON 提取器
# ═══════════════════════════════════════════════════════════════════

class NullMemoryExtractor:
    """无 LLM 时的空操作提取器。"""

    def extract(self, **kwargs: Any) -> MemoryWritePlan:
        return MemoryWritePlan(
            writes=[MemoryWrite(op=MemoryOp.NOOP)],
            rationale="LLM extractor not configured.",
        )


class JSONMemoryExtractor:
    """校验 LLM 输出的 JSON 提取器。"""

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], str | dict[str, Any]],
        security_policy: MemorySecurityPolicy | None = None,
    ) -> None:
        self.generator = generator
        self.security_policy = security_policy or MemorySecurityPolicy()

    def extract(self, **kwargs: Any) -> MemoryWritePlan:
        payload = {
            "user_input": kwargs.get("user_input", ""),
            "agent_output": kwargs.get("agent_output", ""),
            "tool_results": kwargs.get("tool_results") or [],
            "project_id": kwargs.get("project_id"),
            "retrieved_memories": kwargs.get("retrieved_memories") or [],
            "schema": memory_extractor_schema(),
        }
        raw = self.generator(payload)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return self.security_policy.apply(parse_memory_write_plan(data))


# ═══════════════════════════════════════════════════════════════════
# DeepSeek 提取器
# ═══════════════════════════════════════════════════════════════════

class DeepSeekMemoryExtractor:
    """DeepSeek 驱动的 LLM 记忆仲裁器。"""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        max_input_chars: int = 6000,
        max_output_tokens: int = 800,
        temperature: float = 0.0,
        thinking: str = "disabled",
        timeout_seconds: int = 30,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        security_policy: MemorySecurityPolicy | None = None,
    ) -> None:
        if not api_key:
            raise LLMExtractorNotConfigured("DeepSeek API key is required.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_input_chars = max_input_chars
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.thinking = thinking
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.security_policy = security_policy or MemorySecurityPolicy()
        self._cache: dict[str, MemoryWritePlan] = {}

    @classmethod
    def from_env(cls) -> "DeepSeekMemoryExtractor":
        config = load_llm_config(include_secret=True)
        api_key = config.api_key or os.environ.get("CSM_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMExtractorNotConfigured("Set CSM_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.")
        return cls(
            api_key=api_key,
            model=config.model,
            base_url=config.base_url,
            max_input_chars=config.max_input_chars,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            thinking=config.thinking,
        )

    def estimate(self, user_input: str, **kwargs: Any) -> LLMUsageEstimate:
        payload = self._build_dynamic_payload(user_input, kwargs)
        chars = len(payload)
        if not user_input.strip():
            return LLMUsageEstimate(chars, _approx_tokens(chars), self.max_output_tokens, False, "empty input")
        if chars > self.max_input_chars:
            return LLMUsageEstimate(chars, _approx_tokens(chars), self.max_output_tokens, False, "input too large")
        return LLMUsageEstimate(chars, _approx_tokens(chars), self.max_output_tokens, True, "ok")

    def extract(self, user_input: str = "", **kwargs: Any) -> MemoryWritePlan:
        estimate = self.estimate(user_input, **kwargs)
        if not estimate.will_call_api:
            return MemoryWritePlan([MemoryWrite(op=MemoryOp.NOOP)], f"Skipped: {estimate.reason}")

        request_payload = self._build_request(user_input, kwargs)
        cache_key = _stable_hash(request_payload)
        if cache_key in self._cache:
            return self._cache[cache_key]

        response = self.transport(request_payload) if self.transport else self._post(request_payload)
        content = _extract_message_content(response)
        if not content.strip():
            raise ValueError("DeepSeek returned empty content.")
        plan = self.security_policy.apply(parse_memory_write_plan(json.loads(content)))
        self._cache[cache_key] = plan
        return plan

    def dry_run_request(self, user_input: str = "", **kwargs: Any) -> dict[str, Any]:
        estimate = self.estimate(user_input, **kwargs)
        return {
            "estimate": {
                "input_chars": estimate.input_chars,
                "approx_input_tokens": estimate.approx_input_tokens,
                "max_output_tokens": estimate.max_output_tokens,
                "will_call_api": estimate.will_call_api,
                "reason": estimate.reason,
            },
            "request": self._build_request(user_input, kwargs),
        }

    def probe_request(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Output strict JSON only."},
                {"role": "user", "content": 'Return exactly {"ok":true}.'},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": 32,
            "thinking": {"type": self.thinking},
        }

    def live_probe(self) -> dict[str, Any]:
        response = self.transport(self.probe_request()) if self.transport else self._post(self.probe_request())
        content = _extract_message_content(response)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("DeepSeek probe returned invalid JSON.") from exc
        return {"ok": bool(data.get("ok")), "raw": data}

    def _build_request(self, user_input: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _SCHEMA_PROMPT},
                {"role": "user", "content": self._build_dynamic_payload(user_input, kwargs)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "thinking": {"type": self.thinking},
        }

    def _build_dynamic_payload(self, user_input: str, kwargs: dict[str, Any]) -> str:
        compact = {
            "project_id": kwargs.get("project_id"),
            "user_input": user_input,
            "agent_output": kwargs.get("agent_output", ""),
            "tool_results": (kwargs.get("tool_results") or [])[:5],
            "retrieved_memories": _compact_retrieved_memories(kwargs.get("retrieved_memories") or []),
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek API unreachable: {exc.reason}") from exc


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def build_default_extractor() -> MemoryExtractor:
    try:
        return DeepSeekMemoryExtractor.from_env()
    except LLMExtractorNotConfigured:
        return NullMemoryExtractor()


def memory_extractor_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["rationale", "writes"],
        "properties": {
            "rationale": {"type": "string"},
            "writes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["op"],
                    "properties": {
                        "op": {"enum": [op.value for op in MemoryOp]},
                        "content": {"type": "string"},
                        "target_id": {"type": ["integer", "null"]},
                        "summary": {"type": "string"},
                        "tags": {"type": "string"},
                    },
                },
            },
        },
    }


def parse_memory_write_plan(data: dict[str, Any]) -> MemoryWritePlan:
    if not isinstance(data, dict):
        raise ValueError("Extractor output must be a JSON object.")
    rationale = data.get("rationale", "")
    writes_data = data.get("writes")
    if not isinstance(rationale, str):
        raise ValueError("rationale must be a string.")
    if not isinstance(writes_data, list):
        raise ValueError("writes must be an array.")
    writes = [_parse_write(item, index) for index, item in enumerate(writes_data)]
    if not writes:
        writes.append(MemoryWrite(op=MemoryOp.NOOP))
    return MemoryWritePlan(writes=writes, rationale=str(rationale))


def _parse_write(item: dict[str, Any], index: int) -> MemoryWrite:
    if not isinstance(item, dict):
        raise ValueError(f"writes[{index}] must be an object.")
    try:
        op = MemoryOp(item["op"])
    except (KeyError, ValueError):
        raise ValueError(f"writes[{index}] has invalid op.")
    target_id = item.get("target_id")
    if target_id is not None and not isinstance(target_id, int):
        raise ValueError(f"writes[{index}].target_id must be integer or null.")
    content = str(item.get("content", "")).strip()
    if op in {MemoryOp.ADD, MemoryOp.SUPERSEDE} and not content:
        raise ValueError(f"writes[{index}] with {op.value} requires content.")
    if op in {MemoryOp.UPDATE, MemoryOp.SUPERSEDE, MemoryOp.ARCHIVE, MemoryOp.DELETE} and target_id is None:
        raise ValueError(f"writes[{index}] with {op.value} requires target_id.")
    return MemoryWrite(
        op=op,
        content=content,
        target_id=target_id,
        summary=str(item.get("summary", "")).strip(),
        tags=str(item.get("tags", "")).strip(),
    )


def _compact_retrieved_memories(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in memories[:8]:
        if "id" not in item:
            continue
        compacted.append({
            "id": item.get("id"),
            "content": item.get("content"),
            "summary": item.get("summary", ""),
            "tags": item.get("tags", ""),
            "status": item.get("status", "active"),
        })
    return compacted


def _extract_message_content(response: dict[str, Any]) -> str:
    try:
        return str(response["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("DeepSeek response missing choices[0].message.content") from exc


def _approx_tokens(chars: int) -> int:
    return max(1, (chars + 3) // 4)


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
