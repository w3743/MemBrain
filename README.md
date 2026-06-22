# 🧠 MemBrain — LLM Agent 记忆引擎

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/License-GPL%20v3-green" alt="License">
  <br>
  <em>类脑持久化记忆子系统 · BGE 语义检索 · FSRS 间隔强化 · DeepSeek 自动提取</em>
</p>

---

## ⚡ 5 分钟快速开始

```bash
# 1. 安装
pip install git+https://github.com/w3743/MemBrain.git

# 2. 启动（核心依赖仅 Python 标准库）
membrain serve

# 3. 浏览器打开 Web 控制台
# http://127.0.0.1:8765/admin
```

> 💡 `sentence-transformers` 可选——安装后启用 BGE 语义向量检索；不装也能用关键词检索。

---

## 这是什么

MB（MemBrain / 类脑记忆）是为 LLM Agent 设计的持久化记忆引擎。它让 Agent 能记住跨会话的用户偏好、项目约定和纠正历史。

```
存储: SQLite + FTS5   检索: BGE 语义向量 + BM25 关键词混合
衰减: R(t) = s₀·e^(-d·t)     强化: FSRS 间隔效应
仲裁: DeepSeek LLM 自动提取   进化: used/ignored/corrected 反馈自适应
```

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🧬 **类脑衰减** | 指数遗忘曲线 R(t) = s₀·e^(-d·t)，模拟人脑记忆消退 |
| 🔁 **FSRS 强化** | 被检索使用的记忆按间隔效应增强——越久没用的记忆被重新引用时增益越大 |
| 🎯 **混合检索** | BGE-large-zh-v1.5 稠密向量 + FTS5 BM25 稀疏检索，语义与精确兼顾 |
| 🤖 **LLM 仲裁** | DeepSeek 自动分析对话，决定提取/更新/合并/忽略/纠正记忆 |
| 📊 **L1/L2/L3 分层** | 动态百分位分层，优先级自适应调整 |
| 🔄 **进化反馈** | used / ignored / corrected 三元反馈驱动记忆质量持续优化 |
| 🌐 **HTTP Sidecar** | 独立 HTTP 服务，适配 pi / LangChain / 任意 Agent 框架 |
| 🖥️ **Web 控制台** | 中文管理界面，可视化管理记忆、查看健康报告 |

### 与同类方案对比

| | MemBrain | Mem0 | Letta (MemGPT) |
|------|:--:|:--:|:--:|
| 记忆衰减模型 | ✅ 指数衰减+FSRS强化 | ❌ | ❌ |
| 进化反馈 | ✅ used/ignored/corrected | ❌ | ❌ |
| 检索方式 | BGE稠密 + BM25稀疏 | Embedding | Embedding |
| LLM 自动提取 | ✅ DeepSeek 仲裁 | ✅ | ✅ |
| Web 控制台 | ✅ 中文界面 | ❌ | ❌ |
| 核心依赖 | Python 标准库 | 多重依赖 | 多重依赖 |

---

## 记忆算法概要

### 强度模型

| 操作 | 公式 | 说明 |
|------|------|------|
| 衰减 | R = s₀ · e^(-d · t) | 每天衰减，d 默认 0.02 |
| 强化 | g = 0.15 · (1-R)^1.4 · (1-trust) | FSRS 间隔效应，R 越低增益越大 |
| 初始值 | strength=0.6, decay_rate=0.02 | 新记忆强度 |

### 检索模型

```
score = 语义相似度 × 当前强度 R × (1 + boost)
```

被纠正的记忆 boost 为负，自动降权。

### 进化反馈

| 反馈 | 行为 |
|------|------|
| used（被 LLM 引用） | boost +0.05, trust 略增, 强化 |
| ignored（检索了但没用） | boost 微降，R 越高惩罚越大 |
| corrected（被用户纠正） | boost -0.2, trust ×0.7, decay ×1.5 |

### 归档

睡眠整理时，R < 0.01（1% 可回忆概率）的记忆自动归档，不再参与检索。

---

## 📦 安装

### 基础安装（关键词检索）

```bash
pip install git+https://github.com/w3743/MemBrain.git
```

### 完整安装（含 BGE 语义向量）

```bash
pip install "membrain[local-embedding]@git+https://github.com/w3743/MemBrain.git"
```

### 配置 DeepSeek

```bash
set DEEPSEEK_API_KEY=sk-xxx
```

---

## 🚀 在 pi agent 中使用

```bash
# 安装 pi 扩展
pi install git:github.com/w3743/MemBrain.git

# 启动 pi（扩展自动拉起 sidecar）
pi
```

每次对话：
- **提问前** → 自动检索相关记忆，注入到系统提示
- **回答后** → DeepSeek 仲裁器分析对话，提取新记忆

---

## 📋 命令行

```bash
membrain serve                           # 启动 Sidecar + Web 控制台
membrain add "内容" --project demo        # 手动存入记忆
membrain search "查询" --project demo     # 检索记忆
membrain sleep                            # 触发睡眠整理
membrain health                           # 健康报告
membrain demo                             # 运行演示
membrain eval-all                         # 完整评测
```

---

## 🌐 HTTP API

| 端点 | 用途 |
|------|------|
| `/pre_prompt` | 提问前检索记忆 |
| `/post_run` | 回答后提取记忆 |
| `/remember` | 手动存入 |
| `/sleep` | 触发睡眠整理 |
| `/health` | 健康检查 |
| `/admin` | Web 管理控制台 |

---

## 📁 项目结构

```
├── src/membrain/        # 核心代码（14 个模块）
│   ├── engine.py         # 记忆生命周期
│   ├── strength.py       # 强度模型（FSRS 风格）
│   ├── store.py          # SQLite + FTS5
│   ├── retrieval.py      # 混合检索（稠密+稀疏）
│   ├── evolution.py      # 自适应进化
│   ├── embedding.py      # BGE 嵌入
│   ├── extractor.py      # DeepSeek 仲裁器
│   ├── adapters.py       # Agent 框架适配器
│   ├── server.py         # HTTP Sidecar + 控制台
│   └── ...
├── pi-extension/         # pi Agent 扩展
├── tests/                # 测试
├── eval/                 # 评测用例
└── docs/                 # 文档
```

---

## 许可

GPL v3
