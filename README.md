# CSM — 连续强度记忆 (Continuous Strength Memory)

<p align="center">
  <em>简单算法中涌现智能 — 为 LLM Agent 而生的长期记忆子系统</em>
</p>

---

## 什么是 CSM

CSM 是一个**零外部依赖的 Python 记忆引擎**，为 AI 编码助手提供跨会话持久化记忆能力。

核心理念：两条简单规则反复作用，涌现出智能的记忆行为——

```
检索得分 = 语义相似度 × 当前强度 × (1 + 经验偏置)
强度变化 = 指数衰减（遗忘）+ 间隔强化（使用越多、忘得越慢）
```

**“你不用告诉我该怎么记，我自己会学。”**

---

## 快速开始

### 安装

```bash
# 基础安装（SQLite + FTS5 关键词检索）
pip install git+https://github.com/w3743/CSM.git

# 完整安装（含 BGE 语义向量，推荐）
pip install "csm-agent[local-embedding]@git+https://github.com/w3743/CSM.git"

# 中国用户：设置 HuggingFace 镜像加速模型下载
set HF_ENDPOINT=https://hf-mirror.com
```

### 配置 DeepSeek LLM 仲裁器

```bash
set DEEPSEEK_API_KEY=sk-xxx
```

不配也能用——只是退化为关键词检索，不会自动提取记忆。

### 启动

```bash
csm-agent serve
```

浏览器打开 `http://127.0.0.1:8765/admin` 进入中文管理控制台。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| SQLite + FTS5 持久化 | 零外部依赖，WAL 模式，支持全文检索 |
| BGE-large-zh-v1.5 语义向量 | 1024 维本地嵌入，中英文混合检索 |
| 指数衰减 + 间隔强化 | FSRS 风格强度模型，复习越晚增益越大 |
| 动态 L1/L2/L3 百分位分层 | 自适应阈值，不是硬编码 |
| DeepSeek LLM 仲裁器 | 自动判断值得记住的内容，支持 ADD/UPDATE/SUPERSEDE/ARCHIVE/DELETE |
| 自适应进化引擎 | 每条记忆独立调整 decay_rate、boost、trust |
| HTTP Sidecar 服务 | 标准 REST API，支持 pi/OpenClaw/Hermes 集成 |
| 中文 Web 管理控制台 | 总览/记忆库/检索实验/仲裁实验/LLM 配置 |
| OpenAPI 规范 | `/openapi.json` 完整接口文档 |

---

## 记忆生命周期

```
存入 (strength=0.6)
  │
  ▼
语义检索 ← 每次对话前自动触发
  │
  ├─ 被 LLM 引用 → reinforce()：间隔越久增益越大，decay_rate 降低
  ├─ 被检索但未引用 → apply_feedback("ignored")：轻度降权
  └─ 被用户纠正 → apply_feedback("corrected")：快速衰减
  │
  ▼
睡眠整理 → R < 0.01 自动归档
```

---

## 架构

```
pi agent ←→ pi-extension/csm-memory.ts ← HTTP → CSM Python sidecar
                                                    │
                                          ┌─────────┼─────────┐
                                     MemoryStore   CSMEngine  EvolutionEngine
                                      (SQLite)    (检索/强化)  (自适应)
                                          │
                                    BGE Embedding ← DeepSeek LLM
```

---

## CLI 命令

```bash
csm-agent serve          # 启动 HTTP Sidecar + Web 控制台
csm-agent add "内容"      # 手动存入记忆
csm-agent search "查询"   # 检索记忆
csm-agent sleep           # 睡眠整理（归档弱记忆）
csm-agent health          # 健康报告
csm-agent demo            # 运行演示

# 评测
csm-agent eval-all                    # 完整评测套件
csm-agent eval-extractor              # LLM 提取器评测
csm-agent eval-retrieval              # 检索评测
csm-agent eval-e2e                    # 端到端评测
csm-agent eval-strength               # 强度模型评测
csm-agent eval-embedding              # 嵌入质量评测
```

---

## HTTP API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/pre_prompt` | POST | 检索记忆上下文（Agent 运行前） |
| `/post_run` | POST | 观察 Agent 交互，提取记忆 |
| `/remember` | POST | 手动存入一条记忆 |
| `/context` | POST | Hermes 风格上下文检索 |
| `/sleep` | POST | 触发睡眠整理 |
| `/openapi.json` | GET | OpenAPI 规范 |
| `/admin` | GET | Web 管理控制台 |

---

## 项目结构

```
CSM/
├── src/csm_agent/        # 核心代码（14 个模块）
│   ├── engine.py         # 引擎：检索/强化/睡眠
│   ├── store.py          # SQLite 存储层 + FTS5
│   ├── strength.py       # 强度模型（FSRS 风格）
│   ├── retrieval.py      # 混合检索器
│   ├── evolution.py      # 自适应进化引擎
│   ├── embedding.py      # BGE 嵌入后端
│   ├── extractor.py      # DeepSeek LLM 仲裁器
│   ├── adapters.py       # 集成适配层（pi/OpenClaw/Hermes）
│   ├── server.py         # HTTP Sidecar + Web 控制台
│   ├── security.py       # 安全策略（敏感度标注）
│   ├── models.py         # 数据模型
│   ├── cli.py            # 命令行接口
│   ├── evaluation.py     # 评测体系
│   ├── llm_config.py     # LLM 配置管理
│   └── api_contract.py   # OpenAPI 契约
├── tests/                # 测试用例
├── eval/                 # 评测数据
├── pi-extension/         # pi Agent 集成扩展
├── docs/                 # 设计文档
├── install.bat           # Windows 一键安装
├── pyproject.toml        # pip 安装配置
└── package.json          # pi 包定义
```

---

## 谁在用

- **[pi coding agent](https://github.com/earendil-works/pi-coding-agent)** — 通过 `pi-extension/csm-memory.ts` 自动集成

---

## 许可

MIT © CSM Agent Project
