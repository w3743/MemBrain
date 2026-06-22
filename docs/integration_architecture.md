# CSM Agent Integration Architecture

## Core Idea

CSM should be integrated as a memory sidecar, not as a replacement for the agent planner.

Every host agent only needs four operations:

```python
retrieve(query, scope, budget) -> MemoryContext
observe(event) -> MemoryWritePlan
commit(plan, scope) -> list[Memory]
consolidate(scope) -> HealthReport
```

This keeps the memory system portable across OpenClaw, PiAgent, Hermes, or any other long-running agent runtime.

## Lifecycle

```text
user input
  -> host agent pre-run hook
  -> CSM.retrieve(...)
  -> inject compact memory_context only when CSM is confident
  -> agent planning / tool use / response
  -> host agent post-run hook
  -> CSM.observe(...) retrieves top-k related memories for write arbitration
  -> DeepSeek/LLM arbiter receives user_input + retrieved_memories
  -> CSM.commit(...)
  -> scheduled CSM.consolidate(...)
```

The post-run path deliberately avoids a separate LLM gate. CSM uses one LLM arbiter call for memory decisions. Local code performs only hard safety checks, retrieval, schema validation, and database execution.

The pre-run path is deliberately conservative. `ANSWER_INJECTION` memories directly affect the host agent's reply, so CSM should prefer returning an empty context over injecting weakly related memory. This prevents long-term identity, project, or preference memories from leaking into unrelated chats such as weather, casual remarks, or one-off questions.

## DeepSeek Cache Layout

DeepSeek requests are arranged for provider-side context-cache reuse:

```text
messages[0] fixed system: CSM arbiter identity
messages[1] fixed user: operation and memory type rules
messages[2] fixed user: output schema
messages[3] dynamic user: project_id, user_input, agent_output, tool_results, retrieved_memories
```

The stable prompt prefix should change only when the CSM prompt version changes. Dynamic values such as user input, retrieved memories, tool results, timestamps, and project state stay in the final message.

## Write Arbitration

Before committing memories, CSM retrieves a small top-k set of related active memories. The LLM can then emit:

- `ADD` for new durable facts.
- `UPDATE` when a retrieved memory should be refined.
- `SUPERSEDE` when a retrieved memory is outdated or contradicted.
- `ARCHIVE` when a retrieved memory should become inactive but auditable.
- `DELETE` only when the user explicitly asks to forget/remove it.
- `NOOP` for low-value or already represented information.

For `UPDATE`, `SUPERSEDE`, `ARCHIVE`, and `DELETE`, `target_id` must be one of the provided `retrieved_memories.id` values.

## PiAgent Hook

Use `PiAgentMemoryHook` when the host runtime exposes before/after lifecycle callbacks.

```python
from csm_agent import CSMEngine, CSMMemoryAdapter, PiAgentMemoryHook

engine = CSMEngine("piagent_memory.db")
hook = PiAgentMemoryHook(CSMMemoryAdapter(engine))

state = hook.before_agent_start(
    user_input="安装依赖用什么命令？",
    state={"user_id": "u1", "project_id": "my-project"},
)

# Add state["csm_memory_context"] to the agent prompt.

state = hook.agent_end(
    user_input="安装依赖用什么命令？",
    agent_output="使用 bun install。",
    state=state,
)
```

Recommended hook mapping:

- `before_agent_start`: retrieve L1/L2/L3 memories and inject a compact context block.
- `agent_end`: reinforce used memories and commit explicit new memories.
- scheduled task: run `engine.sleep_consolidate()`.

## OpenClaw Sidecar

Use `OpenClawMemorySidecar` when the host runtime is plugin-oriented, gateway-oriented, or easier to connect through HTTP.

Pre-prompt payload:

```json
{
  "user_id": "u1",
  "workspace_id": "openclaw-demo",
  "channel": "telegram",
  "message": "这个工作区向量后端用什么？",
  "budget_chars": 1400
}
```

