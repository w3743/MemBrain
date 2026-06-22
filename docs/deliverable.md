# CSM Agent MVP 交付说明

## 已完成范围

本项目已按研究方案完成最小可运行原型：

1. 记忆对象模型：包含内容、类型、scope、project_id、强度、质量、索引和 supersede 字段。
2. 连续强度模型：实现 `current_strength = base_strength * exp(-decay_rate * elapsed_days)`。
3. 强化机制：有效使用后提升 `base_strength`、更新 `last_used_at` 和 `use_count`。
4. 动态分层：按强度解析为 L1、L2、L3、COLD。
5. SQLite 存储：结构化字段、FTS5 关键词索引、关系链表。
6. 混合检索：关键词召回、本地 BGE-large-zh-v1.5 语义向量、强度、重要性、项目相关性、置信度和冲突风险重排。
7. 记忆操作：ADD、UPDATE、SUPERSEDE、NOOP、ARCHIVE、DELETE。
8. DeepSeek/LLM-only Memory Extractor：移除本地规则抽取器，抽取任务由 LLM 输出结构化 JSON 写入计划。
9. JSON extractor 接口：提供 schema 校验、dry-run 检查、输入长度限制和重复请求缓存，避免无效 token 消耗。
10. 敏感信息策略：保留邮箱、token、key 等关键原文，只做 `sensitivity` 标注，不默认脱敏或拒绝。
11. 睡眠整理：归档低强度低价值记忆，并输出层级统计。
12. 健康报告：输出状态、类型、层级分布和高价值弱化记忆清单。
13. CLI 演示：支持 add、search、extract、eval-extractor、supersede、sleep、health、demo。
14. 接入适配层：提供 PiAgent hook、OpenClaw sidecar payload 和 Hermes provider 三种集成外壳。
15. HTTP sidecar：提供 `/health`、`/openapi.json`、`/pre_prompt`、`/post_run`、`/remember`、`/context`、`/sleep` 外部服务入口。
16. 图形化管理界面：提供 `/admin` CSM 记忆控制台，包含总览、记忆库、检索实验室、仲裁实验室。
17. sidecar 安全：支持可选 API key，POST 端点可通过 `X-CSM-API-Key` 或 `Authorization: Bearer` 保护。
18. 打包配置：补充 `pyproject.toml` build-system、src-layout package discovery 和 console script。
19. 评测体系：提供抽取、检索和端到端 JSONL fixture，输出 accuracy、Recall@k、Precision@k、污染率和过期引用率。
20. 自动测试与评测：覆盖强度衰减、强化、检索、替换链、睡眠归档、记忆抽取、敏感信息策略、评测集、三类集成适配器和 HTTP sidecar。

## 验收命令

```powershell
set PYTHONPATH=src;tests
python tests\run_tests.py
python -m csm_agent.cli demo
python -m csm_agent.cli deepseek-check "以后回答技术问题时，请先给结论。" --project demo
python -m csm_agent.cli deepseek-probe
python -m csm_agent.cli eval-extractor
python -m csm_agent.cli eval-retrieval
python -m csm_agent.cli eval-e2e
python -m csm_agent.cli serve --host 127.0.0.1 --port 8765
```

当前验证结果：标准 pytest 通过 33 项测试；mock LLM 抽取评测 5/5；检索评测 Recall@k = 1.0、forbidden_hit_rate = 0.0；端到端评测 accuracy = 1.0、memory_pollution_rate = 0.0、stale_reference_rate = 0.0。

## 后续升级建议

- 运行时统一使用本地 `sentence-transformers` 后端 `BAAI/bge-large-zh-v1.5`；模型或依赖缺失时应直接修复环境，不再静默回退 hash。
- 提供 DeepSeek API key 后，可用 `CSM_DEEPSEEK_API_KEY` 或 `DEEPSEEK_API_KEY` 启用真实 LLM 抽取；接入前先用 `deepseek-check` 做本地请求与 token 估算检查，再用 `deepseek-probe --confirm-spend` 做固定小请求连通性验证。
- 在 Pi Agent 中把 `before_agent_start`、`agent_end` 和 sleep task 封装为 hook。
- 参考 `docs/integration_architecture.md` 将 CSM 作为 memory sidecar 接入 OpenClaw、PiAgent 或 Hermes。
- 扩展评测集，形成长期偏好、项目状态、冲突替换、临时信息污染、多跳记忆推理五类自动化用例。
