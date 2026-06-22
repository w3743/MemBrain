# CSM Memory Sidecar API Contract

The sidecar exposes a small JSON HTTP API for external agent runtimes.

Run:

```powershell
python -m csm_agent.cli --db csm_memory.db serve --host 127.0.0.1 --port 8765
```

Machine-readable contract:

```text
GET /openapi.json
```

Core endpoints:

- `GET /health`: service readiness check.
- `GET /admin`: built-in CSM Memory Console.
- `GET /admin/health`: admin statistics, embedding config, index version.
- `POST /admin/memories`: list memories for the console.
- `POST /admin/retrieval/test`: run retrieval lab query with score breakdown.
- `POST /admin/arbitration/dry-run`: preview DeepSeek arbiter request without spending tokens.
- `POST /admin/arbitration/run`: run arbiter plan and optionally commit.
- `POST /admin/reindex-embeddings`: rebuild stored embeddings.
- `POST /pre_prompt`: retrieve compact memory context before an agent run. The response includes `memory_context`, `memory_ids`, and per-item retrieval explanation fields.
- `POST /post_run`: observe an agent run and commit memory writes.
- `POST /remember`: store one explicit memory through a provider-style API.
- `POST /context`: retrieve provider-style memory context.
- `POST /sleep`: run sleep consolidation.

All request and response bodies are UTF-8 JSON objects. Unknown endpoints return JSON errors.

`/pre_prompt` uses conservative answer injection. An empty `memory_context` is a normal result, not an error. Host agents should inject memory only when `memory_context` is non-empty; when CSM is uncertain, it intentionally returns nothing so unrelated chats are not disturbed by weakly related long-term memories.

Authentication is optional for local development. If `CSM_API_KEY` or `--api-key` is set, all POST endpoints require either:

```text
X-CSM-API-Key: <key>
```

or:

```text
Authorization: Bearer <key>
```

`GET /health` and `GET /openapi.json` remain public so orchestrators can probe readiness and discover the contract.

Environment variables:

- `CSM_DB`: default SQLite database path.
- `CSM_HOST`: default bind host for `serve`.
- `CSM_PORT`: default bind port for `serve`.
- `CSM_API_KEY`: optional sidecar API key.
- `CSM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY`: enables LLM-only memory extraction.
- `CSM_DEEPSEEK_MODEL`: default `deepseek-v4-flash`.
- `CSM_DEEPSEEK_BASE_URL`: default `https://api.deepseek.com`.
- `CSM_LLM_MAX_INPUT_CHARS`: local input-size guard before any API call.
- `CSM_LLM_MAX_OUTPUT_TOKENS`: max output tokens for extraction.

Before enabling live extraction, run:

```powershell
python -m csm_agent.cli deepseek-check "以后回答技术问题时，请先给结论。" --project demo
```

This builds the request and estimates input size locally. It does not call DeepSeek if no key is configured.

To verify a configured key and network path, use:

```powershell
python -m csm_agent.cli deepseek-probe
```

This still does not call DeepSeek. Add `--confirm-spend` only when you intentionally want to spend a tiny probe request. The probe uses a fixed prompt, `max_tokens=32`, and sends no user memory content.

Recommended integration rule: keep CSM as a sidecar. The host agent remains responsible for planning and tool execution; CSM only retrieves, writes, strengthens, replaces, archives, and reports memory.

Retrieval score fields:

- `semantic_similarity`: embedding semantic match between the query and the indexed memory text.
- `keyword_score`: SQLite FTS5 lexical match score when the query shares surface terms with memory content, summary, tags, or entities.
- `type_match_boost`: lightweight associative prior that pulls likely memory types into the candidate pool, for example name/call-me questions activating `core_identity`.
- `current_strength`: continuous memory strength after decay.
- `experience_activation`: learned activation from prior use, successful use, confirmation, contradiction, and volatility.
- `conflict_risk`: penalty for superseded, archived, or otherwise unsafe-to-inject memory.

Agent feedback loop:

1. Call `/pre_prompt` with the user's message.
2. Inject `memory_context` into the host agent prompt only if it is non-empty.
3. After the agent answers, call `/post_run` with the returned `memory_ids`.
4. CSM reinforces those used memories by increasing `use_count`, `activation_count`, `success_count`, strength, and last activation time.
