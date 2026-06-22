"""Continuous Strength Memory — simple algorithms, emergent intelligence."""

from .engine import CSMEngine
from .models import Memory, MemoryOp, MemoryStatus, MemoryWrite, MemoryWritePlan
from .embedding import LocalSentenceTransformerEmbeddingBackend, build_embedding_backend_from_env
from .retrieval import HybridRetriever, RetrievalMode, SearchResult
from .store import MemoryStore
from .strength import INITIAL_STRENGTH, DECAY_RATE, current_strength, reinforce, resolve_layer
from .security import MemorySecurityPolicy, classify_sensitivity
from .evolution import EvolutionEngine, apply_feedback, detect_feedback, inherit_from
from .adapters import CSMMemoryAdapter, HermesMemoryProvider, OpenClawMemorySidecar, PiAgentMemoryHook
from .extractor import (
    DeepSeekMemoryExtractor, JSONMemoryExtractor, NullMemoryExtractor,
    build_default_extractor, memory_extractor_schema, parse_memory_write_plan,
)
from .server import create_handler, run_server
from .api_contract import openapi_spec

__all__ = [
    "CSMEngine", "MemoryStore",
    "Memory", "MemoryOp", "MemoryStatus", "MemoryWrite", "MemoryWritePlan",
    "LocalSentenceTransformerEmbeddingBackend", "build_embedding_backend_from_env",
    "HybridRetriever", "RetrievalMode", "SearchResult",
    "INITIAL_STRENGTH", "DECAY_RATE", "current_strength", "reinforce", "resolve_layer",
    "MemorySecurityPolicy", "classify_sensitivity",
    "EvolutionEngine", "apply_feedback", "detect_feedback", "inherit_from",
    "CSMMemoryAdapter", "HermesMemoryProvider", "OpenClawMemorySidecar", "PiAgentMemoryHook",
    "DeepSeekMemoryExtractor", "JSONMemoryExtractor", "NullMemoryExtractor",
    "build_default_extractor", "memory_extractor_schema", "parse_memory_write_plan",
    "create_handler", "run_server", "openapi_spec",
]
