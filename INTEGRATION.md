# CSM Agent ↔ pi Agent 集成文档

> **变更记录** — 本文档详细记录了将 CSM 连续强度记忆系统接入 pi coding agent 的全部工作。

---

## 📋 变更摘要

| 日期 | 变更内容 | 影响文件 |
|------|----------|----------|
| 2026-06-22 | **v0.2 架构简化重构** — 移除手工规则，让智能从简单算法涌现 | 全部 src/ 文件 |
| 2026-06-22 | 创建 pi 扩展，实现记忆检索/提取/强化闭环 | `pi-extension/csm-memory.ts` (新增) |
| 2026-06-22 | 编写集成文档 | `INTEGRATION.md` (新增，即本文件) |
| 2026-06-22 | 修复 SQL 注入风险、引擎复用、序号错误等 7 项 bug | store.py, server.py 等 |

### v0.2 简化内容

| 移除 | 原因 |
|------|------|
| `MemoryType` 枚举 (6 种预设类型) | 用自由标签 `tags` 替代，LLM 自主分类 |
| `importance` 字段 | 重要性 = 被检索次数 × 强度，自然涌现 |
| `confidence` 字段 | 置信度由后续验证行为体现 |
| `volatility`, `confirm_count`, `contradict_count`, `success_count` | 过度复杂的追踪，简化为 `access_count` |
| `DECAY_RATE_BY_TYPE` 字典 | 统一衰减率 0.02/天 |
| 检索中 7 个手工权重 | 简化为 `语义相似度 × 强度` |
| 中英文关键词→类型映射表 | 纯 embedding 语义匹配 |
| 固定 L1/L2/L3 阈值 (0.8/0.4/0.1) | 动态百分位阈值（前 20%/60%/90%） |
| 大段 LLM 操作规则提示词 | 简短自然语言描述意图 |

### 新增

| 新增 | 说明 |
|------|------|
| 自由标签 `tags` | 替代固定 MemoryType |
| `access_count` | 涌现"重要性"的核心字段 |
| 动态百分位分层 | `compute_layer_thresholds()` |
| 本地语义嵌入 | 统一使用本地 BGE-large-zh-v1.5，不再回退 hash |

---

## 🏗 架构

```
┌──────────────────────────────────┐
│  pi Agent (Node.js/TypeScript)    │
│                                   │
│  ~/.pi/agent/extensions/          │
│       csm-memory.ts  ◄── 本扩展  │
│                                   │
│  生命周期钩子：                    │
│    before_agent_start ───┐        │
│    agent_end          ───┤        │
│    session_shutdown   ───┤        │
│                          │        │
│  命令：                   │        │
│    /remember <内容>      │        │
│    /csm-health          │        │
│    /csm-search <查询>    │        │
└──────────────┬───────────┘        │
               │ HTTP               │
               ▼                    │
┌──────────────────────────────┐    │
│  CSM Sidecar (Python)         │    │
│  python -m csm_agent.cli      │    │
│       serve                   │    │
│                               │    │
│  POST /pre_prompt  检索记忆   │◄───┘
│  POST /post_run    观察+写入  │
│  POST /remember    显式记忆   │
│  POST /sleep       睡眠整理   │
│  GET  /admin/health 健康报告 │
│                               │
│  SQLite 数据库：               │
│  ~/.pi/agent/csm_memory.db    │
└──────────────────────────────┘
```

### 数据流时序

```
用户输入 "我的名字叫王家裕"
  │
  ▼
┌─ before_agent_start ─────────────────────────────┐
│  1. 计算 project_id = sha256(cwd)[:16]           │
│  2. POST /pre_prompt { message, workspace_id }   │
│  3. CSM 检索相关记忆 → memory_context            │
│  4. 注入到 system prompt 末尾                    │
│  5. 保存 memory_ids 供后续强化                   │
└──────────────────────────────────────────────────┘
  │
  ▼
  Agent 处理请求（LLM 调用、工具执行）
  │
  ▼
┌─ agent_end ──────────────────────────────────────┐
│  1. 提取 user_input + agent_output               │
│  2. POST /post_run { message, agent_output,      │
│       memory_ids }                               │
│  3. CSM DeepSeek 仲裁器分析是否需要写入记忆       │
│  4. 若需要：ADD / UPDATE / SUPERSEDE / NOOP      │
│  5. 传回的 memory_ids 被强化（+use_count 等）    │
└──────────────────────────────────────────────────┘
  │
  ▼
下次对话时，CSM 会检索到已存储的记忆（如 "用户名叫王家裕"）
```

