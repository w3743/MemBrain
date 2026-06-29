from __future__ import annotations

from typing import Any


def openapi_spec() -> dict[str, Any]:
    json_content = {"application/json": {"schema": {"type": "object"}}}
    admin_memory_save_content = {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": ["integer", "string", "null"], "description": "Existing memory id. Leave empty to create."},
                    "content": {"type": "string"},
                    "project_id": {"type": ["string", "null"], "description": "Workspace/project boundary."},
                    "user_id": {
                        "type": ["string", "null"],
                        "deprecated": True,
                        "description": "Ignored compatibility field; BrainMemory runs in single-user mode.",
                    },
                    "summary": {"type": "string"},
                    "tags": {"type": "string"},
                },
                "required": ["content"],
            }
        }
    }
    admin_retrieval_content = {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "project_id": {"type": ["string", "null"]},
                    "user_id": {
                        "type": ["string", "null"],
                        "deprecated": True,
                        "description": "Ignored compatibility field; project_id is the only retrieval boundary.",
                    },
                    "mode": {"type": "string", "enum": ["answer_injection", "write_arbitration"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            }
        }
    }
    admin_arbitration_content = {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "user_input": {"type": "string"},
                    "project_id": {"type": ["string", "null"]},
                    "user_id": {
                        "type": ["string", "null"],
                        "deprecated": True,
                        "description": "Ignored compatibility field; project_id is the only storage boundary.",
                    },
                    "agent_output": {"type": "string"},
                    "tool_results": {"type": "array", "items": {"type": "string"}},
                    "commit": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["user_input"],
            }
        }
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "BrainMemory Sidecar API",
            "version": "0.1.0",
            "description": "Evidence-Adaptive Spaced Memory sidecar API for long-running LLM agents.",
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-BrainMemory-API-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Service health check",
                    "responses": {"200": {"description": "Healthy service", "content": json_content}},
                }
            },
            "/pre_prompt": {
                "post": {
                    "summary": "Retrieve compact memory context before an agent run",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": json_content},
                    "responses": {"200": {"description": "Memory context", "content": json_content}},
                }
            },
            "/post_run": {
                "post": {
                    "summary": "Observe an agent run and commit memory updates",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": json_content},
                    "responses": {"200": {"description": "Write plan result", "content": json_content}},
                }
            },
            "/remember": {
                "post": {
                    "summary": "Store one explicit memory",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": json_content},
                    "responses": {"200": {"description": "Stored memory id", "content": json_content}},
                }
            },
            "/context": {
                "post": {
                    "summary": "Retrieve Hermes-style memory provider context",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": json_content},
                    "responses": {"200": {"description": "Memory context", "content": json_content}},
                }
            },
            "/sleep": {
                "post": {
                    "summary": "Run sleep consolidation",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": False, "content": json_content},
                    "responses": {"200": {"description": "Sleep consolidation report", "content": json_content}},
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "OpenAPI contract",
                    "responses": {"200": {"description": "OpenAPI JSON", "content": json_content}},
                }
            },
            "/admin": {
                "get": {
                    "summary": "BrainMemory Console web UI",
                    "responses": {"200": {"description": "HTML admin console"}},
                }
            },
            "/admin/health": {
                "get": {
                    "summary": "Admin health and memory statistics",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "responses": {"200": {"description": "Admin health", "content": json_content}},
                }
            },
            "/admin/memories": {
                "post": {
                    "summary": "List all memories for the admin console",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": False, "content": json_content},
                    "responses": {"200": {"description": "Memory list", "content": json_content}},
                }
            },
            "/admin/memory/save": {
                "post": {
                    "summary": "Create or update one memory from the admin console",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": admin_memory_save_content},
                    "responses": {"200": {"description": "Saved memory", "content": json_content}},
                }
            },
            "/admin/retrieval/test": {
                "post": {
                    "summary": "Run retrieval lab query",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": admin_retrieval_content},
                    "responses": {"200": {"description": "Retrieval result", "content": json_content}},
                }
            },
            "/admin/arbitration/dry-run": {
                "post": {
                    "summary": "Preview DeepSeek arbiter request without spending tokens",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": admin_arbitration_content},
                    "responses": {"200": {"description": "Arbiter dry run", "content": json_content}},
                }
            },
            "/admin/arbitration/run": {
                "post": {
                    "summary": "Run arbiter preview or commit",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": True, "content": admin_arbitration_content},
                    "responses": {"200": {"description": "Arbiter plan", "content": json_content}},
                }
            },
            "/admin/reindex-embeddings": {
                "post": {
                    "summary": "Rebuild embeddings for all memories",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": False, "content": json_content},
                    "responses": {"200": {"description": "Reindex result", "content": json_content}},
                }
            },
            "/admin/feedback": {
                "post": {
                    "summary": "List probabilistic feedback evidence events",
                    "security": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
                    "requestBody": {"required": False, "content": json_content},
                    "responses": {"200": {"description": "Feedback events", "content": json_content}},
                }
            },
        },
    }
