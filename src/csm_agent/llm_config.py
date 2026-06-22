"""
LLM 配置管理
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LLM_CONFIG_PATH = ".csm_llm_config.json"


@dataclass(slots=True)
class LLMConfig:
    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.0
    max_output_tokens: int = 800
    max_input_chars: int = 6000
    thinking: str = "disabled"


def llm_config_path() -> Path:
    return Path(os.environ.get("CSM_LLM_CONFIG_PATH", DEFAULT_LLM_CONFIG_PATH))


def load_llm_config(include_secret: bool = True) -> LLMConfig:
    data: dict[str, Any] = {}
    path = llm_config_path()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    api_key = str(data.get("api_key") or os.environ.get("CSM_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")
    if not include_secret:
        api_key = _mask_secret(api_key)
    return LLMConfig(
        provider=str(data.get("provider") or "deepseek"),
        api_key=api_key,
        model=str(data.get("model") or os.environ.get("CSM_DEEPSEEK_MODEL") or "deepseek-v4-flash"),
        base_url=str(data.get("base_url") or os.environ.get("CSM_DEEPSEEK_BASE_URL") or "https://api.deepseek.com"),
        temperature=float(data.get("temperature", 0.0)),
        max_output_tokens=int(data.get("max_output_tokens") or os.environ.get("CSM_LLM_MAX_OUTPUT_TOKENS") or 800),
        max_input_chars=int(data.get("max_input_chars") or os.environ.get("CSM_LLM_MAX_INPUT_CHARS") or 6000),
        thinking=str(data.get("thinking") or "disabled"),
    )


def save_llm_config(payload: dict[str, Any]) -> LLMConfig:
    current = load_llm_config(include_secret=True)
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key or set(api_key) == {"*"}:
        api_key = current.api_key
    config = LLMConfig(
        provider=str(payload.get("provider") or current.provider or "deepseek"),
        api_key=api_key,
        model=str(payload.get("model") or current.model),
        base_url=str(payload.get("base_url") or current.base_url).rstrip("/"),
        temperature=_bounded_float(payload.get("temperature", current.temperature), 0.0, 2.0),
        max_output_tokens=_bounded_int(payload.get("max_output_tokens", current.max_output_tokens), 32, 8192),
        max_input_chars=_bounded_int(payload.get("max_input_chars", current.max_input_chars), 256, 200000),
        thinking=str(payload.get("thinking") or current.thinking or "disabled"),
    )
    data = {
        "provider": config.provider, "api_key": config.api_key,
        "model": config.model, "base_url": config.base_url,
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "max_input_chars": config.max_input_chars,
        "thinking": config.thinking,
    }
    llm_config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def public_llm_config() -> dict[str, Any]:
    config = load_llm_config(include_secret=False)
    return {
        "provider": config.provider, "api_key": config.api_key,
        "configured": bool(load_llm_config(include_secret=True).api_key),
        "model": config.model, "base_url": config.base_url,
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "max_input_chars": config.max_input_chars,
        "thinking": config.thinking,
    }


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _bounded_float(value: Any, min_value: float, max_value: float) -> float:
    parsed = float(value)
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"value must be between {min_value} and {max_value}")
    return parsed


def _bounded_int(value: Any, min_value: int, max_value: int) -> int:
    parsed = int(value)
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"value must be between {min_value} and {max_value}")
    return parsed
