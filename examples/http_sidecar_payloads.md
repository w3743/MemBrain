# HTTP Sidecar Payload Examples

Start the service:

```powershell
$env:CSM_API_KEY="dev-secret"
$env:CSM_DEEPSEEK_API_KEY="<your-deepseek-key>"
python -m csm_agent.cli --db csm_memory.db serve --host 127.0.0.1 --port 8765
```

Before starting live extraction, inspect the request locally:

```powershell
python -m csm_agent.cli deepseek-check "以后回答技术问题时，请先给结论。" --project demo
```

## Health

```powershell
curl http://127.0.0.1:8765/health
```

Response:

```json
{"ok": true, "service": "csm-memory-sidecar"}
```

## OpenAPI Contract

```powershell
curl http://127.0.0.1:8765/openapi.json
```

## OpenClaw-Style Pre-Prompt

```powershell
curl -X POST http://127.0.0.1:8765/pre_prompt `
  -H "Content-Type: application/json" `
  -H "X-CSM-API-Key: dev-secret" `
  -d "{\"user_id\":\"u1\",\"workspace_id\":\"openclaw-demo\",\"message\":\"这个工作区的向量后端是什么？\"}"
```

Response shape:

```json
{
  "memory_context": "Relevant long-term memory:\n- [L2 #1 score=0.421] OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。",
  "memory_ids": [1],
  "items": []
}
```

## OpenClaw-Style Post-Run

```powershell
curl -X POST http://127.0.0.1:8765/post_run `
  -H "Content-Type: application/json" `
  -H "X-CSM-API-Key: dev-secret" `
  -d "{\"user_id\":\"u1\",\"workspace_id\":\"openclaw-demo\",\"message\":\"OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。\"}"
```

Response shape:

```json
{
  "write_plan": ["ADD"],
  "committed_ids": [1],
  "rationale": "Reinforce used memories and store extracted reusable facts."
}
```

## Hermes-Style Provider Calls

Remember:

```powershell
curl -X POST http://127.0.0.1:8765/remember `
  -H "Content-Type: application/json" `
  -H "X-CSM-API-Key: dev-secret" `
  -d "{\"user_id\":\"u1\",\"project_id\":\"hermes\",\"content\":\"Hermes 项目回答风格：简洁，避免无关解释。\"}"
```

Get context:

```powershell
curl -X POST http://127.0.0.1:8765/context `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer dev-secret" `
  -d "{\"user_id\":\"u1\",\"project_id\":\"hermes\",\"prompt\":\"回答风格是什么？\"}"
```

Sleep consolidation:

```powershell
curl -X POST http://127.0.0.1:8765/sleep -H "Content-Type: application/json" -H "X-CSM-API-Key: dev-secret" -d "{}"
```
