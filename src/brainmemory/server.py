"""
HTTP Sidecar 服务 + 管理控制台 HTML
"""

from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .adapters import AgentScope, BrainMemoryAdapter, HermesMemoryProvider, OpenClawMemorySidecar
from .api_contract import openapi_spec
from .engine import BrainMemoryEngine
from .embedding import embedding_config_from_env
from .extractor import DeepSeekMemoryExtractor, LLMExtractorNotConfigured, build_default_extractor
from .llm_config import public_llm_config, save_llm_config
from .models import MemoryOp, MemoryStatus, MemoryWrite, MemoryWritePlan
from .retrieval import RetrievalMode
from .strength import current_strength


def create_handler(db_path: str | Path, api_key: str | None = None):
    """返回 (handler_class, cleanup_callable)。"""
    db = str(db_path)
    configured_api_key = api_key
    shared_engine = BrainMemoryEngine(db)

    class BrainMemoryRequestHandler(BaseHTTPRequestHandler):
        server_version = "BrainMemory/1.0"

        def do_GET(self) -> None:
            if self.path == "/" or self.path == "/admin":
                if not self._authorized():
                    self._send_json({"error": "unauthorized"}, status=401)
                    return
                self._send_html(admin_console_html(api_key=configured_api_key))
                return
            if self.path == "/health":
                self._send_json({"ok": True, "service": "brainmemory-sidecar"})
                return
            if self.path == "/admin/health":
                if not self._authorized():
                    self._send_json({"error": "unauthorized"}, status=401)
                    return
                self._send_json(self._admin_health())
                return
            if self.path == "/admin/llm/config":
                if not self._authorized():
                    self._send_json({"error": "unauthorized"}, status=401)
                    return
                self._send_json(public_llm_config())
                return
            if self.path == "/openapi.json":
                self._send_json(openapi_spec())
                return
            self._send_json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:
            try:
                if not self._authorized():
                    self._send_json({"error": "unauthorized"}, status=401)
                    return
                payload = self._read_json()
                response = self._dispatch_post(payload)
                self._send_json(response)
            except ValueError as exc:
                self._send_json({"error": "bad_request", "message": str(exc)}, status=400)
            except Exception as exc:
                self._send_json({"error": "internal_error", "message": str(exc)}, status=500)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _dispatch_post(self, payload: dict[str, Any]) -> dict[str, Any]:
            adapter = BrainMemoryAdapter(shared_engine)
            sidecar = OpenClawMemorySidecar(adapter)
            provider = HermesMemoryProvider(adapter)
            if self.path == "/pre_prompt":
                return sidecar.handle_pre_prompt(payload)
            if self.path == "/post_run":
                return sidecar.handle_post_run(payload)
            if self.path == "/remember":
                memory_id = provider.remember(
                    str(payload.get("content", "")),
                    user_id=str(payload.get("user_id", "default")),
                    project_id=payload.get("project_id") or payload.get("workspace_id"),
                )
                return {"memory_id": memory_id}
            if self.path == "/context":
                context = provider.get_context(
                    str(payload.get("prompt") or payload.get("message") or ""),
                    user_id=str(payload.get("user_id", "default")),
                    project_id=payload.get("project_id") or payload.get("workspace_id"),
                )
                return {"memory_context": context}
            if self.path == "/sleep":
                return provider.sleep()
            if self.path.startswith("/admin/"):
                return self._dispatch_admin(shared_engine, adapter, payload)
            raise ValueError(f"unknown endpoint: {self.path}")

        def _dispatch_admin(self, engine: BrainMemoryEngine, adapter: BrainMemoryAdapter, payload: dict[str, Any]) -> dict[str, Any]:
            if self.path == "/admin/memories":
                return {"items": [_memory_payload(m) for m in engine.store.list_all()]}
            if self.path == "/admin/llm/config":
                save_llm_config(payload)
                return public_llm_config()
            if self.path == "/admin/llm/probe":
                return DeepSeekMemoryExtractor.from_env().live_probe()
            if self.path == "/admin/memory/save":
                memory_id = payload.get("id")
                content = str(payload.get("content", "")).strip()
                if not content:
                    raise ValueError("content is required")
                project_id = payload.get("project_id") or None
                user_id = str(payload.get("user_id", "")).strip()
                summary = str(payload.get("summary", "")).strip()
                tags = str(payload.get("tags", "")).strip()
                if memory_id:
                    memory = engine.apply_operation(
                        MemoryOp.UPDATE, target_id=int(memory_id),
                        content=content, summary=summary,
                    )
                    if memory:
                        memory.project_id = project_id
                        memory.tags = tags
                        memory = engine.store.update(memory)
                else:
                    if user_id:
                        plan = MemoryWritePlan(
                            writes=[MemoryWrite(op=MemoryOp.ADD, content=content, summary=summary, tags=tags)],
                            rationale="Admin manual save.",
                        )
                        committed = adapter.commit(plan, AgentScope(user_id=user_id, project_id=project_id))
                        memory = committed[0] if committed else None
                    else:
                        memory = engine.add_memory(content, summary=summary, project_id=project_id, tags=tags)
                return {"memory": _memory_payload(memory)}
            if self.path == "/admin/retrieval/test":
                query = str(payload.get("query", ""))
                project_id = payload.get("project_id") or None
                user_id = str(payload.get("user_id", "")).strip()
                limit = int(payload.get("limit", 8))
                mode = RetrievalMode(str(payload.get("mode", RetrievalMode.ANSWER_INJECTION.value)))
                results = _search_for_admin(engine, query, project_id, limit, mode, user_id=user_id)
                return {"items": [_search_payload(r) for r in results], "mode": mode.value}
            if self.path == "/admin/arbitration/dry-run":
                text = str(payload.get("user_input", ""))
                project_id = payload.get("project_id") or None
                user_id = str(payload.get("user_id", "")).strip()
                retrieved = _retrieved_for_admin(engine, text, project_id, int(payload.get("limit", 5)), user_id=user_id)
                try:
                    extractor = DeepSeekMemoryExtractor.from_env()
                    req = extractor.dry_run_request(text, agent_output=str(payload.get("agent_output", "")),
                        tool_results=[str(i) for i in payload.get("tool_results", [])],
                        project_id=_admin_storage_project_id(project_id, user_id), retrieved_memories=retrieved)
                    return {"ok": True, "will_call_api": req["estimate"]["will_call_api"], "retrieved_memories": retrieved, "dry_run": req}
                except LLMExtractorNotConfigured as exc:
                    return {"ok": False, "will_call_api": False, "reason": str(exc), "retrieved_memories": retrieved}
            if self.path == "/admin/arbitration/run":
                text = str(payload.get("user_input", ""))
                project_id = payload.get("project_id") or None
                user_id = str(payload.get("user_id", "")).strip()
                retrieved = _retrieved_for_admin(engine, text, project_id, int(payload.get("limit", 5)), user_id=user_id)
                plan = build_default_extractor().extract(
                    user_input=text, agent_output=str(payload.get("agent_output", "")),
                    tool_results=[str(i) for i in payload.get("tool_results", [])],
                    project_id=_admin_storage_project_id(project_id, user_id), retrieved_memories=retrieved)
                committed = []
                if bool(payload.get("commit", False)):
                    committed = [m.id for m in adapter.commit(plan, AgentScope(user_id=user_id or "default", project_id=project_id))]
                return {"retrieved_memories": retrieved, "plan": _plan_payload(plan), "committed_ids": committed}
            if self.path == "/admin/memory/archive":
                memory = engine.apply_operation(MemoryOp.ARCHIVE, target_id=int(payload["id"]))
                return {"memory": _memory_payload(memory) if memory else None}
            if self.path == "/admin/memory/delete":
                memory = engine.apply_operation(MemoryOp.DELETE, target_id=int(payload["id"]))
                return {"deleted": bool(memory), "id": int(payload["id"])}
            if self.path == "/admin/memory/reinforce":
                memory = engine.reinforce_used(int(payload["id"]))
                return {"memory": _memory_payload(memory)}
            if self.path == "/admin/memory/wrong":
                memory = engine.evolution.record_manual_correction(int(payload["id"]))
                return {"memory": _memory_payload(memory) if memory else None}
            if self.path == "/admin/memory/verify":
                memory = engine.evolution.record_manual_verify(int(payload["id"]))
                return {"memory": _memory_payload(memory) if memory else None}
            if self.path == "/admin/reindex-embeddings":
                return engine.reindex_embeddings()
            if self.path == "/admin/feedback":
                memory_id = payload.get("memory_id")
                rows = engine.store.feedback_events(int(memory_id) if memory_id is not None else None)
                return {"items": [dict(row) for row in rows]}
            raise ValueError(f"unknown endpoint: {self.path}")

        def _admin_health(self) -> dict[str, Any]:
            report = shared_engine.health_report()
            report["memory_index_version"] = shared_engine.store.index_version()
            report["embedding"] = embedding_config_from_env()
            report["deepseek_configured"] = bool(public_llm_config()["configured"])
            return report

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("request body must be valid JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("request JSON must be an object")
            return data

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, html: str, status: int = 200) -> None:
            raw = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not configured_api_key:
                return True
            provided = self.headers.get("X-BrainMemory-API-Key", "")
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                provided = authorization.removeprefix("Bearer ").strip()
            return secrets.compare_digest(provided, configured_api_key)

    return BrainMemoryRequestHandler, lambda: shared_engine.close()


