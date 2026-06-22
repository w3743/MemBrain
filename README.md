# CSM Agent MVP

This repository implements the MVP described in `类脑连续强度记忆Agent研究方案_完善版.docx`.

It provides a small, runnable Continuous Strength Memory subsystem:

- SQLite persistence with FTS5 keyword search.
- Local `sentence-transformers` embedding with `BAAI/bge-large-zh-v1.5` as the required semantic backend.
- `memory_strength` exponential decay and reinforcement.
- Dynamic L1/L2/L3 layer resolution.
- ADD, UPDATE, SUPERSEDE, NOOP, ARCHIVE and DELETE operations.
- DeepSeek/LLM-only Memory Extractor with JSON schema validation.
- Cache-friendly DeepSeek arbiter prompts: fixed CSM rules/schema are sent before dynamic task input to improve provider-side context cache hits.
- Retrieved-memory arbitration: post-run extraction receives top-k existing memories so the LLM can choose UPDATE, SUPERSEDE, ARCHIVE, or DELETE with a concrete `target_id`.
- JSON schema validation and sensitive-data guardrails for LLM-backed extractor integration.
- JSONL extraction, retrieval, and end-to-end evaluation fixtures with CLI evaluators.
- Hybrid retrieval with final-score reranking.
- Separate retrieval modes for answer injection and write arbitration, with type-aware boosts and duplicate suppression.
- Sleep consolidation and memory health reporting.
- Adapter layer for PiAgent hooks, OpenClaw sidecar payloads, and Hermes-style memory providers.
- Zero-dependency HTTP sidecar service for external agent runtimes.
- OpenAPI contract endpoint at `/openapi.json`.
- Built-in Chinese CSM Memory Console at `/admin` with dashboard, memory browser, retrieval lab, and arbitration lab.
- A CLI demo and automated tests.

## Quick Start

```powershell
set PYTHONPATH=src;tests
python tests\run_tests.py
python -m csm_agent.cli demo
```

Create a memory database:

```powershell
python -m csm_agent.cli add "用户偏好简洁回答" --type long_term_preference --project demo
python -m csm_agent.cli search "解释概念时应该什么风格" --project demo
python -m csm_agent.cli extract "以后回答技术问题时，请先给结论，再给必要步骤。" --project demo
python -m csm_agent.cli deepseek-check "以后回答技术问题时，请先给结论。" --project demo
python -m csm_agent.cli deepseek-probe
python -m csm_agent.cli embedding-info
python -m csm_agent.cli reindex-embeddings
python -m csm_agent.cli eval-extractor
python -m csm_agent.cli eval-retrieval
python -m csm_agent.cli eval-e2e
python -m csm_agent.cli serve --host 127.0.0.1 --port 8765
python -m csm_agent.cli sleep
python -m csm_agent.cli health
```

Open the management console after starting the sidecar:

```text
http://127.0.0.1:8765/admin
```

For sidecar deployments, set `CSM_API_KEY` or pass `serve --api-key ...` to require `X-CSM-API-Key` or `Authorization: Bearer ...` on POST endpoints.
For live LLM extraction, set `CSM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY`. Run `deepseek-check` first; it validates the request locally and reports whether an API call would be made. `deepseek-probe` also does not call the API unless you add `--confirm-spend`; with that flag it sends one tiny fixed JSON probe and no user content.

By default the CLI writes to `csm_memory.db` in the current directory. Use `--db path\to\file.db` to choose another database.

## Project Layout

- `src/csm_agent/`: core implementation.
- `tests/`: regression tests for strength, retrieval, supersede and sleep.
- `eval/`: extractor, retrieval, and end-to-end evaluation fixtures.
- `examples/http_sidecar_payloads.md`: HTTP payload examples for OpenClaw/Hermes-style integration.
- `docs/api_contract.md`: HTTP API contract summary.
- `docs/deliverable.md`: project completion notes and acceptance checklist.
- `docs/integration_architecture.md`: integration model for OpenClaw, PiAgent and Hermes.

## MVP Boundary

The semantic embedding backend is local BGE-large-zh-v1.5. The project should fail loudly if local embeddings are unavailable instead of silently downgrading retrieval quality.

```powershell
set CSM_EMBEDDING_BACKEND=local
set CSM_EMBEDDING_MODEL=C:\Users\wangj\Desktop\1\models\bge-large-zh-v1.5
python -m csm_agent.cli reindex-embeddings
```

If the local model directory is missing, install `sentence-transformers` and download `BAAI/bge-large-zh-v1.5` into `models\bge-large-zh-v1.5` before running the sidecar:

```powershell
pip install sentence-transformers
```

Memory extraction is LLM-only: set `CSM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY` to enable DeepSeek extraction. Without a key, extraction returns NOOP and does not call any API. Use `deepseek-check` to inspect request size and JSON payload locally before spending tokens.

The extractor intentionally uses a single LLM arbiter call instead of an LLM gate. Local code handles hard safety limits, retrieval, schema validation, and execution. The LLM receives retrieved memories only in the final dynamic message, while stable CSM rules and output schema remain in earlier fixed messages to improve DeepSeek context-cache locality.
