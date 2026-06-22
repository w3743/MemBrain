"""
安全策略 — 仅标注敏感度，不修改记忆内容
"""

from __future__ import annotations

import re

from .models import MemoryWrite, MemoryWritePlan


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|access[_-]?key)\b")


class MemorySecurityPolicy:
    """根据内容自动标注敏感度。"""

    def apply(self, plan: MemoryWritePlan) -> MemoryWritePlan:
        return MemoryWritePlan(
            writes=[self._apply(write) for write in plan.writes],
            rationale=plan.rationale,
        )

    def _apply(self, write: MemoryWrite) -> MemoryWrite:
        s = classify_sensitivity(write.content)
        if s != write.sensitivity:
            return MemoryWrite(op=write.op, content=write.content, target_id=write.target_id,
                               summary=write.summary, tags=write.tags, sensitivity=s)
        return write


def classify_sensitivity(text: str) -> str:
    if SECRET_RE.search(text):
        return "secret"
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        return "personal"
    return "normal"