# ── 序列化辅助 ──────────────────────────────────────────────

def _memory_payload(memory) -> dict[str, Any]:
    strength = current_strength(memory)
    return {
        "id": memory.id, "content": memory.content, "summary": memory.summary,
        "strength": round(strength, 4),
        "access_count": memory.access_count,
        "project_id": memory.project_id, "status": memory.status.value,
        "tags": memory.tags, "sensitivity": memory.sensitivity,
        "superseded_by": memory.superseded_by,
        "decay_rate": round(memory.decay_rate, 5),
        "boost": round(memory.boost, 4),
        "trust": round(memory.trust, 4),
        "trust_alpha": round(memory.trust_alpha, 4),
        "trust_beta": round(memory.trust_beta, 4),
        "stability": round(memory.stability, 4),
        "difficulty": round(memory.difficulty, 4),
        "utility": round(memory.utility, 4),
        "exposure_count": memory.exposure_count,
        "correction_count": memory.correction_count,
        "error_count": memory.error_count,
        "verify_count": memory.verify_count,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
    }


def _search_payload(result) -> dict[str, Any]:
    return {
        "memory": _memory_payload(result.memory),
        "final_score": round(result.final_score, 4),
        "semantic_similarity": round(result.semantic_similarity, 4),
        "keyword_score": round(result.keyword_score, 4),
        "current_strength": round(result.current_strength, 4),
        "trust_score": round(result.trust_score, 4),
        "utility_score": round(result.utility_score, 4),
        "interference": round(result.interference, 4),
    }


