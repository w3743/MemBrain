# 类脑记忆 — LLM Agent 记忆引擎

<p align="center">
  <em>为 pi coding agent 设计的类脑持久化记忆子系统</em>
</p>

---

## 这是什么

MB（MemBrain / 类脑记忆）是 pi coding agent 的记忆后端，让 pi 能记住跨会话的用户偏好、项目约定和纠正历史。

技术方案：每条记忆以指数曲线衰减（模拟遗忘），被检索并使用时按 FSRS 间隔效应强化（使用间隔越久，强化收益越大）。DeepSeek LLM 自动从对话中提取值得记住的内容。

```
存储: SQLite + FTS5   检索: BGE 语义向量 + 关键词混合
衰减: R(t) = s₀·e^(-d·t)     强化: FSRS 间隔效应
仲裁: DeepSeek LLM 自动提取   进化: used/ignored/corrected 反馈自适应
```

核心依赖只有 Python 标准库。`sentence-transformers` 可选（提供语义检索，不装也能用关键词检索）。

---

## 在 pi agent 中使用

### 1. 安装 Python 后端

```bash
pip install git+https://github.com/w3743/CSM.git
pip install "membrain[local-embedding]@git+https://github.com/w3743/CSM.git"  # 含语义向量
```

### 2. 安装 pi 扩展

```bash
pi install git:github.com/w3743/CSM.git
```

### 3. 配置 DeepSeek

```bash
set DEEPSEEK_API_KEY=sk-xxx
```

### 4. 启动 pi

```bash
pi
```

pi 启动时扩展自动拉起 CSM sidecar，之后每次对话：
- 提问前 → 检索相关记忆，注入到系统提示
- 回答后 → DeepSeek 仲裁器分析对话，提取新记忆

### 手动管理

```bash
membrain serve           # 单独启动 Sidecar + Web 控制台
membrain health           # 查看健康报告
```

浏览器打开 `http://127.0.0.1:8765/admin` 进入管理控制台。

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

## 命令行

```bash
membrain serve                           # 启动 Sidecar
membrain add "内容" --project demo        # 手动存入
membrain search "查询" --project demo     # 检索
membrain sleep                            # 睡眠整理
membrain health                           # 健康报告
membrain demo                             # 演示
membrain eval-all                         # 完整评测
```

---

## HTTP API

| 端点 | 用途 |
|------|------|
| `/pre_prompt` | pi 提问前检索记忆 |
| `/post_run` | pi 回答后提取记忆 |
| `/remember` | 手动存入 |
| `/sleep` | 触发睡眠整理 |
| `/health` | 健康检查 |
| `/admin` | Web 管理控制台 |

---

## 项目结构

```
├── src/membrain/        # 核心代码（14 个模块）
│   ├── engine.py         # 记忆生命周期
│   ├── strength.py       # 强度模型（FSRS 风格）
│   ├── store.py          # SQLite + FTS5
│   ├── retrieval.py      # 混合检索
│   ├── evolution.py      # 自适应进化
│   ├── embedding.py      # BGE 嵌入
│   ├── extractor.py      # DeepSeek 仲裁器
│   ├── adapters.py       # pi/OpenClaw/Hermes 适配
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
