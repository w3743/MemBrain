import pytest
import os
import json

from csm_agent.extractor import (
    DeepSeekMemoryExtractor, JSONMemoryExtractor, LLMExtractorNotConfigured,
    build_default_extractor, parse_memory_write_plan,
)
from csm_agent.models import MemoryOp


def test_default_extractor_without_key_is_noop() -> None:
    old_deepseek = os.environ.pop("DEEPSEEK_API_KEY", None)
    old_csm_deepseek = os.environ.pop("CSM_DEEPSEEK_API_KEY", None)
    old_config_path = os.environ.get("CSM_LLM_CONFIG_PATH")
    os.environ["CSM_LLM_CONFIG_PATH"] = "__missing_test_llm_config__.json"
    try:
        extractor = build_default_extractor()
        plan = extractor.extract(user_input="以后回答技术问题时，请先给结论。")
        assert plan.writes[0].op == MemoryOp.NOOP
    finally:
        if old_deepseek is not None:
            os.environ["DEEPSEEK_API_KEY"] = old_deepseek
        if old_csm_deepseek is not None:
            os.environ["CSM_DEEPSEEK_API_KEY"] = old_csm_deepseek
        if old_config_path is None:
            os.environ.pop("CSM_LLM_CONFIG_PATH", None)
        else:
            os.environ["CSM_LLM_CONFIG_PATH"] = old_config_path


def test_deepseek_requires_api_key() -> None:
    with pytest.raises(LLMExtractorNotConfigured):
        DeepSeekMemoryExtractor("")


def test_json_extractor_parses_valid_llm_output() -> None:
    extractor = JSONMemoryExtractor(
        lambda payload: {
            "rationale": "User stated a durable preference.",
            "writes": [{"op": "ADD", "content": "用户偏好先给结论。", "tags": "偏好"}],
        }
    )
    plan = extractor.extract(user_input="以后先给结论。")
    assert plan.writes[0].op == MemoryOp.ADD
    assert plan.writes[0].tags == "偏好"


def test_json_extractor_rejects_invalid_schema() -> None:
    with pytest.raises(ValueError):
        parse_memory_write_plan({"rationale": "bad", "writes": [{"op": "ADD"}]})


def test_security_marks_email_without_redacting() -> None:
    extractor = JSONMemoryExtractor(
        lambda payload: {
            "rationale": "contact memory",
            "writes": [{"op": "ADD", "content": "默认联系邮箱使用 user@example.com。"}],
        }
    )
    plan = extractor.extract(user_input="默认联系邮箱使用 user@example.com。")
    assert plan.writes[0].op == MemoryOp.ADD
    assert "user@example.com" in plan.writes[0].content


def test_security_marks_secret_without_rejecting() -> None:
    extractor = JSONMemoryExtractor(
        lambda payload: {
            "rationale": "secret",
            "writes": [{"op": "ADD", "content": "api_key = sk_test_1234567890"}],
        }
    )
    plan = extractor.extract(user_input="记住 api_key = sk_test_1234567890")
    assert plan.writes[0].op == MemoryOp.ADD


def test_deepseek_dry_run_and_fake_transport_cache() -> None:
    calls = 0

    def transport(payload):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": '{"rationale":"ok","writes":[{"op":"ADD","content":"用户偏好先给结论。"}]}'}}]}

    extractor = DeepSeekMemoryExtractor("test-key", transport=transport)
    dry = extractor.dry_run_request("以后先给结论。", project_id="demo")
    assert dry["estimate"]["will_call_api"] is True

    first = extractor.extract(user_input="以后先给结论。", project_id="demo")
    second = extractor.extract(user_input="以后先给结论。", project_id="demo")
    assert first.writes[0].op == MemoryOp.ADD
    assert second.writes[0].content == first.writes[0].content
    assert calls == 1


def test_deepseek_prompt_is_simplified() -> None:
    extractor = DeepSeekMemoryExtractor("test-key", transport=lambda p: {})
    request = extractor.dry_run_request("我的名字叫江家裕。", project_id="demo",
        retrieved_memories=[{"id": 7, "content": "用户偏好被称为家裕。"}])["request"]
    messages = request["messages"]
    combined = "\n".join(m["content"] for m in messages)

    # 简化后的 prompt 不应包含长规则
    assert len(messages) == 3  # system + schema + dynamic
    assert "durable information" in combined
    # 不应包含旧版的长篇操作规则
    assert "core_identity" not in combined


def test_deepseek_probe_requires_explicit_call_path() -> None:
    calls = 0

    def transport(payload):
        nonlocal calls
        calls += 1
        assert payload["max_tokens"] == 32
        return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    extractor = DeepSeekMemoryExtractor("test-key", transport=transport)
    request = extractor.probe_request()
    assert request["max_tokens"] == 32
    assert calls == 0

    result = extractor.live_probe()
    assert result["ok"] is True
    assert calls == 1


def test_deepseek_skips_oversized_input_without_transport_call() -> None:
    def transport(payload):
        raise AssertionError("transport should not be called")

    extractor = DeepSeekMemoryExtractor("test-key", max_input_chars=10, transport=transport)
    plan = extractor.extract(user_input="这是一段非常长的输入，应该被本地跳过。")
    assert plan.writes[0].op == MemoryOp.NOOP