---

## 🚀 安装指南

### 前置条件

- Node.js 18+ (pi 运行环境)
- Python 3.10+ (CSM sidecar 运行环境)
- CSM 项目位于 `C:\Users\wangj\Desktop\1`

### 方式一：保持扩展在 CSM 项目内（推荐）

扩展文件已创建在项目的 `pi-extension/` 目录中，无需移动：

```powershell
# 1. 设置环境变量（添加到 $PROFILE 或系统环境变量）
[Environment]::SetEnvironmentVariable("CSM_PROJECT_DIR", "C:\Users\wangj\Desktop\1", "User")

# 2. 启动 pi 时加载扩展
pi -e C:\Users\wangj\Desktop\1\pi-extension\csm-memory.ts
```

或者创建符号链接实现自动加载：

```powershell
# 创建符号链接到 pi 自动发现目录
New-Item -ItemType SymbolicLink `
  -Path "$env:USERPROFILE\.pi\agent\extensions\csm-memory.ts" `
  -Target "C:\Users\wangj\Desktop\1\pi-extension\csm-memory.ts" `
  -Force
```

设置环境变量后（见上），直接 `pi` 即可自动加载。

### 方式二：复制扩展文件

```powershell
# 1. 复制扩展
Copy-Item C:\Users\wangj\Desktop\1\pi-extension\csm-memory.ts `
  $env:USERPROFILE\.pi\agent\extensions\csm-memory.ts

# 2. 设置环境变量（必须！否则扩展找不到 CSM 项目）
[Environment]::SetEnvironmentVariable("CSM_PROJECT_DIR", "C:\Users\wangj\Desktop\1", "User")
```

### 配置 DeepSeek API（启用自动记忆提取）

```powershell
# 方式1：设置环境变量
$env:CSM_DEEPSEEK_API_KEY = "sk-your-key"

# 方式2：使用管理界面
# 启动 sidecar 后访问 http://127.0.0.1:19876/admin
# 在 "LLM 接入" 页面配置
```

> **不配置 DeepSeek API Key 也可以使用**：检索功能和 `/remember` 手动存入仍然正常工作，只是无法自动从对话中提取记忆。

---

## 📖 使用说明

### 自动记忆流程

1. 正常与 pi 对话，无需任何额外操作
2. CSM 在后台自动检索相关历史记忆，注入到 AI 上下文中
3. 每轮对话结束后，CSM 自动分析是否需要提取新的持久记忆
4. 被使用的记忆会被自动强化

### 用户命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `/remember <内容>` | 手动存入一条记忆 | `/remember 用户偏好使用 pnpm 管理依赖` |
| `/csm-health` | 查看记忆库健康状态 | `/csm-health` |
| `/csm-search <查询>` | 搜索记忆库 | `/csm-search 依赖管理` |

### 记忆的类型

| 类型 | 说明 | 衰减速度 |
|------|------|----------|
| `core_identity` | 用户名、角色等核心身份 | 极慢 (0.001/天) |
| `long_term_preference` | 长期偏好（语言、风格、工具） | 很慢 (0.005/天) |
| `project_memory` | 项目决策、约束、架构 | 中等 (0.03/天) |
| `procedural_memory` | 可复用的步骤、命令 | 较慢 (0.01/天) |
| `episodic_memory` | 有意义的事件 | 较快 (0.06/天) |
| `temporary_chat` | 临时信息 | 很快 (0.15/天) |

### 记忆强度层级

| 层级 | 强度范围 | 说明 |
|------|----------|------|
| L1 | ≥ 0.8 | 高价值核心记忆 |
| L2 | 0.4 - 0.8 | 活跃的常规记忆 |
| L3 | 0.1 - 0.4 | 弱化的边缘记忆 |
| COLD | < 0.1 | 将被睡眠整理归档 |

---

## ⚙ 配置参考

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CSM_PROJECT_DIR` | 自动检测 | CSM 项目根目录 |
| `CSM_PORT` | 19876 | sidecar HTTP 端口 |
| `CSM_DB` | `~/.pi/agent/csm_memory.db` | SQLite 数据库路径 |
| `CSM_DEEPSEEK_API_KEY` | (空) | DeepSeek API 密钥 |
| `DEEPSEEK_API_KEY` | (空) | 同上（备选变量名） |
| `CSM_EMBEDDING_BACKEND` | `local` | 向量后端，运行时统一使用本地 BGE |
| `CSM_EMBEDDING_MODEL` | `C:\Users\wangj\Desktop\1\models\bge-large-zh-v1.5` | 本地 BGE-large-zh-v1.5 模型目录 |

### 本地语义向量

```powershell
# 安装依赖
pip install sentence-transformers

