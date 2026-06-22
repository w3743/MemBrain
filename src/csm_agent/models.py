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
    # ── 元数据 ──
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def text_for_index(self) -> str:
        return " ".join(part for part in [self.content, self.summary, self.tags] if part)

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
            created_at=parse_dt(g("created_at")),
            updated_at=parse_dt(g("updated_at")),
        )