def _search_for_admin(engine, text, project_id, limit, mode: RetrievalMode, user_id: str = "") -> list[Any]:
    return engine.search(text, project_id=project_id, limit=limit, mode=mode)


def _retrieved_for_admin(engine, text, project_id, limit, user_id: str = "") -> list[dict[str, Any]]:
    ranked = engine.search(
        text,
        project_id=project_id,
        limit=limit,
        mode=RetrievalMode.WRITE_ARBITRATION,
    )
    return [{
        "id": r.memory.id, "content": r.memory.content,
        "summary": r.memory.summary, "tags": r.memory.tags,
        "status": r.memory.status.value,
    } for r in ranked if r.memory.id]


def _admin_storage_project_id(project_id: str | None, user_id: str = "") -> str | None:
    return project_id


def _plan_payload(plan) -> dict[str, Any]:
    return {
        "rationale": plan.rationale,
        "feedback": plan.feedback or [],
        "writes": [{"op": w.op.value, "target_id": w.target_id, "content": w.content, "summary": w.summary, "tags": w.tags} for w in plan.writes],
    }


# ── 管理控制台 HTML ─────────────────────────────────────────

def admin_console_html(api_key: str | None = None) -> str:
    import json as _json
    key_js = _json.dumps(api_key) if api_key else "null"
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MB 记忆控制台</title>
  <style>
    :root { color-scheme: light; --bg: #f6f7f9; --panel: #ffffff; --panel-2: #eef2f6; --text: #1d2430; --muted: #667085; --line: #d7dde5; --accent: #0f766e; --accent-2: #2563eb; --danger: #b42318; --warn: #b54708; --radius: 8px; font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    .shell { min-height: 100vh; display: grid; grid-template-columns: 240px 1fr; }
    aside { background: #1f2937; color: #f9fafb; padding: 20px 16px; }
    .brand { font-size: 18px; font-weight: 700; margin-bottom: 20px; }
    .nav { display: grid; gap: 6px; }
    .nav button { width: 100%; text-align: left; border: 0; background: transparent; color: #d1d5db; padding: 10px 12px; border-radius: 6px; cursor: pointer; font-size: 14px; }
    .nav button.active, .nav button:hover { background: #374151; color: #fff; }
    main { padding: 20px; overflow: auto; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
    h1 { font-size: 22px; margin: 0; }
    h2 { font-size: 16px; margin: 0 0 12px; }
    .grid { display: grid; gap: 12px; }
    .stats { grid-template-columns: repeat(4, minmax(140px, 1fr)); }
    .two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; }
    .stat .label { color: var(--muted); font-size: 12px; }
    .stat .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
    input, select, textarea { border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; font: inherit; background: #fff; color: var(--text); min-height: 36px; }
    textarea { width: 100%; min-height: 96px; resize: vertical; }
    .form-grid { display: grid; grid-template-columns: 100px 1fr 1fr; gap: 8px; margin-bottom: 12px; }
    .form-grid textarea { grid-column: 1 / -1; min-height: 72px; }
    .llm-grid { display: grid; grid-template-columns: minmax(180px, 240px) minmax(320px, 1fr); gap: 8px; margin-bottom: 12px; }
    .llm-grid .wide { grid-column: 1 / -1; }
    button.action { border: 1px solid var(--line); background: #fff; color: var(--text); border-radius: 6px; padding: 8px 11px; cursor: pointer; font: inherit; }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.blue { background: var(--accent-2); color: #fff; border-color: var(--accent-2); }
    button.danger { color: var(--danger); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; background: var(--panel-2); position: sticky; top: 0; }
    .content-cell { max-width: 460px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .pill { display: inline-flex; align-items: center; min-height: 22px; border-radius: 999px; padding: 2px 8px; font-size: 12px; background: #e5eefb; color: #1d4ed8; }
    .pill.ok { background: #dcfce7; color: #166534; }
    .pill.warn { background: #ffedd5; color: var(--warn); }
    .pill.bad { background: #fee2e2; color: var(--danger); }
    .hidden { display: none; }
    pre { margin: 0; background: #111827; color: #e5e7eb; border-radius: 6px; padding: 12px; overflow: auto; max-height: 420px; font-size: 12px; }
    .split { display: grid; grid-template-columns: 360px 1fr; gap: 12px; align-items: start; }
    .stack { display: grid; gap: 12px; }
    .bars { display: grid; gap: 8px; }
    .bar { display: grid; grid-template-columns: 120px 1fr 52px; align-items: center; gap: 8px; font-size: 12px; }
    .track { height: 8px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }
    .fill { height: 100%; background: var(--accent); }
    .muted { color: var(--muted); }
    .notice { margin-bottom: 12px; padding: 10px 12px; border: 1px solid var(--line); background: #fff; border-radius: var(--radius); color: var(--muted); }
    .notice.bad { border-color: #fecaca; background: #fef2f2; color: var(--danger); }
    .notice.ok { border-color: #bbf7d0; background: #f0fdf4; color: #166534; }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">类脑记忆</div>
      <div class="nav">
        <button data-view="dashboard" class="active">总览</button>
        <button data-view="memories">记忆库</button>
        <button data-view="llm">LLM 接入</button>
        <button data-view="retrieval">检索实验</button>
        <button data-view="arbitration">仲裁实验</button>
      </div>
    </aside>
    <main>
      <div class="topbar">
        <h1 id="title">总览</h1>
        <button class="action" onclick="refreshAll()">刷新</button>
      </div>
      <section id="dashboard" class="view">
        <div class="grid stats">
          <div class="panel stat"><div class="label">记忆总数</div><div class="value" id="stat-total">0</div></div>
          <div class="panel stat"><div class="label">活跃记忆</div><div class="value" id="stat-active">0</div></div>
          <div class="panel stat"><div class="label">嵌入后端</div><div class="value" id="stat-embedding" style="font-size:18px">-</div></div>
          <div class="panel stat"><div class="label">常见标签</div><div class="value" id="stat-tags" style="font-size:14px">-</div></div>
        </div>
        <div class="grid two" style="margin-top:12px">
          <div class="panel"><h2>强度统计</h2><div id="strength-stats" class="bars"></div></div>
          <div class="panel"><h2>状态分布</h2><div id="status-bars" class="bars"></div></div>
        </div>
      </section>
      <section id="memories" class="view hidden">
        <div class="panel">
          <h2>新增 / 修改记忆</h2>
          <div class="form-grid">
            <input id="edit-id" placeholder="ID（空=新增）">
            <input id="edit-project" placeholder="project_id">
            <input id="edit-user" placeholder="user_id（新增可选）">
            <input id="edit-tags" placeholder="标签（逗号分隔）">
            <textarea id="edit-content" placeholder="记忆内容"></textarea>
            <input id="edit-summary" placeholder="摘要（可选）">
          </div>
          <div class="toolbar">
            <button class="action primary" onclick="saveMemory()">保存</button>
            <button class="action" onclick="clearMemoryForm()">清空</button>
          </div>
          <div id="memory-notice" class="notice hidden"></div>
          <div class="toolbar">
            <input id="memory-filter" placeholder="筛选..." oninput="renderMemories()">
            <select id="memory-status" onchange="renderMemories()">
              <option value="">全部</option><option value="active">活跃</option><option value="archived">已归档</option><option value="superseded">已替代</option>
            </select>
            <button class="action" onclick="loadMemories()">重新加载</button>
            <button class="action blue" onclick="reindexEmbeddings()">重建向量</button>
          </div>
          <div style="overflow:auto"><table><thead><tr><th>ID</th><th>强度</th><th>访问</th><th>状态</th><th>标签</th><th>内容</th><th>操作</th></tr></thead><tbody id="memory-rows"></tbody></table></div>
        </div>
      </section>
      <section id="llm" class="view hidden">
        <div class="stack">
          <div class="panel"><h2>LLM API</h2>
            <div class="llm-grid">
              <select id="llm-provider"><option value="deepseek">DeepSeek</option></select>
              <input id="llm-model" placeholder="模型">
              <input id="llm-base-url" class="wide" placeholder="API 地址">
              <input id="llm-api-key" class="wide" type="password" placeholder="API Key（留空=不修改）">
              <input id="llm-thinking" placeholder="thinking">
              <input id="llm-temperature" type="number" min="0" max="2" step="0.05" placeholder="温度">
              <input id="llm-max-output" type="number" min="32" max="8192" placeholder="输出上限">
              <input id="llm-max-input" type="number" min="256" max="200000" placeholder="输入上限">
            </div>
            <div class="toolbar">
              <button class="action primary" onclick="saveLlmConfig()">保存</button>
              <button class="action blue" onclick="probeLlm()">连通测试</button>
            </div>
            <div id="llm-notice" class="notice hidden"></div>
            <pre id="llm-output">{}</pre>
          </div>
        </div>
      </section>
      <section id="retrieval" class="view hidden">
        <div class="split">
          <div class="panel"><h2>查询</h2>
            <textarea id="retrieval-query">以后应该怎么称呼我？</textarea>
            <div class="toolbar">
              <input id="retrieval-project" placeholder="project_id">
              <input id="retrieval-user" placeholder="user_id（可选）">
              <select id="retrieval-mode"><option value="answer_injection">回答注入</option><option value="write_arbitration">写入仲裁</option></select>
              <input id="retrieval-limit" type="number" value="8" min="1" max="20" style="width:72px">
            </div>
            <button class="action primary" onclick="runRetrieval()">检索</button>
            <div id="retrieval-notice" class="notice hidden"></div>
          </div>
          <div class="panel"><h2>结果</h2>
            <div style="overflow:auto"><table><thead><tr><th>ID</th><th>总分</th><th>语义</th><th>关键词</th><th>强度</th><th>内容</th></tr></thead><tbody id="retrieval-rows"></tbody></table></div>
          </div>
        </div>
      </section>
      <section id="arbitration" class="view hidden">
        <div class="split">
          <div class="panel"><h2>对话输入</h2>
            <textarea id="arb-user" placeholder="用户输入"></textarea>
            <input id="arb-project" placeholder="project_id" style="width:100%;margin-top:8px">
            <input id="arb-user-id" placeholder="user_id（可选）" style="width:100%;margin-top:8px">
            <textarea id="arb-agent" placeholder="agent_output" style="margin-top:8px"></textarea>
            <div class="toolbar">
              <button class="action" onclick="dryRunArbitration()">本地预览</button>
              <button class="action blue" onclick="runArbitration(false)">运行仲裁</button>
              <button class="action primary" onclick="runArbitration(true)">仲裁并写入</button>
            </div>
            <div id="arb-notice" class="notice hidden"></div>
          </div>
          <div class="panel"><h2>输出</h2><pre id="arb-output"></pre></div>
        </div>
      </section>
    </main>
  </div>
<script>
const BRAINMEMORY_API_KEY = """ + key_js + r""";
const state={memories:[],health:{}};
const titles={dashboard:"总览",memories:"记忆库",llm:"LLM 接入",retrieval:"检索实验",arbitration:"仲裁实验"};
const statusLabels={active:"活跃",archived:"已归档",superseded:"已替代"};
async function api(path,payload=null){
  const headers=payload===null?{}:{"Content-Type":"application/json"};
  if(BRAINMEMORY_API_KEY)headers["X-BrainMemory-API-Key"]=BRAINMEMORY_API_KEY;
  const opts=payload===null?{headers}:{method:"POST",headers,body:JSON.stringify(payload)};
  const res=await fetch(path,opts);const data=await res.json();
  if(!res.ok)throw new Error(data.message||data.error||"request failed");return data;
}
document.querySelectorAll(".nav button").forEach(btn=>btn.addEventListener("click",()=>{
  document.querySelectorAll(".nav button").forEach(i=>i.classList.remove("active"));btn.classList.add("active");
  document.querySelectorAll(".view").forEach(v=>v.classList.add("hidden"));
  document.getElementById(btn.dataset.view).classList.remove("hidden");
  document.getElementById("title").textContent=titles[btn.dataset.view];
}));
function bars(target,data){
  const entries=Object.entries(data||{});const max=Math.max(1,...entries.map(([,v])=>v));
  document.getElementById(target).innerHTML=entries.map(([k,v])=>`<div class="bar"><span>${k}</span><div class="track"><div class="fill" style="width:${Math.round(v/max*100)}%"></div></div><span>${typeof v==="number"?v.toFixed(3):v}</span></div>`).join("")||'<span class="muted">-</span>';
}
async function loadHealth(){
  state.health=await api("/admin/health");
  document.getElementById("stat-total").textContent=state.health.total||0;
  document.getElementById("stat-active").textContent=state.health.active||0;
  document.getElementById("stat-embedding").textContent=(state.health.embedding||{}).backend||"-";
  const tags=state.health.common_tags||[];
  document.getElementById("stat-tags").textContent=tags.length?tags.slice(0,3).map(([t,c])=>`${t}(${c})`).join(", "):"-";
  bars("strength-stats",{avg:state.health.avg_strength||0,max:state.health.max_strength||0,min:state.health.min_strength||0});
  bars("status-bars",state.health.statuses||{});
}
async function loadMemories(){state.memories=(await api("/admin/memories",{})).items||[];renderMemories();}
async function loadLlmConfig(){
  const d=await(await fetch("/admin/llm/config")).json();
  document.getElementById("llm-provider").value=d.provider||"deepseek";
  document.getElementById("llm-model").value=d.model||"";
  document.getElementById("llm-base-url").value=d.base_url||"";
  document.getElementById("llm-thinking").value=d.thinking||"disabled";
  document.getElementById("llm-api-key").value="";
  document.getElementById("llm-temperature").value=d.temperature??0;
  document.getElementById("llm-max-output").value=d.max_output_tokens||800;
  document.getElementById("llm-max-input").value=d.max_input_chars||6000;
  document.getElementById("llm-output").textContent=JSON.stringify(d,null,2);
}
async function saveLlmConfig(){
  await withNotice("llm-notice",async()=>{
    const d=await api("/admin/llm/config",{
      provider:document.getElementById("llm-provider").value,
      api_key:document.getElementById("llm-api-key").value,
      model:document.getElementById("llm-model").value,
      base_url:document.getElementById("llm-base-url").value,
      thinking:document.getElementById("llm-thinking").value,
      temperature:Number(document.getElementById("llm-temperature").value||0),
      max_output_tokens:Number(document.getElementById("llm-max-output").value||800),
      max_input_chars:Number(document.getElementById("llm-max-input").value||6000)
    });
    document.getElementById("llm-output").textContent=JSON.stringify(d,null,2);
    document.getElementById("llm-api-key").value="";showNotice("llm-notice","已保存","ok");
  });
}
async function probeLlm(){
  await withNotice("llm-notice",async()=>{
    const d=await api("/admin/llm/probe",{});
    document.getElementById("llm-output").textContent=JSON.stringify(d,null,2);
    showNotice("llm-notice",d.ok?"连通正常":"异常",d.ok?"ok":"bad");
  });
}
function statusPill(s){const cls=s==="active"?"ok":s==="archived"||s==="superseded"?"warn":"bad";return `<span class="pill ${cls}">${statusLabels[s]||s}</span>`;}
function renderMemories(){
  const q=document.getElementById("memory-filter").value.toLowerCase();
  const st=document.getElementById("memory-status").value;
  const rows=state.memories.filter(m=>{const hay=(m.content+" "+m.tags+" "+m.project_id).toLowerCase();return (!q||hay.includes(q))&&(!st||m.status===st);});
  document.getElementById("memory-rows").innerHTML=rows.map(m=>`<tr><td>${m.id}</td><td>${m.strength}</td><td>${m.access_count}</td><td>${statusPill(m.status)}</td><td>${esc(m.tags)}</td><td class="content-cell" title="${esc(m.content)}">${esc(m.content)}</td><td>
    <button class="action" onclick="editMemory(${m.id})">编辑</button>
    <button class="action" onclick="memoryAction('/admin/memory/reinforce',${m.id})">强化</button>
    <button class="action" onclick="memoryAction('/admin/memory/archive',${m.id})">归档</button>
    <button class="action danger" onclick="memoryAction('/admin/memory/delete',${m.id})">删除</button>
  </td></tr>`).join("");
}
async function memoryAction(path,id){await withNotice("memory-notice",async()=>{await api(path,{id});await refreshAll();showNotice("memory-notice","完成","ok");});}
async function saveMemory(){
  await withNotice("memory-notice",async()=>{
    const r=await api("/admin/memory/save",{
      id:document.getElementById("edit-id").value||null,
      content:document.getElementById("edit-content").value,
      project_id:document.getElementById("edit-project").value||null,
      user_id:document.getElementById("edit-user").value||null,
      summary:document.getElementById("edit-summary").value,
      tags:document.getElementById("edit-tags").value
    });
    clearMemoryForm();await refreshAll();showNotice("memory-notice","已保存 #"+r.memory.id,"ok");
  });
}
function editMemory(id){
  const m=state.memories.find(i=>i.id===id);if(!m)return;
  document.getElementById("edit-id").value=m.id;
  document.getElementById("edit-project").value=m.project_id||"";
  document.getElementById("edit-user").value="";
  document.getElementById("edit-tags").value=m.tags||"";
  document.getElementById("edit-content").value=m.content||"";
  document.getElementById("edit-summary").value=m.summary||"";
}
function clearMemoryForm(){["edit-id","edit-project","edit-user","edit-tags","edit-content","edit-summary"].forEach(id=>document.getElementById(id).value="");}
async function reindexEmbeddings(){
  await withNotice("memory-notice",async()=>{const r=await api("/admin/reindex-embeddings",{});await refreshAll();showNotice("memory-notice","已重建 "+r.reindexed+" 条","ok");});
}
async function runRetrieval(){
  await withNotice("retrieval-notice",async()=>{
    const d=await api("/admin/retrieval/test",{
      query:document.getElementById("retrieval-query").value,
      project_id:document.getElementById("retrieval-project").value||null,
      user_id:document.getElementById("retrieval-user").value||null,
      mode:document.getElementById("retrieval-mode").value,
      limit:Number(document.getElementById("retrieval-limit").value||8)
    });
    document.getElementById("retrieval-rows").innerHTML=(d.items||[]).map(i=>`<tr><td>${i.memory.id}</td><td>${i.final_score}</td><td>${i.semantic_similarity}</td><td>${i.keyword_score}</td><td>${i.current_strength}</td><td class="content-cell" title="${esc(i.memory.content)}">${esc(i.memory.content)}</td></tr>`).join("");
    showNotice("retrieval-notice",(d.items||[]).length+" 条结果","ok");
  });
}
async function dryRunArbitration(){await withNotice("arb-notice",async()=>{const r=await api("/admin/arbitration/dry-run",arbPayload());document.getElementById("arb-output").textContent=JSON.stringify(r,null,2);showNotice("arb-notice","预览完成","ok");});}
async function runArbitration(commit){await withNotice("arb-notice",async()=>{const r=await api("/admin/arbitration/run",arbPayload(commit));document.getElementById("arb-output").textContent=JSON.stringify(r,null,2);if(commit)await refreshAll();showNotice("arb-notice",commit?"完成":"完成（未写入）","ok");});}
function arbPayload(commit){return {user_input:document.getElementById("arb-user").value,project_id:document.getElementById("arb-project").value||null,user_id:document.getElementById("arb-user-id").value||null,agent_output:document.getElementById("arb-agent").value,commit:!!commit,limit:5,tool_results:[]};}
function esc(v){return String(v||"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[ch]));}
function showNotice(t,m,k){const b=document.getElementById(t);b.textContent=m;b.className="notice "+(k||"");}
async function withNotice(t,fn){try{await fn();}catch(e){showNotice(t,e.message||String(e),"bad");}}
async function refreshAll(){await loadHealth();await loadMemories();await loadLlmConfig();}
refreshAll().catch(e=>alert(e.message));
</script>
</body>
</html>"""


def run_server(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765, api_key: str | None = None) -> None:
    """Start the 类脑记忆 HTTP sidecar with a visual startup animation."""
    import sys

    # ── Suppress noisy third-party output during startup ────────────
    import logging
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

    # ── Startup messages use stdout for coordination with parent process ──
    handler_cls = None
    cleanup_fn = None
    server = None

    try:
        sys.stderr.write("类脑记忆 loading model...")
        sys.stderr.flush()

        handler_cls, cleanup_fn = create_handler(db_path, api_key=api_key)

        server = HTTPServer((host, port), handler_cls)
        sys.stderr.write(f"\r类脑记忆 ready -> http://{host}:{port}/admin\n")
        sys.stderr.flush()
        server.serve_forever()
    except Exception:
        sys.stderr.write("\r类脑记忆 startup FAILED\n")
        sys.stderr.flush()
        raise
    finally:
        if cleanup_fn is not None:
            cleanup_fn()
        if server is not None:
            server.server_close()
