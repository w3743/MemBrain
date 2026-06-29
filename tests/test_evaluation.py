from pathlib import Path
from types import SimpleNamespace

from brainmemory.engine import BrainMemoryEngine
from brainmemory.evaluation import (
    evaluate_end_to_end_fixture, evaluate_mock_llm_fixture, evaluate_retrieval_fixture,
    evaluate_strength_fixture, evaluate_embedding_quality, run_full_evaluation,
    load_extraction_cases, load_retrieval_cases, load_end_to_end_cases, load_strength_cases,
    evaluate_retrieval_full,
)
from brainmemory.evaluation import RetrievalCase


def test_load_extraction_cases() -> None:
    cases = load_extraction_cases(Path("eval/extraction_cases.jsonl"))
    assert len(cases) >= 30  # 从 5 扩展到 30+
    assert cases[0].id == "identity_name"


def test_load_retrieval_cases() -> None:
    cases = load_retrieval_cases(Path("eval/retrieval_cases.jsonl"))
    assert len(cases) >= 40
    assert cases[0].expected_contains == "bun install"
    assert any(len(case.expected_any) > 1 for case in cases)
    assert len({case.category for case in cases}) >= 6


def test_retrieval_metrics_separate_positive_and_negative_cases() -> None:
    class FakeEngine:
        def search(self, query, project_id=None, limit=3):
            if query == "hit":
                return [SimpleNamespace(
                    memory=SimpleNamespace(content="use bun install"),
                    final_score=0.8,
                )]
            if query == "forbidden":
                return [SimpleNamespace(
                    memory=SimpleNamespace(content="old pnpm install"),
                    final_score=0.7,
                )]
            return []

    cases = [
        RetrievalCase("positive-hit", "hit", None, "bun", expected_any=("bun",)),
        RetrievalCase("positive-miss", "miss", None, "sqlite", expected_any=("sqlite",)),
        RetrievalCase("negative-pass", "empty", None, ""),
        RetrievalCase("negative-fail", "forbidden", None, "", forbidden_contains="pnpm"),
    ]
    result = evaluate_retrieval_full(FakeEngine(), cases)

    assert result.total == 4
    assert result.positive_total == 2
    assert result.negative_total == 2
    assert result.recall_at_k == 0.5
    assert result.mrr == 0.5
    assert result.no_answer_accuracy == 0.5
    assert result.forbidden_hit_rate == 1.0
    assert result.recall_ci95[0] < result.recall_at_k < result.recall_ci95[1]


def test_retrieval_expected_any_accepts_equivalent_labels() -> None:
    class FakeEngine:
        def search(self, query, project_id=None, limit=3):
            return [SimpleNamespace(
                memory=SimpleNamespace(content="package manager is bun"),
                final_score=0.8,
            )]

    case = RetrievalCase(
        "equivalent", "manager", None, "bun install",
        expected_any=("bun install", "package manager is bun"),
        category="paraphrase",
    )
    result = evaluate_retrieval_full(FakeEngine(), [case])
    assert result.recall_at_k == 1.0
    assert result.category_metrics["paraphrase"]["recall_at_k"] == 1.0


def test_load_end_to_end_cases() -> None:
    cases = load_end_to_end_cases(Path("eval/e2e_cases.jsonl"))
    assert len(cases) >= 10  # 从 3 扩展到 10+
    assert cases[0].history


def test_load_strength_cases() -> None:
    cases = load_strength_cases(Path("eval/strength_cases.jsonl"))
    assert len(cases) >= 12
    types = {c.type for c in cases}
    assert "decay" in types
    assert "reinforce" in types
    assert types == {"decay", "reinforce"}


def test_mock_llm_extractor_fixture_eval(tmp_path) -> None:
    result = evaluate_mock_llm_fixture(Path("eval/extraction_cases.jsonl"))
    assert result.total >= 30
    assert result.accuracy >= 0.9
    assert len(result.failures) <= result.total * 0.1  # 最多 10% 失败


def test_retrieval_fixture_eval(tmp_path) -> None:
    result = evaluate_retrieval_fixture(tmp_path / "retrieval.db", Path("eval/retrieval_cases.jsonl"))
    assert result.total >= 18
    assert result.recall_at_k >= 0.6
    assert result.forbidden_hit_rate <= 0.2
    assert result.mrr >= 0.0
    assert result.ndcg >= 0.0


def test_end_to_end_fixture_eval(tmp_path) -> None:
    result = evaluate_end_to_end_fixture(tmp_path, Path("eval/e2e_cases.jsonl"))
    assert result.total >= 10
    assert result.accuracy >= 0.8
    assert result.memory_pollution_rate <= 0.3
    assert result.stale_reference_rate <= 0.3


def test_strength_model_eval() -> None:
    result = evaluate_strength_fixture(Path("eval/strength_cases.jsonl"))
    assert result.total >= 12
    assert result.accuracy >= 0.9
    assert result.decay_ok >= 8
    assert result.reinforce_ok >= 2


def test_embedding_quality_eval(tmp_path) -> None:
    engine = BrainMemoryEngine(tmp_path / "emb.db")
    try:
        result = evaluate_embedding_quality(engine)
        assert result.details["backend"] == "local_bge_large_zh"
        assert result.synonym_recall >= 0.0
        assert result.paraphrase_recall >= 0.0
    finally:
        engine.close()


def test_run_full_evaluation(tmp_path) -> None:
    db = tmp_path / "full_eval.db"
    report = run_full_evaluation(db, tmp_path / "e2e_work")
    assert "extraction" in report
    assert "retrieval" in report
    assert "e2e" in report
    assert "strength" in report
    assert "embedding" in report
    assert report["extraction"]["accuracy"] >= 0.8
    assert report["strength"]["accuracy"] >= 0.8