# 设置环境变量。pi 扩展和管理台脚本会自动设置；手动运行 CLI 时可显式设置。
$env:CSM_EMBEDDING_BACKEND = "local"
$env:CSM_EMBEDDING_MODEL = "C:\Users\wangj\Desktop\1\models\bge-large-zh-v1.5"

# 重建向量索引（在 CSM 项目目录执行）
cd C:\Users\wangj\Desktop\1
$env:PYTHONPATH = "src"
python -m csm_agent.cli --db $env:USERPROFILE\.pi\agent\csm_memory.db reindex-embeddings
```

---

## 📁 文件清单

```
C:\Users\wangj\Desktop\1\
├── pi-extension/
│   └── csm-memory.ts          ★ 新增：pi 扩展（约 350 行 TypeScript）
├── INTEGRATION.md              ★ 新增：本集成文档
├── src/csm_agent/              (未修改)
│   ├── server.py               (未修改)
│   ├── engine.py               (未修改)
│   ├── adapters.py             (未修改)
│   └── ...                     (均未修改)
```

---

## 🔧 故障排查

### sidecar 无法启动

```powershell
# 1. 检查 Python 是否可用
python --version

# 2. 手动启动 sidecar 查看错误
cd C:\Users\wangj\Desktop\1
$env:PYTHONPATH = "src"
python -m csm_agent.cli --db $env:USERPROFILE\.pi\agent\csm_memory.db serve --port 19876

# 3. 检查端口是否被占用
netstat -ano | findstr 19876
```

### 自动记忆提取不工作

```powershell
# 检查 DeepSeek API 配置
cd C:\Users\wangj\Desktop\1
$env:PYTHONPATH = "src"
python -m csm_agent.cli deepseek-check "测试内容" --project test
```

### 查看记忆库内容

在 sidecar 运行后访问管理界面：

```
http://127.0.0.1:19876/admin
```

或在 CLI 中直接查询：

```powershell
cd C:\Users\wangj\Desktop\1
$env:PYTHONPATH = "src"
python -m csm_agent.cli --db $env:USERPROFILE\.pi\agent\csm_memory.db search "关键词"
```

---

## 🧪 验证集成

### 端到端测试

```powershell
# 1. 启动 sidecar
cd C:\Users\wangj\Desktop\1
$env:PYTHONPATH = "src"
Start-Process python -ArgumentList "-m","csm_agent.cli","--db","$env:USERPROFILE\.pi\agent\csm_memory.db","serve","--port","19876"

# 2. 测试手动存入记忆
curl -X POST http://127.0.0.1:19876/remember `
  -H "Content-Type: application/json" `
  -d '{"user_id":"pi-user","project_id":"test","content":"用户偏好简洁中文回答"}'

# 3. 测试检索
curl -X POST http://127.0.0.1:19876/pre_prompt `
  -H "Content-Type: application/json" `
  -d '{"user_id":"pi-user","workspace_id":"test","message":"回答风格是什么？"}'

# 4. 测试观测+写入
curl -X POST http://127.0.0.1:19876/post_run `
  -H "Content-Type: application/json" `
  -d '{"user_id":"pi-user","workspace_id":"test","message":"我的名字叫王家裕","agent_output":"好的，我记住了，你叫王家裕。","memory_ids":[]}'

# 5. 查看健康状态
curl http://127.0.0.1:19876/admin/health
```

### pi 中验证

```powershell
# 启动 pi 并加载扩展
pi -e C:\Users\wangj\Desktop\1\pi-extension\csm-memory.ts

# 在 pi 中测试：
/remember 用户偏好：回答技术问题时先给结论，再给必要步骤
/csm-health
/csm-search 回答风格
```

---

## 🔄 后续升级建议

1. **多用户支持**：将 `user_id` 从固定的 `"pi-user"` 改为系统用户名
2. **记忆可视化**：在 pi 中集成简易的记忆浏览 UI
3. **会话级记忆隔离**：利用 CSM 的 `session_id` 字段追踪对话来源
4. **记忆冲突检测**：当新旧记忆矛盾时，提醒用户确认
5. **定期睡眠整理**：添加 pi 定时任务，自动归档过期记忆
6. **向量后端升级**：启用 `sentence-transformers` 获得更好的语义检索

---

> **设计原则**：CSM 作为 memory sidecar，不修改 pi 核心代码。pi 负责规划和工具执行，CSM 只负责记忆的检索、写入、强化和报告。
