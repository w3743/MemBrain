"""
评测体系 — 抽取 / 检索 / 端到端 / 强度模型 / 语义质量
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from .adapters import BrainMemoryAdapter, PiAgentMemoryHook
from .engine import BrainMemoryEngine
from .extractor import JSONMemoryExtractor, MemoryExtractor
from .models import Memory, MemoryOp, utc_now


# ═══════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ExtractionCase:
    id: str
    user_input: str
    project_id: str | None
    expected_op: MemoryOp
    must_contain: str = ""
    must_not_contain: str = ""


@dataclass(slots=True)
class RetrievalCase:
    id: str
    query: str
    project_id: str | None
    expected_contains: str
    expected_any: tuple[str, ...] = ()
    forbidden_contains: str = ""
    k: int = 3
    min_score: float = 0.0
    category: str = "general"


@dataclass(slots=True)
class EndToEndCase:
    id: str
    project_id: str | None
    history: list[str]
    query: str
    expected_contains: str
    forbidden_contains: str = ""


@dataclass(slots=True)
class StrengthCase:
    id: str
    type: str  # decay | reinforce
    initial_strength: float
    days_elapsed: float
    access_count: int
    expected_range: list[float]  # [min, max]
    expected_layer: str = ""


# ── 结果 ──────────────────────────────────────────────────────

@dataclass(slots=True)
class EvalResult:
    total: int = 0
    passed: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(slots=True)
class RetrievalEvalResult:
    total: int = 0
    positive_total: int = 0
    negative_total: int = 0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0  # Mean Reciprocal Rank
    ndcg: float = 0.0  # Normalized Discounted Cumulative Gain
    forbidden_hit_rate: float = 0.0
    avg_first_score: float = 0.0
    no_answer_accuracy: float = 0.0
    recall_ci95: tuple[float, float] = (0.0, 0.0)
    category_metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    per_case: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class E2EResult:
    total: int = 0
    passed: int = 0
    memory_pollution_rate: float = 0.0
    stale_reference_rate: float = 0.0
    avg_context_chars: float = 0.0
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(slots=True)
class StrengthEvalResult:
    total: int = 0
    passed: int = 0
    decay_ok: int = 0
    reinforce_ok: int = 0
    threshold_ok: int = 0  # retained in the report schema for compatibility
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


# ═══════════════════════════════════════════════════════════════════
# 加载器
# ═══════════════════════════════════════════════════════════════════

def _parse_jsonl(path: str | Path, builder) -> list:
    cases = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            cases.append(builder(json.loads(stripped), line_no))
        except Exception as exc:
            raise ValueError(f"{path}:{line_no} parse error: {exc}") from exc
    return cases


def load_extraction_cases(path: str | Path) -> list[ExtractionCase]:
    def build(data, _ln):
        return ExtractionCase(
            id=str(data["id"]), user_input=str(data["user_input"]),
            project_id=data.get("project_id"),
            expected_op=MemoryOp(data["expected_op"]),
            must_contain=str(data.get("must_contain", "")),
            must_not_contain=str(data.get("must_not_contain", "")),
        )
    return _parse_jsonl(path, build)


def load_retrieval_cases(path: str | Path) -> list[RetrievalCase]:
    def build(data, _ln):
        expected_contains = str(data.get("expected_contains", ""))
        raw_expected_any = data.get("expected_any", [])
        if not isinstance(raw_expected_any, list):
            raise ValueError("expected_any must be a list")
        expected_any = tuple(str(item) for item in raw_expected_any if str(item))
        if expected_contains and expected_contains not in expected_any:
            expected_any = (expected_contains, *expected_any)
        return RetrievalCase(
            id=str(data["id"]), query=str(data["query"]),
            project_id=data.get("project_id"),
            expected_contains=expected_contains,
            expected_any=expected_any,
            forbidden_contains=str(data.get("forbidden_contains", "")),
            k=int(data.get("k", 3)),
            min_score=float(data.get("min_score", 0.0)),
            category=str(data.get("category") or _infer_retrieval_category(str(data["id"]))),
        )
    return _parse_jsonl(path, build)


def _infer_retrieval_category(case_id: str) -> str:
    if case_id.startswith(("ret_empty", "ret_nonexist", "ret_short", "ret_negative")):
        return "negative"
    if case_id.startswith("ret_isolation"):
        return "scope"
    if case_id.startswith(("ret_cn_", "ret_en_", "ret_cross")):
        return "cross_language"
    if case_id.startswith("ret_rank"):
        return "ranking"
    if case_id.startswith("ret_sem"):
        return "paraphrase"
    if case_id.startswith("ret_partial"):
        return "partial"
    return "exact"


def load_end_to_end_cases(path: str | Path) -> list[EndToEndCase]:
    def build(data, ln):
        history = data.get("history", data.get("history", []))
        if not isinstance(history, list):
            raise ValueError(f"line {ln}: history must be a list")
        return EndToEndCase(
            id=str(data["id"]), project_id=data.get("project_id"),
            history=[str(h) for h in history],
            query=str(data["query"]),
            expected_contains=str(data.get("expected_contains", "")),
            forbidden_contains=str(data.get("forbidden_contains", "")),
        )
    return _parse_jsonl(path, build)


def load_strength_cases(path: str | Path) -> list[StrengthCase]:
    def build(data, _ln):
        return StrengthCase(
            id=str(data["id"]), type=str(data["type"]),
            initial_strength=float(data["initial_strength"]),
            days_elapsed=float(data["days_elapsed"]),
            access_count=int(data["access_count"]),
            expected_range=[float(x) for x in data["expected_range"]],
        )
    return _parse_jsonl(path, build)


# ═══════════════════════════════════════════════════════════════════
# 1. 抽取评测
# ═══════════════════════════════════════════════════════════════════

def evaluate_extractor(extractor: MemoryExtractor, cases: list[ExtractionCase]) -> EvalResult:
    failures = []
    stats = defaultdict(int)
    for case in cases:
        plan = extractor.extract(user_input=case.user_input, project_id=case.project_id)
        first = plan.writes[0]
        reasons = []

        if first.op != case.expected_op:
            reasons.append(f"expected {case.expected_op.value}, got {first.op.value}")
            stats[f"op_mismatch_{case.expected_op.value}"] += 1

        if case.must_contain and case.must_contain not in first.content:
            reasons.append(f"missing '{case.must_contain}'")
            stats["missing_content"] += 1

        if case.must_not_contain and case.must_not_contain in first.content:
            reasons.append(f"should not contain '{case.must_not_contain}'")
            stats["unwanted_content"] += 1

        if reasons:
            failures.append({"id": case.id, "reasons": "; ".join(reasons)})
        stats["total"] += 1

    return EvalResult(
        total=len(cases), passed=len(cases) - len(failures), failures=failures,
        metadata={"op_distribution": {k: v for k, v in stats.items() if k != "total"}},
    )


def evaluate_mock_llm_fixture(fixture_path: str | Path) -> EvalResult:
    cases = load_extraction_cases(fixture_path)
    by_id = {case.id: case for case in cases}

    def generator(payload: dict[str, Any]) -> dict[str, Any]:
        user_input = str(payload["user_input"])
        case = next(c for c in by_id.values() if c.user_input == user_input)
        write: dict[str, Any] = {"op": case.expected_op.value}
        if case.expected_op != MemoryOp.NOOP:
            write.update({"content": user_input, "summary": user_input[:120], "tags": ""})
            if case.expected_op == MemoryOp.SUPERSEDE:
                write["target_id"] = 1
        return {"rationale": "mock", "writes": [write]}

    return evaluate_extractor(JSONMemoryExtractor(generator), cases)


# ═══════════════════════════════════════════════════════════════════
# 2. 检索评测（含 MRR、NDCG）
# ═══════════════════════════════════════════════════════════════════

def evaluate_retrieval_full(engine: BrainMemoryEngine, cases: list[RetrievalCase]) -> RetrievalEvalResult:
    failures = []
    recall_hits = 0
    precision_sum = 0.0
    forbidden_hits = 0
    forbidden_total = 0
    mrr_sum = 0.0
    ndcg_sum = 0.0
    first_scores_sum = 0.0
    first_score_count = 0
    positive_total = 0
    negative_total = 0
    negative_passed = 0
    category_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "positive": 0, "hits": 0, "negative": 0, "negative_passed": 0}
    )
    per_case: list[dict[str, Any]] = []

    for case in cases:
        results = engine.search(case.query, project_id=case.project_id, limit=case.k)
        contents = [r.memory.content for r in results]
        scores = [r.final_score for r in results]

        expected_terms = case.expected_any or ((case.expected_contains,) if case.expected_contains else ())
        expected_hits = []
        for idx, content in enumerate(contents):
            if expected_terms and any(term in content for term in expected_terms):
                expected_hits.append(idx)

        forbidden = bool(case.forbidden_contains and any(case.forbidden_contains in c for c in contents))
        stats = category_stats[case.category]
        stats["total"] += 1

        if expected_terms:
            positive_total += 1
            stats["positive"] += 1
            score_ok = not case.min_score or bool(scores and scores[0] >= case.min_score)
            if expected_hits and score_ok:
                recall_hits += 1
                stats["hits"] += 1
                mrr_sum += 1.0 / (expected_hits[0] + 1)
                ndcg_sum += 1.0 / math.log2(expected_hits[0] + 2)
            else:
                expected_label = " | ".join(expected_terms)
                failures.append({
                    "id": case.id, "reason": f"expected one of '{expected_label}' not in top {case.k}",
                    "results": " | ".join(contents[:5]),
                })
        else:
            negative_total += 1
            stats["negative"] += 1
            negative_ok = not results if not case.forbidden_contains else not forbidden
            if negative_ok:
                negative_passed += 1
                stats["negative_passed"] += 1

        if case.forbidden_contains:
            forbidden_total += 1
        if forbidden:
            forbidden_hits += 1
            if not any(f["id"] == case.id for f in failures):
                failures.append({
                    "id": case.id, "reason": f"forbidden '{case.forbidden_contains}' appeared",
                    "results": " | ".join(contents[:5]),
                })

        if expected_terms:
            precision_sum += len(expected_hits) / case.k if case.k else 0.0

        if scores:
            first_scores_sum += scores[0]
            first_score_count += 1

        per_case.append({
            "id": case.id,
            "query": case.query,
            "category": case.category,
            "recall": 1 if expected_hits else 0,
            "first_rank": expected_hits[0] + 1 if expected_hits else None,
            "top_score": round(scores[0], 4) if scores else 0,
            "num_results": len(results),
        })

    total = len(cases)
    ci_low, ci_high = _wilson_interval(recall_hits, positive_total)
    category_metrics: dict[str, dict[str, float | int]] = {}
    for category, stats in sorted(category_stats.items()):
        positives = stats["positive"]
        negatives = stats["negative"]
        category_metrics[category] = {
            "total": stats["total"],
            "positive_total": positives,
            "recall_at_k": stats["hits"] / positives if positives else 0.0,
            "negative_total": negatives,
            "no_answer_accuracy": stats["negative_passed"] / negatives if negatives else 0.0,
        }
    return RetrievalEvalResult(
        total=total,
        positive_total=positive_total,
        negative_total=negative_total,
        recall_at_k=recall_hits / positive_total if positive_total else 0.0,
        precision_at_k=precision_sum / positive_total if positive_total else 0.0,
        mrr=mrr_sum / positive_total if positive_total else 0.0,
        ndcg=ndcg_sum / positive_total if positive_total else 0.0,
        forbidden_hit_rate=forbidden_hits / forbidden_total if forbidden_total else 0.0,
        avg_first_score=first_scores_sum / first_score_count if first_score_count else 0.0,
        no_answer_accuracy=negative_passed / negative_total if negative_total else 0.0,
        recall_ci95=(ci_low, ci_high),
        category_metrics=category_metrics,
        failures=failures,
        per_case=per_case,
    )


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Return the Wilson confidence interval for a binomial proportion."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = (p + z2 / (2.0 * total)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def seed_retrieval_fixture(engine: BrainMemoryEngine) -> None:
    """种子数据：构建一个包含多种类型记忆的数据库。"""
    # 项目依赖 + supersede 链
    old = engine.add_memory("项目依赖管理使用 pnpm install。", project_id="demo", tags="依赖,pnpm")
    engine.apply_operation(MemoryOp.SUPERSEDE, target_id=old.id,
                           content="项目依赖管理已改用 bun install。", project_id="demo", tags="依赖,bun")

    # 偏好
    pref = engine.add_memory("用户偏好：回答技术问题时先给结论，再给必要步骤。", project_id="demo", tags="偏好,回答风格")
    engine.add_memory("回答尽量简洁，不要展开无关的细节。", project_id="demo", tags="偏好,简洁")

    # 项目事实
    engine.add_memory("OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。", project_id="openclaw-demo", tags="向量,后端")
    engine.add_memory("部署步骤：先运行 pnpm build，然后 docker compose up -d。", project_id="demo", tags="部署,流程")
    engine.add_memory("前端使用 React 18 + TypeScript，状态管理用 Zustand。", project_id="demo", tags="前端,技术栈")

    # 临时 + 敏感
    engine.add_memory("今天临时使用 test@example.com 做一次登录测试。", project_id="demo", tags="临时")
    engine.add_memory("API key = sk-abc123def456", project_id="demo", tags="密钥")

    # 强化偏好（模拟多次使用）
    for _ in range(3):
        engine.reinforce_used(pref.id or 0)


def evaluate_retrieval_fixture(db_path: str | Path, fixture_path: str | Path) -> RetrievalEvalResult:
    engine = BrainMemoryEngine(db_path)
    try:
        # Keep repeated benchmark runs independent even when the CLI already has
        # the database open. Deleting rows works across SQLite connections,
        # whereas unlinking an open database fails on Windows.
        engine.store.conn.execute("DELETE FROM memory_feedback_events")
        engine.store.conn.execute("DELETE FROM memory_links")
        engine.store.conn.execute("DELETE FROM memories_fts")
        engine.store.conn.execute("DELETE FROM memories")
        engine.store.conn.commit()
        seed_retrieval_fixture(engine)
        return evaluate_retrieval_full(engine, load_retrieval_cases(fixture_path))
    finally:
        engine.close()


# ═══════════════════════════════════════════════════════════════════
# 3. 端到端评测
# ═══════════════════════════════════════════════════════════════════

def evaluate_end_to_end_case(case: EndToEndCase, db_path: str | Path) -> tuple[bool, bool, bool, int, str]:
    db_path = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    engine = BrainMemoryEngine(db_path)
    try:
        def generator(payload: dict[str, Any]) -> dict[str, Any]:
            text = str(payload["user_input"])
            # 启发式规则模拟 LLM
            if not text.strip():
                return {"rationale": "empty", "writes": [{"op": "NOOP"}]}
            if any(kw in text for kw in ["临时", "天气", "你好", "早上好", "困"]):
                return {"rationale": "temporary", "writes": [{"op": "NOOP"}]}
            if "纠正" in text or "改用" in text or "改回" in text:
                return {"rationale": "correction", "writes": [{"op": "SUPERSEDE", "target_id": 1, "content": text, "summary": text[:120], "tags": ""}]}
            write: dict[str, Any] = {"op": "ADD", "content": text, "summary": text[:120], "tags": ""}
            if "偏好" in text or "简洁" in text:
                write["tags"] = "偏好"
            if "步骤" in text or "部署" in text:
                write["tags"] = "部署,流程"
            return {"rationale": "mock e2e", "writes": [write]}

        adapter = BrainMemoryAdapter(engine, extractor=JSONMemoryExtractor(generator))
        hook = PiAgentMemoryHook(adapter)
        state: dict[str, Any] = {"user_id": "eval-user", "project_id": case.project_id}

        for item in case.history:
            state = hook.before_agent_start(item, state)
            state = hook.agent_end(item, "已处理。", state)

        state = hook.before_agent_start(case.query, state)
        context = str(state.get("brainmemory_memory_context", ""))
        passed = case.expected_contains in context
        if case.forbidden_contains and case.forbidden_contains in context:
            passed = False
        polluted = "test@example.com" in context
        stale = bool(case.forbidden_contains and case.forbidden_contains in context)
        return passed, polluted, stale, len(context), context
    finally:
        engine.close()


def evaluate_end_to_end_fixture(work_dir: str | Path, fixture_path: str | Path) -> E2EResult:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []
    pollution = 0
    stale = 0
    passed_count = 0
    total_chars = 0
    cases = load_end_to_end_cases(fixture_path)

    for case in cases:
        passed, polluted, stale_hit, chars, context = evaluate_end_to_end_case(case, work / f"{case.id}.db")
        passed_count += int(passed)
        pollution += int(polluted)
        stale += int(stale_hit)
        total_chars += chars
        if not passed:
            failures.append({
                "id": case.id,
                "reason": "expected content not found in memory context",
                "context": context[:300],
            })

    total = len(cases)
    return E2EResult(
        total=total, passed=passed_count,
        memory_pollution_rate=pollution / total if total else 0.0,
        stale_reference_rate=stale / total if total else 0.0,
        avg_context_chars=total_chars / total if total else 0.0,
        failures=failures,
    )


# ═══════════════════════════════════════════════════════════════════
# 4. 强度模型评测
# ═══════════════════════════════════════════════════════════════════

from .strength import current_strength, reinforce, DECAY_RATE, REINFORCEMENT_GAIN


def evaluate_strength_model(cases: list[StrengthCase]) -> StrengthEvalResult:
    """验证强度模型的数学正确性。

    对每种测试类型，创建模拟 Memory 并验证：
    - 衰减：elapsed_days 后的 current_strength 是否在预期范围
    - 强化：reinforce 后的值是否在预期范围
    """
    failures = []
    decay_ok = 0
    reinforce_ok = 0
    threshold_ok = 0
    passed = 0

    for case in cases:
        now = utc_now()
        memory = Memory(
            id=1, content="test", summary="test",
            strength=case.initial_strength,
            last_accessed_at=now - timedelta(days=case.days_elapsed) if case.days_elapsed > 0 else now,
            access_count=case.access_count,
        )

        if case.type == "decay":
            cs = current_strength(memory, now)
            lo, hi = case.expected_range
            if lo <= cs <= hi:
                decay_ok += 1
                passed += 1
            else:
                failures.append({
                    "id": case.id,
                    "reason": f"decay: expected [{lo:.4f}, {hi:.4f}], got {cs:.4f}",
                    "detail": f"strength={case.initial_strength} days={case.days_elapsed} decay_rate={DECAY_RATE}",
                })

        elif case.type == "reinforce":
            # reinforce(memory) 内部会先计算 current_strength，再做强化。
            cs = current_strength(memory, now)
            reinforced = reinforce(memory)
            lo, hi = case.expected_range
            if lo <= reinforced <= hi:
                reinforce_ok += 1
                passed += 1
            else:
                failures.append({
                    "id": case.id,
                    "reason": f"reinforce: expected [{lo:.4f}, {hi:.4f}], got {reinforced:.4f}",
                    "detail": f"pre-strength={cs:.4f} gain={REINFORCEMENT_GAIN}",
                })

    return StrengthEvalResult(
        total=len(cases), passed=passed,
        decay_ok=decay_ok, reinforce_ok=reinforce_ok, threshold_ok=threshold_ok,
        failures=failures,
    )


def evaluate_strength_fixture(fixture_path: str | Path) -> StrengthEvalResult:
    return evaluate_strength_model(load_strength_cases(fixture_path))


# ═══════════════════════════════════════════════════════════════════
# 5. 语义嵌入质量评测
# ═══════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class EmbeddingQualityResult:
    synonym_recall: float = 0.0  # 同义词召回率
    paraphrase_recall: float = 0.0  # 改写召回率
    cross_lang_recall: float = 0.0  # 跨语言召回率
    avg_similarity: float = 0.0  # 平均语义相似度
    details: dict[str, Any] = field(default_factory=dict)


def evaluate_embedding_quality(engine: BrainMemoryEngine) -> EmbeddingQualityResult:
    """评估嵌入模型的质量。

    测试三组语义关系：
    1. 同义词：add → store, insert → 应该召回同样的记忆
    2. 改写：不同表述 → 应该召回同样的记忆
    3. 跨语言：中文查询 → 英文记忆，反之亦然
    """
    backend = engine.store.embedding

    # 清除旧数据
    engine.store.conn.execute("DELETE FROM memories")
    engine.store.conn.execute("DELETE FROM memories_fts")
    engine.store.conn.commit()
    engine.store.init_schema()

    # 种子数据
    memories = {
        "dep_bun": engine.add_memory("使用 bun install 安装项目依赖。", tags="依赖,构建"),
        "style_concise": engine.add_memory("回答简洁，先给结论再展开。", tags="偏好,风格"),
        "deploy_docker": engine.add_memory("Deploy with Docker Compose on port 8080.", tags="deploy,docker"),
    }

    # 1. 同义词测试
    synonym_pairs = [
        ("安装依赖用什么", "dep_bun"),
        ("怎么装包", "dep_bun"),
        ("依赖管理", "dep_bun"),
    ]
    synonym_hits = 0
    for query, expected_key in synonym_pairs:
        results = engine.search(query, limit=3)
        contents = [r.memory.content for r in results]
        if any(memories[expected_key].content in c for c in contents):
            synonym_hits += 1

    # 2. 改写测试
    paraphrase_pairs = [
        ("回答问题的方式有什么要求", "style_concise"),
        ("回复应该怎么做", "style_concise"),
        ("怎么回答比较好", "style_concise"),
    ]
    paraphrase_hits = 0
    for query, expected_key in paraphrase_pairs:
        results = engine.search(query, limit=3)
        contents = [r.memory.content for r in results]
        if any(memories[expected_key].content in c for c in contents):
            paraphrase_hits += 1

    # 3. 跨语言测试
    cross_lang_pairs = [
        ("如何部署服务", "deploy_docker"),
        ("container deployment", "deploy_docker"),
        ("Docker port", "deploy_docker"),
    ]
    cross_hits = 0
    similarities = []
    for query, expected_key in cross_lang_pairs:
        results = engine.search(query, limit=3)
        contents = [r.memory.content for r in results]
        if any(memories[expected_key].content in c for c in contents):
            cross_hits += 1
        if results:
            similarities.append(results[0].semantic_similarity)

    n_syn = len(synonym_pairs)
    n_para = len(paraphrase_pairs)
    n_cross = len(cross_lang_pairs)

    return EmbeddingQualityResult(
        synonym_recall=synonym_hits / n_syn if n_syn else 0,
        paraphrase_recall=paraphrase_hits / n_para if n_para else 0,
        cross_lang_recall=cross_hits / n_cross if n_cross else 0,
        avg_similarity=sum(similarities) / len(similarities) if similarities else 0,
        details={
            "backend": backend.name,
            "synonym_pairs": n_syn,
            "synonym_hits": synonym_hits,
            "paraphrase_pairs": n_para,
            "paraphrase_hits": paraphrase_hits,
            "cross_lang_pairs": n_cross,
            "cross_lang_hits": cross_hits,
        },
    )


# ═══════════════════════════════════════════════════════════════════
# 6. 综合评测报告
# ═══════════════════════════════════════════════════════════════════

def run_full_evaluation(db_path: str | Path, work_dir: str | Path) -> dict[str, Any]:
    """运行完整评测套件并返回综合报告。"""
    report: dict[str, Any] = {}

    # 1. 抽取评测
    ext_result = evaluate_mock_llm_fixture(Path("eval/extraction_cases.jsonl"))
    report["extraction"] = {
        "total": ext_result.total,
        "accuracy": round(ext_result.accuracy, 4),
        "failures": len(ext_result.failures),
        "details": ext_result.metadata,
    }

    # 2. 检索评测
    ret_result = evaluate_retrieval_fixture(db_path, Path("eval/retrieval_cases.jsonl"))
    report["retrieval"] = {
        "total": ret_result.total,
        "positive_total": ret_result.positive_total,
        "negative_total": ret_result.negative_total,
        "recall_at_k": round(ret_result.recall_at_k, 4),
        "precision_at_k": round(ret_result.precision_at_k, 4),
        "mrr": round(ret_result.mrr, 4),
        "ndcg": round(ret_result.ndcg, 4),
        "forbidden_hit_rate": round(ret_result.forbidden_hit_rate, 4),
        "avg_first_score": round(ret_result.avg_first_score, 4),
        "no_answer_accuracy": round(ret_result.no_answer_accuracy, 4),
        "recall_ci95": [round(value, 4) for value in ret_result.recall_ci95],
        "category_metrics": ret_result.category_metrics,
        "failures": len(ret_result.failures),
    }

    # 3. 端到端评测
    e2e_result = evaluate_end_to_end_fixture(work_dir, Path("eval/e2e_cases.jsonl"))
    report["e2e"] = {
        "total": e2e_result.total,
        "accuracy": round(e2e_result.accuracy, 4),
        "pollution_rate": round(e2e_result.memory_pollution_rate, 4),
        "stale_rate": round(e2e_result.stale_reference_rate, 4),
        "avg_context_chars": round(e2e_result.avg_context_chars, 1),
        "failures": len(e2e_result.failures),
    }

    # 4. 强度模型评测
    str_result = evaluate_strength_fixture(Path("eval/strength_cases.jsonl"))
    report["strength"] = {
        "total": str_result.total,
        "accuracy": round(str_result.accuracy, 4),
        "decay_ok": str_result.decay_ok,
        "reinforce_ok": str_result.reinforce_ok,
        "threshold_ok": str_result.threshold_ok,
        "failures": str_result.failures,
    }

    # 5. 嵌入质量评测
    engine = BrainMemoryEngine(db_path)
    try:
        emb_result = evaluate_embedding_quality(engine)
        report["embedding"] = {
            "backend": emb_result.details["backend"],
            "synonym_recall": round(emb_result.synonym_recall, 4),
            "paraphrase_recall": round(emb_result.paraphrase_recall, 4),
            "cross_lang_recall": round(emb_result.cross_lang_recall, 4),
            "avg_similarity": round(emb_result.avg_similarity, 4),
        }
    finally:
        engine.close()

    return report