Response:

```json
{
  "memory_context": "Relevant long-term memory:\\n- [L2 #1 ...]",
  "memory_ids": [1],
  "items": []
}
```

Post-run payload:

```json
{
  "user_id": "u1",
  "workspace_id": "openclaw-demo",
  "message": "记住这个工作区使用 sqlite-vec。",
  "agent_output": "已记录。",
  "memory_ids": [1],
  "explicit_memories": ["OpenClaw demo 工作区使用 sqlite-vec 作为向量后端。"]
}
```

This shape is intentionally transport-neutral. It can be exposed through an HTTP route, a local plugin call, or an internal service call.

## Hermes Provider

Use `HermesMemoryProvider` when the host runtime already has a memory provider abstraction.

```python
from csm_agent import CSMEngine, CSMMemoryAdapter, HermesMemoryProvider

provider = HermesMemoryProvider(CSMMemoryAdapter(CSMEngine("hermes_memory.db")))
provider.remember("Hermes 项目回答风格：简洁，避免无关解释。", user_id="u1", project_id="hermes")
context = provider.get_context("回答风格是什么？", user_id="u1", project_id="hermes")
```

CSM should output short memory context, not dump the whole memory database into the prompt.

## Scope Rules

Use scope to prevent memory leakage:

- `user_id`: owner of personal preferences and identity.
- `project_id` or `workspace_id`: project memory boundary.
- `channel`: useful for OpenClaw-style multi-channel agents.
- `session_id`: useful for tracing, but should not be the main long-term storage key.

If `project_id` is missing, the adapter can fall back to `channel:user_id`.

## What CSM Should Remember

Good candidates:

- Long-term user preferences.
- Stable project decisions.
- Tooling conventions.
- Repeated procedures.
- Superseded decisions and why they changed.
- High-value debugging lessons.

Poor candidates:

- One-time secrets or temporary credentials.
- Tool outputs that can be fetched from the source of truth.
- Full chat transcripts.
- Low-value small talk.
- Current project state that should come from Git, issue trackers, files, calendars, or APIs.

## Next Production Steps

1. Use local semantic retrieval everywhere: install `sentence-transformers`, set `CSM_EMBEDDING_BACKEND=local`, and set `CSM_EMBEDDING_MODEL` to a local BGE-large-zh-v1.5 directory such as `models\bge-large-zh-v1.5`; then run `python -m csm_agent.cli reindex-embeddings`.
2. For larger memory stores, add sqlite-vec or another ANN vector index after the local embedding quality is validated.
3. Configure `CSM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY` to enable `DeepSeekMemoryExtractor`.
4. Keep `MemorySecurityPolicy` enabled so sensitive content is labeled by sensitivity without destroying the original value.
5. Add real transport for OpenClaw if it expects HTTP or plugin RPC.
6. Expand `eval/` into a larger A/B evaluation set: no memory, vector RAG, CSM without sleep, CSM full.

## Evaluation Commands

```powershell
python -m csm_agent.cli eval-extractor
python -m csm_agent.cli eval-retrieval
python -m csm_agent.cli eval-e2e
python -m csm_agent.cli deepseek-check "以后回答技术问题时，请先给结论。" --project demo
python -m csm_agent.cli deepseek-probe
```

The current local fixtures cover memory extraction schema behavior, retrieval quality, stale-memory suppression, temporary-information handling, and multi-turn memory lifecycle behavior. Fixture extraction uses mock LLM output and does not call DeepSeek.

## HTTP Sidecar

Run:

```powershell
python -m csm_agent.cli --db csm_memory.db serve --host 127.0.0.1 --port 8765
```

Endpoints:

- `GET /health`
- `GET /openapi.json`
- `POST /pre_prompt`
- `POST /post_run`
- `POST /remember`
- `POST /context`
- `POST /sleep`

See `docs/api_contract.md` for the API contract and `examples/http_sidecar_payloads.md` for concrete payloads.
