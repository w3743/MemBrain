from __future__ import annotations

import http.client
import json
import os
import threading
from http.server import HTTPServer

from csm_agent.server import create_handler


class SidecarTestServer:
    def __init__(self, db_path, api_key: str | None = None):
        self._old_llm_config_path = os.environ.get("CSM_LLM_CONFIG_PATH")
        os.environ["CSM_LLM_CONFIG_PATH"] = str(db_path.with_name("llm_config.json"))
        handler_cls, self._cleanup = create_handler(db_path, api_key=api_key)
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self._cleanup()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self._old_llm_config_path is None:
            os.environ.pop("CSM_LLM_CONFIG_PATH", None)
        else:
            os.environ["CSM_LLM_CONFIG_PATH"] = self._old_llm_config_path

    def post(self, path: str, payload: dict, headers: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            request_headers = {"Content-Type": "application/json"}
            if headers:
                request_headers.update(headers)
            conn.request("POST", path, body=body, headers=request_headers)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
        finally:
            conn.close()

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
        finally:
            conn.close()

    def get_text(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")
            return response.status, raw, response.getheader("Content-Type")
        finally:
            conn.close()


def test_sidecar_health_and_openclaw_flow(tmp_path) -> None:
    server = SidecarTestServer(tmp_path / "sidecar.db")
    server.start()
    try:
        status, health = server.get("/health")
        assert status == 200
        assert health["ok"] is True

        status, spec = server.get("/openapi.json")
        assert status == 200
        assert spec["openapi"].startswith("3.")
        assert "/pre_prompt" in spec["paths"]
        admin_save_props = spec["paths"]["/admin/memory/save"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        admin_retrieval_props = spec["paths"]["/admin/retrieval/test"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        admin_arbitration_props = spec["paths"]["/admin/arbitration/run"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "user_id" in admin_save_props
        assert "personal/shared partition" in admin_save_props["user_id"]["description"]
        assert "user_id" in admin_retrieval_props
        assert "shared project partition" in admin_retrieval_props["user_id"]["description"]
        assert "user_id" in admin_arbitration_props
        assert "AgentScope partitioning" in admin_arbitration_props["user_id"]["description"]

        status, post = server.post("/post_run", {
            "user_id": "u1", "workspace_id": "openclaw-demo",
            "message": "OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。",
            "explicit_memories": ["OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。"],
        })
        assert status == 200
        assert "ADD" in post["write_plan"]
        assert post["committed_ids"]

        status, pre = server.post("/pre_prompt", {
            "user_id": "u1", "workspace_id": "openclaw-demo",
            "message": "这个工作区的向量后端是什么？",
        })
        assert status == 200
        assert "sqlite-vec" in pre["memory_context"]
    finally:
        server.stop()


def test_sidecar_hermes_provider_endpoints(tmp_path) -> None:
    server = SidecarTestServer(tmp_path / "sidecar.db")
    server.start()
    try:
        status, remembered = server.post("/remember", {
            "user_id": "u1", "project_id": "hermes",
            "content": "Hermes 项目回答风格：简洁，避免无关解释。",
        })
        assert status == 200
        assert remembered["memory_id"] is not None

        status, context = server.post("/context", {
            "user_id": "u1", "project_id": "hermes",
            "prompt": "回答风格是什么？",
        })
        assert status == 200
        assert "简洁" in context["memory_context"]

        status, sleep = server.post("/sleep", {})
        assert status == 200
        assert sleep["total"] >= 1
    finally:
        server.stop()


def test_sidecar_api_key_auth(tmp_path) -> None:
    server = SidecarTestServer(tmp_path / "sidecar.db", api_key="secret-key")
    server.start()
    try:
        status, health = server.get("/health")
        assert status == 200

        status, rejected = server.post("/post_run", {
            "user_id": "u1", "workspace_id": "test",
            "message": "test", "explicit_memories": ["test"],
        })
        assert status == 401

        status, accepted = server.post("/post_run", {
            "user_id": "u1", "workspace_id": "test",
            "message": "test", "explicit_memories": ["test memory"],
        }, headers={"X-CSM-API-Key": "secret-key"})
        assert status == 200
        assert "ADD" in accepted["write_plan"]
    finally:
        server.stop()


def test_admin_console_and_core_admin_apis(tmp_path) -> None:
    server = SidecarTestServer(tmp_path / "sidecar.db")
    server.start()
    try:
        status, html, content_type = server.get_text("/admin")
        assert status == 200
        assert "text/html" in content_type
        assert "CSM" in html
        assert "edit-user" in html
        assert "retrieval-user" in html
        assert "arb-user-id" in html

        status, remembered = server.post("/remember", {
            "user_id": "u1", "project_id": "admin-demo",
            "content": "用户偏好被称为家裕。",
        })
        assert status == 200

        status, health = server.get("/admin/health")
        assert status == 200
        assert health["active"] == 1
        assert "embedding" in health

        status, memories = server.post("/admin/memories", {})
        assert status == 200
        assert memories["items"][0]["content"] == "用户偏好被称为家裕。"

        status, saved = server.post("/admin/memory/save", {
            "content": "用户的名字叫王家裕。",
            "project_id": "admin-demo",
            "tags": "名字,身份",
        })
        assert status == 200
        assert saved["memory"]["tags"] == "名字,身份"

        status, scoped_saved = server.post("/admin/memory/save", {
            "content": "我叫王家裕。",
            "project_id": "admin-demo",
            "user_id": "u1",
            "tags": "名字,身份,称呼",
        })
        assert status == 200
        assert scoped_saved["memory"]["project_id"] == "admin-demo:user:u1"

        status, scoped_project = server.post("/admin/memory/save", {
            "content": "项目依赖管理使用 bun install。",
            "project_id": "admin-demo",
            "user_id": "u1",
            "tags": "依赖,bun",
        })
        assert status == 200
        assert scoped_project["memory"]["project_id"] == "admin-demo"

        status, updated = server.post("/admin/memory/save", {
            "id": saved["memory"]["id"],
            "content": "用户的名字叫江家裕。",
            "project_id": "admin-demo",
            "tags": "名字,身份",
        })
        assert status == 200
        assert updated["memory"]["content"] == "用户的名字叫江家裕。"

        status, deleted = server.post("/admin/memory/delete", {"id": saved["memory"]["id"]})
        assert status == 200
        assert deleted["deleted"] is True

        status, memories_after_delete = server.post("/admin/memories", {})
        assert status == 200
        assert all(item["id"] != saved["memory"]["id"] for item in memories_after_delete["items"])

        status, retrieval = server.post("/admin/retrieval/test", {
            "query": "应该怎么称呼用户？",
            "project_id": "admin-demo",
            "user_id": "u1",
            "mode": "answer_injection",
            "limit": 3,
        })
        assert status == 200
        assert retrieval["items"]
        assert retrieval["items"][0]["memory"]["id"] == remembered["memory_id"]

        status, manual_personal = server.post("/admin/retrieval/test", {
            "query": "我叫什么名字？",
            "project_id": "admin-demo",
            "user_id": "u1",
            "mode": "answer_injection",
            "limit": 5,
        })
        assert status == 200
        assert any(item["memory"]["id"] == scoped_saved["memory"]["id"] for item in manual_personal["items"])

        status, manual_private_other_user = server.post("/admin/retrieval/test", {
            "query": "我叫什么名字？",
            "project_id": "admin-demo",
            "user_id": "u2",
            "mode": "answer_injection",
            "limit": 5,
        })
        assert status == 200
        assert all(item["memory"]["id"] != scoped_saved["memory"]["id"] for item in manual_private_other_user["items"])

        status, manual_shared_other_user = server.post("/admin/retrieval/test", {
            "query": "项目依赖管理怎么安装？",
            "project_id": "admin-demo",
            "user_id": "u2",
            "mode": "answer_injection",
            "limit": 5,
        })
        assert status == 200
        assert any(item["memory"]["id"] == scoped_project["memory"]["id"] for item in manual_shared_other_user["items"])

        status, arb_u1 = server.post("/admin/arbitration/dry-run", {
            "user_input": "应该怎么称呼用户？",
            "project_id": "admin-demo",
            "user_id": "u1",
            "agent_output": "",
            "limit": 3,
        })
        assert status == 200
        assert any(item["id"] == remembered["memory_id"] for item in arb_u1["retrieved_memories"])

        status, arb_u2 = server.post("/admin/arbitration/dry-run", {
            "user_input": "应该怎么称呼用户？",
            "project_id": "admin-demo",
            "user_id": "u2",
            "agent_output": "",
            "limit": 3,
        })
        assert status == 200
        assert all(item["id"] != remembered["memory_id"] for item in arb_u2["retrieved_memories"])

        status, reindex = server.post("/admin/reindex-embeddings", {})
        assert status == 200
        assert reindex["reindexed"] >= 1
    finally:
        server.stop()
