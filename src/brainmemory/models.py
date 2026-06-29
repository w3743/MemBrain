"""
CSM Agent — 简化核心模型

核心理念：智能从简单算法中涌现，而非预设规则。
只保留两个基本力：重复强化 + 时间衰减。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


DEFAULT_STABILITY: float = 27.465307216702744  # ln(3) / (2 * 0.02)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dt_to_str(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# ── 记忆状态 ──────────────────────────────────────────────

class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


# ── 记忆操作 ──────────────────────────────────────────────

class MemoryOp(StrEnum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    NOOP = "NOOP"
    ARCHIVE = "ARCHIVE"
    DELETE = "DELETE"


# ── 写入计划 ──────────────────────────────────────────────

@dataclass(slots=True)
class MemoryWrite:
    op: MemoryOp
    content: str = ""
    target_id: int | None = None
    summary: str = ""
    tags: str = ""
    sensitivity: str = "normal"


@dataclass(slots=True)
class MemoryWritePlan:
    writes: list[MemoryWrite]
    rationale: str = ""
    feedback: list[dict[str, Any]] | None = None


# ── Memory ────────────────────────────────────────────────

@dataclass(slots=True)
class Memory:
    id: int | None
    content: str
    summary: str = ""
    strength: float = 0.6
    access_count: int = 0
    last_accessed_at: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: int | None = None
    tags: str = ""
    scope: str = "user"
    project_id: str | None = None
    sensitivity: str = "normal"
    # ── 自适应进化字段 ──
    decay_rate: float = 0.02      # 每条记忆自己的衰减率，从默认值开始自调
    boost: float = 0.0             # 检索偏向，被用到+0.05，被无视-0.02
    trust: float = 0.5             # 信任度，验证正确+，被纠正-
    error_count: int = 0           # 被纠正次数
    verify_count: int = 0          # 被验证正确的次数
    stability: float = DEFAULT_STABILITY
    difficulty: float = 0.5
    utility: float = 0.5
    trust_alpha: float = 2.0
    trust_beta: float = 2.0
    exposure_count: int = 0
    correction_count: int = 0
    # ── 元数据 ──
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def text_for_index(self) -> str:
        return " ".join(part for part in [self.content, self.summary, self.tags] if part)

    @property
    def trust_mean(self) -> float:
        total = self.trust_alpha + self.trust_beta
        return self.trust_alpha / total if total > 0 else self.trust

    def sync_trust(self) -> None:
        self.trust = self.trust_mean

    def ensure_trust_distribution(self) -> None:
        if abs(self.trust - self.trust_mean) > 0.05:
            self.trust_alpha = max(0.1, self.trust * 4.0)
            self.trust_beta = max(0.1, (1.0 - self.trust) * 4.0)

    @classmethod
    def from_row(cls, row: Any) -> "Memory":
        def g(k, default=None):
            try:
                v = row[k]
                return v if v is not None else default
            except (KeyError, IndexError):
                return default

        return cls(
            id=row["id"],
            content=row["content"],
            summary=g("summary", ""),
            strength=float(g("strength") or g("base_strength") or 0.6),
            access_count=int(g("access_count") or g("use_count") or 0),
            last_accessed_at=parse_dt(g("last_accessed_at") or g("last_used_at")),
            status=MemoryStatus(g("status", "active")),
            superseded_by=g("superseded_by"),
            tags=g("tags", ""),
            scope=g("scope", "user"),
            project_id=g("project_id"),
            sensitivity=g("sensitivity", "normal"),
            decay_rate=float(g("decay_rate") or 0.02),
            boost=float(g("boost") or 0.0),
            trust=float(g("trust") or 0.5),
            error_count=int(g("error_count") or 0),
            verify_count=int(g("verify_count") or 0),
            stability=float(g("stability", DEFAULT_STABILITY)),
            difficulty=float(g("difficulty", 0.5)),
            utility=float(g("utility", 0.5)),
            trust_alpha=float(g("trust_alpha", 2.0)),
            trust_beta=float(g("trust_beta", 2.0)),
            exposure_count=int(g("exposure_count", 0)),
            correction_count=int(g("correction_count", 0)),
            created_at=parse_dt(g("created_at")),
            updated_at=parse_dt(g("updated_at")),
        )
