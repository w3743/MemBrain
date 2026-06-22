from pathlib import Path

from csm_agent.engine import CSMEngine
from csm_agent.evaluation import (
    evaluate_end_to_end_fixture, evaluate_mock_llm_fixture, evaluate_retrieval_fixture,
    evaluate_strength_fixture, evaluate_embedding_quality, run_full_evaluation,
    load_extraction_cases, load_retrieval_cases, load_end_to_end_cases, load_strength_cases,
)


def test_load_extraction_cases() -> None:
    cases = load_extraction_cases(Path("eval/extraction_cases.jsonl"))
    assert len(cases) >= 30  # 从 5 扩展到 30+
    assert cases[0].id == "identity_name"


def test_load_retrieval_cases() -> None:
    cases = load_retrieval_cases(Path("eval/retrieval_cases.jsonl"))
    assert len(cases) >= 18  # 从 3 扩展到 18+
    assert cases[0].expected_contains == "bun install"


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
    assert "threshold" in types


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
    engine = CSMEngine(tmp_path / "emb.db")
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
