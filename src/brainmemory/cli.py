"""
MB 记忆系统 — 命令行接口
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .engine import BrainMemoryEngine
from .embedding import embedding_config_from_env
from .evaluation import (
    evaluate_end_to_end_fixture, evaluate_retrieval_fixture, evaluate_mock_llm_fixture,
    evaluate_strength_fixture, run_full_evaluation, evaluate_embedding_quality,
)
from .extractor import DeepSeekMemoryExtractor, LLMExtractorNotConfigured, build_default_extractor
from .models import MemoryOp
from .retrieval import RetrievalMode
from .server import run_server
from .strength import current_strength


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="类脑记忆 — continuous strength memory")
    parser.add_argument("--db", default=os.environ.get("BRAINMEMORY_DB", "brainmemory.db"), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a memory")
    add_p.add_argument("content")
    add_p.add_argument("--project")
    add_p.add_argument("--summary", default="")
    add_p.add_argument("--tags", default="")

    up_p = sub.add_parser("update", help="Update a memory")
    up_p.add_argument("target_id", type=int)
    up_p.add_argument("content")
    up_p.add_argument("--summary", default="")

    super_p = sub.add_parser("supersede", help="Replace outdated memory")
    super_p.add_argument("target_id", type=int)
    super_p.add_argument("content")
    super_p.add_argument("--summary", default="")

    search_p = sub.add_parser("search", help="Search memories")
    search_p.add_argument("query")
    search_p.add_argument("--project")
    search_p.add_argument("--limit", type=int, default=5)

    ext_p = sub.add_parser("extract", help="Extract memory writes from text")
    ext_p.add_argument("text")
    ext_p.add_argument("--project")
    ext_p.add_argument("--commit", action="store_true")

    eval_p = sub.add_parser("eval-extractor", help="Evaluate extractor")
    eval_p.add_argument("--fixture", default="eval/extraction_cases.jsonl")

    eval_r = sub.add_parser("eval-retrieval", help="Evaluate retrieval")
    eval_r.add_argument("--fixture", default="eval/retrieval_cases.jsonl")

    eval_e2e = sub.add_parser("eval-e2e", help="Evaluate end-to-end")
    eval_e2e.add_argument("--fixture", default="eval/e2e_cases.jsonl")
    eval_e2e.add_argument("--work-dir", default=".csm_eval")

    serve_p = sub.add_parser("serve", help="Run HTTP sidecar")
    serve_p.add_argument("--host", default=os.environ.get("BRAINMEMORY_HOST", "127.0.0.1"))
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("BRAINMEMORY_PORT", "8765")))
    serve_p.add_argument("--api-key", default=os.environ.get("BRAINMEMORY_API_KEY"))

    ds_p = sub.add_parser("deepseek-check", help="Validate DeepSeek request locally")
    ds_p.add_argument("text")
    ds_p.add_argument("--project")

    probe_p = sub.add_parser("deepseek-probe", help="Test DeepSeek connectivity")
    probe_p.add_argument("--confirm-spend", action="store_true", help="Actually call the API")

    sub.add_parser("sleep", help="Run sleep consolidation")
    sub.add_parser("health", help="Memory health report")
    sub.add_parser("embedding-info", help="Show embedding backend")
    sub.add_parser("reindex-embeddings", help="Rebuild embeddings")
    uninstall_p = sub.add_parser("uninstall", help="Completely uninstall BrainMemory")
    uninstall_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    sub.add_parser("demo", help="Create and query demo memories")

    eval_all = sub.add_parser("eval-all", help="Run full evaluation suite")
    eval_all.add_argument("--work-dir", default=".csm_eval")

    eval_str = sub.add_parser("eval-strength", help="Evaluate strength model")
    eval_str.add_argument("--fixture", default="eval/strength_cases.jsonl")

    eval_emb = sub.add_parser("eval-embedding", help="Evaluate embedding quality")
    return parser


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _pip_uninstall_brainmemory() -> None:
    """卸载 brainmemory Python 包，兼容 pip 安装和 editable 安装。"""
    import shutil
    import site
    import subprocess
    import sys
    import sysconfig

    # 先尝试常规 pip uninstall
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "brainmemory", "-y"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("[OK] pip uninstall brainmemory 完成")
        return

    # pip uninstall 失败，手动清理残留（editable 安装等）
    print("[..] 常规卸载失败，深度清理残留...")
    cleaned = False

    for sp in site.getsitepackages():
        sp = Path(sp)
        if not sp.exists():
            continue

        # 清理 brainmemory 包目录
        pkg = sp / "brainmemory"
        if pkg.is_dir():
            shutil.rmtree(pkg, ignore_errors=True)
            print(f"  [OK] 已删除 {pkg}")
            cleaned = True

        # 清理 dist-info / egg-info
        for pattern in ["brainmemory-*.dist-info", "brainmemory-*.egg-info"]:
            for d in sp.glob(pattern):
                shutil.rmtree(d, ignore_errors=True)
                print(f"  [OK] 已删除 {d}")
                cleaned = True

        # 清理 editable 安装的 .pth 文件
        for pth in sp.glob("__editable__.brainmemory-*.pth"):
            pth.unlink(missing_ok=True)
            print(f"  [OK] 已删除 {pth}")
            cleaned = True

        # 清理 .egg-link（旧版 editable）
        egg = sp / "brainmemory.egg-link"
        if egg.exists():
            egg.unlink()
            print(f"  [OK] 已删除 {egg}")
            cleaned = True

    # 清理 Scripts 中的 brainmemory.exe
    scripts = Path(sysconfig.get_path("scripts"))
    for exe in scripts.glob("brainmemory*"):
        if exe.is_file():
            exe.unlink(missing_ok=True)
            print(f"  [OK] 已删除 {exe}")
            cleaned = True

    if cleaned:
        print("[OK] 所有残留已清理")
    else:
        print("[--] 未找到残留，可能已卸载")


def run_uninstall(db_path: str) -> None:
    """完全卸载 BrainMemory：杀 sidecar、删数据库、删扩展。"""
    import subprocess
    import sys

    db = Path(db_path)
    home = Path.home()
    ext = home / ".pi" / "agent" / "extensions" / "brainmemory.ts"

    print("=== BrainMemory 卸载 ===\n")

    # 1) 杀 sidecar 进程
    killed = False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "python.exe"],
                capture_output=True, text=True,
            )
            # 只报告，不强制要求成功（可能没有 sidecar 在跑）
            if result.returncode == 0:
                killed = True
                print("[OK] sidecar 进程已终止")
            else:
                print("[--] 未找到 sidecar 进程")
        else:
            result = subprocess.run(
                ["pkill", "-f", "brainmemory.cli serve"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                killed = True
                print("[OK] sidecar 进程已终止")
            else:
                print("[--] 未找到 sidecar 进程")
    except Exception as e:
        print(f"[!!] 杀进程失败: {e}")

    # 2) 删数据库文件（含 WAL/SHM）
    for suffix in ["", "-wal", "-shm"]:
        f = Path(str(db) + suffix)
        if f.exists():
            f.unlink()
            print(f"[OK] 已删除 {f}")
    if not any((Path(str(db) + s)).exists() for s in ["", "-wal", "-shm"]):
        if not db.exists():
            print("[--] 数据库文件不存在（可能已删除）")

    # 3) 删 pi 扩展文件
    if ext.exists():
        ext.unlink()
        print(f"[OK] 已删除扩展 {ext}")
    else:
        print(f"[--] 扩展文件不存在 {ext}")

    # 4) 卸载 Python 包（处理 pip 安装和 editable 安装）
    print(f"\n[..] 卸载 Python 包...")
    _pip_uninstall_brainmemory()
    print(f"\n=== 卸载完成 ===")


def run_demo(db_path: str) -> None:
    path = Path(db_path)
    if path.exists():
        path.unlink()
    engine = BrainMemoryEngine(path)
    try:
        style = engine.add_memory(
            "用户长期偏好：解释技术概念时使用简洁、直接的中文回答。",
            project_id="csm",
            tags="偏好,回答风格",
        )
        package = engine.add_memory(
            "CSM 项目最初使用 pnpm 管理前端依赖。",
            project_id="csm",
            tags="项目,技术栈,依赖",
        )
        engine.apply_operation(
            MemoryOp.SUPERSEDE, target_id=package.id,
            content="CSM 项目依赖管理已改为 bun，应优先使用 bun install。",
            project_id="csm",
        )
        engine.add_memory(
            "今天临时使用 test@example.com 做一次登录测试，不应作为长期默认邮箱。",
            project_id="csm",
            tags="临时信息",
        )
        engine.reinforce_used(style.id or 0)
        results = engine.search("安装依赖应该用什么命令？", project_id="csm")
        print("Demo search results:")
        for result in results:
            print(f"- #{result.memory.id} R={result.current_strength:.3f}: {result.memory.content}")
        print("\nHealth report:")
        print_json(engine.health_report())
    finally:
        engine.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "uninstall":
        if not args.yes:
            confirm = input("⚠️ 确认完全卸载 BrainMemory？此操作不可撤销！[y/N] ")
            if confirm.strip().lower() != "y":
                print("已取消。")
                return
        run_uninstall(args.db)
        return
    if args.command == "demo":
        run_demo(args.db)
        return
    if args.command == "serve":
        run_server(args.db, host=args.host, port=args.port, api_key=args.api_key)
        return
    if args.command == "embedding-info":
        print_json(embedding_config_from_env())
        return

    engine = BrainMemoryEngine(args.db)
    try:
        if args.command == "add":
            memory = engine.add_memory(args.content, project_id=args.project, summary=args.summary, tags=args.tags)
            print_json({"id": memory.id, "content": memory.content})
        elif args.command == "update":
            memory = engine.apply_operation(MemoryOp.UPDATE, target_id=args.target_id, content=args.content, summary=args.summary)
            print_json({"id": memory.id if memory else None})
        elif args.command == "supersede":
            memory = engine.apply_operation(MemoryOp.SUPERSEDE, target_id=args.target_id, content=args.content, summary=args.summary)
            print_json({"id": memory.id if memory else None})
        elif args.command == "search":
            print_json([{
                "id": r.memory.id, "score": round(r.final_score, 4),
                "strength": round(r.current_strength, 4),
                "strength": round(r.current_strength, 4),
                "content": r.memory.content,
            } for r in engine.search(args.query, project_id=args.project, limit=args.limit)])
        elif args.command == "extract":
            extractor = build_default_extractor()
            retrieved = [{
                "id": r.memory.id, "content": r.memory.content,
                "summary": r.memory.summary, "tags": r.memory.tags,
                "status": r.memory.status.value,
            } for r in engine.search(args.text, project_id=args.project, limit=5, mode=RetrievalMode.WRITE_ARBITRATION) if r.memory.id]
            plan = extractor.extract(user_input=args.text, project_id=args.project, retrieved_memories=retrieved)
            committed = []
            if args.commit:
                for write in plan.writes:
                    result = engine.apply_operation(write.op, content=write.content, target_id=write.target_id,
                        project_id=args.project, summary=write.summary, tags=write.tags)
                    if result is not None:
                        committed.append(result.id)
            print_json({"rationale": plan.rationale, "writes": [{"op": w.op.value, "target_id": w.target_id, "content": w.content, "summary": w.summary, "tags": w.tags} for w in plan.writes], "committed_ids": committed})
        elif args.command == "eval-extractor":
            result = evaluate_mock_llm_fixture(args.fixture)
            print_json({"total": result.total, "passed": result.passed, "accuracy": round(result.accuracy, 4), "failures": result.failures})
        elif args.command == "deepseek-check":
            try:
                extractor = DeepSeekMemoryExtractor.from_env()
                print_json(extractor.dry_run_request(args.text, project_id=args.project))
            except LLMExtractorNotConfigured as exc:
                print_json({"ok": False, "will_call_api": False, "reason": str(exc)})
        elif args.command == "deepseek-probe":
            try:
                extractor = DeepSeekMemoryExtractor.from_env()
            except LLMExtractorNotConfigured as exc:
                print_json({"ok": False, "reason": str(exc)})
                return
            if not args.confirm_spend:
                print_json({"ok": True, "will_call_api": False, "reason": "Add --confirm-spend", "request": extractor.probe_request()})
                return
            print_json({"ok": True, "will_call_api": True, "result": extractor.live_probe()})
        elif args.command == "eval-retrieval":
            result = evaluate_retrieval_fixture(args.db, args.fixture)
            print_json({
                "total": result.total, "recall_at_k": round(result.recall_at_k, 4),
                "precision_at_k": round(result.precision_at_k, 4),
                "mrr": round(result.mrr, 4), "ndcg": round(result.ndcg, 4),
                "forbidden_hit_rate": round(result.forbidden_hit_rate, 4),
                "avg_first_score": round(result.avg_first_score, 4),
                "failures": result.failures,
            })
        elif args.command == "eval-e2e":
            result = evaluate_end_to_end_fixture(args.work_dir, args.fixture)
            print_json({
                "total": result.total, "passed": result.passed,
                "accuracy": round(result.accuracy, 4),
                "memory_pollution_rate": round(result.memory_pollution_rate, 4),
                "stale_reference_rate": round(result.stale_reference_rate, 4),
                "avg_context_chars": round(result.avg_context_chars, 1),
                "failures": result.failures,
            })
        elif args.command == "eval-all":
            report = run_full_evaluation(args.db, args.work_dir)
            print_json(report)
        elif args.command == "eval-strength":
            result = evaluate_strength_fixture(args.fixture)
            print_json({
                "total": result.total, "accuracy": round(result.accuracy, 4),
                "decay_ok": result.decay_ok, "reinforce_ok": result.reinforce_ok,
                "threshold_ok": result.threshold_ok, "failures": result.failures,
            })
        elif args.command == "eval-embedding":
            result = evaluate_embedding_quality(engine)
            print_json({
                "backend": result.details["backend"],
                "synonym_recall": round(result.synonym_recall, 4),
                "paraphrase_recall": round(result.paraphrase_recall, 4),
                "cross_lang_recall": round(result.cross_lang_recall, 4),
                "avg_similarity": round(result.avg_similarity, 4),
                "details": result.details,
            })
        elif args.command == "sleep":
            print_json(engine.sleep_consolidate())
        elif args.command == "health":
            print_json(engine.health_report())
        elif args.command == "reindex-embeddings":
            print_json(engine.reindex_embeddings())
    finally:
        engine.close()


if __name__ == "__main__":
    main()
