"""
向量嵌入后端

运行时统一使用本地 BAAI/bge-large-zh-v1.5 嵌入。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
DEFAULT_LOCAL_MODEL = "BAAI/bge-large-zh-v1.5"
PROJECT_LOCAL_MODEL = Path(__file__).resolve().parents[2] / "models" / "bge-large-zh-v1.5"


class EmbeddingBackend(Protocol):
    """嵌入后端接口。"""
    name: str

    def embed(self, text: str) -> list[float]:
        ...


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text):
        token = token.lower()
        if CJK_RE.search(token):
            chars = [ch for ch in token if CJK_RE.match(ch)]
            tokens.extend(chars)
            tokens.extend("".join(chars[i : i + 2]) for i in range(max(0, len(chars) - 1)))
        else:
            tokens.append(token)
    return tokens


class LocalSentenceTransformerEmbeddingBackend:
    """本地 sentence-transformers 嵌入 — 推荐方案。

    使用 BAAI/bge-large-zh-v1.5（1024 维），中文语义理解能力强大。
    模型首次加载时自动下载（约 1.3GB），之后缓存于本地。
    """

    name = "local_bge_large_zh"

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for local embeddings. "
                "Run: pip install sentence-transformers"
            ) from exc
        self.model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        vector = self.model.encode(text or "", normalize_embeddings=True)
        return [float(item) for item in vector]


def build_embedding_backend_from_env() -> EmbeddingBackend:
    """根据环境构建本地 BGE 嵌入后端。

    CSM_EMBEDDING_BACKEND 仅接受空值、local、sentence-transformers。
    项目运行时不再回退 hash；缺少依赖或模型时应直接报错，避免系统悄悄降级。
    """
    backend_env = os.environ.get("CSM_EMBEDDING_BACKEND", "").strip().lower()

    if backend_env in {"", "local", "sentence-transformers", "sentence_transformers"}:
        model_name = os.environ.get("CSM_EMBEDDING_MODEL") or _default_model_path()
        return LocalSentenceTransformerEmbeddingBackend(str(model_name))

    raise ValueError(f"unsupported CSM_EMBEDDING_BACKEND: {backend_env}")


def embedding_config_from_env() -> dict[str, str | int | None]:
    backend_env = os.environ.get("CSM_EMBEDDING_BACKEND", "").strip().lower() or "local"
    model = os.environ.get("CSM_EMBEDDING_MODEL") or str(_default_model_path())
    return {
        "backend": backend_env,
        "model": model,
        "default_local_model": DEFAULT_LOCAL_MODEL,
        "available": _detect_available_backends(),
    }


def _detect_available_backends() -> list[str]:
    try:
        import sentence_transformers  # noqa: F401
        return ["local"]
    except ImportError:
        return []


def _default_model_path() -> str:
    if PROJECT_LOCAL_MODEL.exists():
        return str(PROJECT_LOCAL_MODEL)
    return DEFAULT_LOCAL_MODEL


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。假设向量已归一化，结果为 [0, 1]。"""
    if not a or not b:
        return 0.0
    return max(0.0, sum(x * y for x, y in zip(a, b)))
