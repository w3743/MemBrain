/**
 * CSM Memory Extension for Pi Coding Agent
 * ─────────────────────────────────────────
 * 为 pi agent 提供跨对话持久化记忆能力。
 *
 * 工作原理：
 *   1. 启动时自动拉起 CSM Python sidecar 子进程
 *   2. 每次用户提问前，检索相关历史记忆并注入到 system prompt
 *   3. 每次回答后，观察交互内容，由 DeepSeek LLM 仲裁器决定是否写入新记忆
 *   4. pi 退出时自动关闭 sidecar
 *
 * 命令：
 *   /remember <内容>  手动存入一条记忆
 *   /csm-health        查看记忆健康报告
 *   /csm-search <查询> 搜索记忆库
 *
 * 环境变量：
 *   CSM_PROJECT_DIR     CSM 项目根目录（默认：本文件所在目录的上级）
 *   CSM_PORT            sidecar 端口（默认：19876）
 *   CSM_DB              数据库路径（默认：~/.pi/agent/csm_memory.db）
 *   CSM_EMBEDDING_MODEL 本地 BGE 模型路径（默认：<项目>/models/bge-large-zh-v1.5）
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

// ═══════════════════════════════════════════════════════════════════
// 配置
// ═══════════════════════════════════════════════════════════════════

const CSM_HOST = "127.0.0.1";
const CSM_PORT = parseInt(process.env.CSM_PORT || "19876", 10);
const CSM_BASE = `http://${CSM_HOST}:${CSM_PORT}`;
/** 智能检测 CSM 项目根目录（仅开发模式使用） */
const CSM_PROJECT_DIR = (() => {
  if (process.env.CSM_PROJECT_DIR) return process.env.CSM_PROJECT_DIR;
  const dir = resolve(__dirname);
  if (dir.endsWith("pi-extension") || dir.endsWith("pi-extension\\")) {
    return resolve(dir, "..");
  }
  return dir;
})();
/** pip 安装后 membrain 在 site-packages，无需 PYTHONPATH */
const CSM_IS_PIP_INSTALLED = (() => {
  try {
    const result = spawnSync(
      process.platform === "win32" ? "python" : "python3",
      ["-c", "import membrain"],
      { timeout: 5000 }
    );
    return result.status === 0;
  } catch {
    return false;
  }
})();
const CSM_SRC_DIR = join(CSM_PROJECT_DIR, "src");
/** BGE 模型路径：pip 安装后用 HF 缓存，开发模式用本地 models/ */
const CSM_EMBEDDING_MODEL =
  process.env.CSM_EMBEDDING_MODEL ||
  (CSM_IS_PIP_INSTALLED ? "" : join(CSM_PROJECT_DIR, "models", "bge-large-zh-v1.5"));
const CSM_DB_PATH =
  process.env.CSM_DB ||
  join(homedir(), ".pi", "agent", "csm_memory.db");

// 启动 sidecar 的最大等待时间（毫秒）
const STARTUP_TIMEOUT_MS = 15_000;
// 健康检查轮询间隔（毫秒）
const HEALTH_POLL_MS = 300;

// ═══════════════════════════════════════════════════════════════════
// 运行时状态
// ═══════════════════════════════════════════════════════════════════

let sidecarProcess: ChildProcess | null = null;
let sidecarAvailable = false;
/** 最近一次 pre_prompt 返回的 memory_ids，用于 post_run 时回传强化 */
let lastMemoryIds: number[] = [];

// ═══════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════

/** 将当前工作目录映射为稳定的 project_id */
function getProjectId(cwd: string): string {
  return createHash("sha256").update(cwd).digest("hex").slice(0, 16);
}

/** 确保数据库目录存在 */
function ensureDbDir(dbPath: string): void {
  const dir = dbPath.replace(/[/\\][^/\\]+$/, "");
  if (dir && !existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
}

/** 向 CSM sidecar 发送 HTTP 请求 */
async function csmFetch(
  path: string,
  body?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${CSM_BASE}${path}`, {
    method: body !== undefined ? "POST" : "GET",
    headers: body !== undefined
      ? { "Content-Type": "application/json" }
      : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`CSM ${path} returned ${res.status}: ${text}`);
  }
  return (await res.json()) as Record<string, unknown>;
}

/** 检查 sidecar 是否就绪 */
async function checkHealth(): Promise<boolean> {
  try {
    const data = await csmFetch("/health");
    return data.ok === true;
  } catch {
    return false;
  }
}

/** 查找可用的 Python 解释器——优先能 import membrain 的 */
function findPython(): string {
  const isWin = process.platform === "win32";
  const candidates = isWin ? [
    process.env.CSM_PYTHON || "",
    "python",
    "python3",
  ] : [
    process.env.CSM_PYTHON || "",
    "python3",
    "python",
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    try {
      const result = spawnSync(candidate, ["-c", "import membrain"], {
        encoding: "utf-8", timeout: 5000,
      });
      if (result.status === 0) return candidate;
    } catch { /* try next */ }
  }
  return isWin ? "python" : "python3";
}

/** 启动 CSM sidecar 子进程 */
function spawnSidecar(): ChildProcess {
  ensureDbDir(CSM_DB_PATH);
  const pythonCmd = findPython();
  const proc = spawn(pythonCmd, [
    "-m", "membrain.cli", "serve",
    "--host", CSM_HOST,
    "--port", String(CSM_PORT),
  ], {
    env: {
      ...process.env,
      CSM_DB: CSM_DB_PATH,
      CSM_HOST: CSM_HOST,
      CSM_PORT: String(CSM_PORT),
      CSM_EMBEDDING_BACKEND: "local",
      CSM_EMBEDDING_MODEL: CSM_EMBEDDING_MODEL,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  proc.stdout?.on("data", (chunk: Buffer) => {
    const line = chunk.toString("utf-8").trim();
    if (line) console.log(`[csm-sidecar] ${line}`);
  });

  proc.stderr?.on("data", (chunk: Buffer) => {
    console.error(`[csm-sidecar:err] ${chunk.toString("utf-8").trim()}`);
  });

  proc.on("exit", (code, signal) => {
    console.log(`[csm-sidecar] exited code=${code} signal=${signal}`);
    if (proc === sidecarProcess) {
      sidecarAvailable = false;
      sidecarProcess = null;
    }
  });

  proc.on("error", (err) => {
    console.error(`[csm-sidecar] spawn error: ${err.message}`);
    sidecarAvailable = false;
    sidecarProcess = null;
  });

  return proc;
}

/** 等待 sidecar 就绪，超时则放弃 */
async function waitForSidecar(proc: ChildProcess): Promise<boolean> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) {
      // 进程已退出，启动失败
      return false;
    }
    if (await checkHealth()) {
      return true;
    }
    await sleep(HEALTH_POLL_MS);
  }
  return false;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 停止 sidecar */
function stopSidecar(): void {
  if (sidecarProcess) {
    const pid = sidecarProcess.pid;
    console.log(`[mb-memory] stopping sidecar (pid=${pid})...`);
    try {
      if (process.platform === "win32") {
        // Windows: 使用 taskkill 确保整个进程树被终止
        spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
          stdio: "ignore",
        });
      } else {
        sidecarProcess.kill("SIGTERM");
        // 给予 3 秒优雅退出时间，然后强制终止
        setTimeout(() => {
          if (sidecarProcess && sidecarProcess.exitCode === null) {
            sidecarProcess.kill("SIGKILL");
          }
        }, 3000);
      }
    } catch {
      // 忽略终止过程中的错误
    }
    sidecarProcess = null;
    sidecarAvailable = false;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Agent 消息提取辅助函数
// ═══════════════════════════════════════════════════════════════════

interface PiMessage {
  role: string;
  content?: Array<{ type: string; text?: string }>;
}

/** 从消息列表中提取纯文本 */
function extractText(messages: PiMessage[], role: string): string {
  const msg = messages.find((m) => m.role === role);
  if (!msg?.content) return "";
  return msg.content
    .filter((c) => c.type === "text" && c.text)
    .map((c) => c.text!)
    .join("\n");
}

/** 检查 membrain Python 包是否已安装 */
function checkCSMInstalled(): boolean {
  try {
    const result = spawnSync(
      process.platform === "win32" ? "python" : "python3",
      ["-c", "import membrain"],
      { encoding: "utf-8", timeout: 5000 }
    );
    return result.status === 0;
  } catch {
    return false;
  }
}

// ═══════════════════════════════════════════════════════════════════
// 扩展入口
// ═══════════════════════════════════════════════════════════════════

export default async function (pi: ExtensionAPI) {
  // ── 检查 Python 后端是否已安装 ──────────────────────────────
  if (!checkCSMInstalled()) {
    console.log(
      "[mb-memory] membrain Python package not found.\n" +
      "  Install it first: pip install membrain\n" +
      "  Local bge-large-zh-v1.5 embeddings are required at runtime.\n" +
      "  Memory features disabled this session."
    );
    return;
  }

  // ── 启动 CSM sidecar ──────────────────────────────────────────
  if (!sidecarAvailable) {
    if (await checkHealth()) {
      // Sidecar 已在运行（可能是上一个 pi 实例留下的，或手动启动的）
      sidecarAvailable = true;
      console.log("[mb-memory] connected to existing sidecar");
    } else {
      console.log("[mb-memory] starting CSM sidecar...");
      sidecarProcess = spawnSidecar();
      const ready = await waitForSidecar(sidecarProcess);
      if (ready) {
        sidecarAvailable = true;
        console.log("[mb-memory] sidecar ready");
      } else {
        console.error("[mb-memory] sidecar failed to start within timeout");
        console.error("[mb-memory] memory features will be disabled this session");
        sidecarAvailable = false;
      }
    }
  }

  // ── 退出时关闭 sidecar ────────────────────────────────────────
  pi.on("session_shutdown", async (event) => {
    // 仅在真正退出时关闭，reload/new/resume/fork 时保持运行
    if (event.reason === "quit") {
      stopSidecar();
    }
  });

  // ── 每次 Agent 开始前：检索记忆并注入上下文 ───────────────────
  pi.on("before_agent_start", async (event, ctx) => {
    if (!sidecarAvailable) return;

    const projectId = getProjectId(ctx.cwd);
    try {
      const result = await csmFetch("/pre_prompt", {
        user_id: "pi-user",
        workspace_id: projectId,
        message: event.prompt,
        budget_chars: 2400,
      });

      // 保存 memory_ids 供 post_run 回传强化
      lastMemoryIds = (result.memory_ids as number[]) || [];

      const memoryContext = result.memory_context as string | undefined;
      if (memoryContext && memoryContext.trim()) {
        return {
          systemPrompt:
            event.systemPrompt +
            "\n\n<!-- 以下为持久化记忆上下文，由 CSM Memory 系统提供 -->\n" +
            memoryContext +
            "\n<!-- 持久化记忆上下文结束 -->\n",
        };
      }
    } catch (err) {
      console.error(`[mb-memory] pre_prompt error: ${(err as Error).message}`);
    }
  });

  // ── 每次 Agent 结束后：观察交互并提取记忆 ─────────────────────
  pi.on("agent_end", async (event, ctx) => {
    if (!sidecarAvailable) return;

    const projectId = getProjectId(ctx.cwd);
    const messages = event.messages as PiMessage[] | undefined;
    if (!messages) return;

    const userInput = extractText(messages, "user");
    const agentOutput = extractText(messages, "assistant");
    if (!userInput) return;

    try {
      const result = await csmFetch("/post_run", {
        user_id: "pi-user",
        workspace_id: projectId,
        message: userInput,
        agent_output: agentOutput,
        memory_ids: lastMemoryIds,
      });

      const committed = result.committed_ids as number[] | undefined;
      if (committed && committed.length > 0) {
        console.log(`[mb-memory] committed memories: ${committed.join(", ")}`);
      }
    } catch (err) {
      console.error(`[mb-memory] post_run error: ${(err as Error).message}`);
    } finally {
      lastMemoryIds = [];
    }
  });

  // ═════════════════════════════════════════════════════════════
  // 用户命令
  // ═════════════════════════════════════════════════════════════

  /** /remember <内容> — 手动存入一条记忆 */
  pi.registerCommand("remember", {
    description: "手动存入一条持久记忆供后续对话使用",
    handler: async (args, ctx) => {
      if (!sidecarAvailable) {
        ctx.ui.notify("CSM sidecar 未就绪，无法存入记忆", "error");
        return;
      }
      if (!args || !args.trim()) {
        ctx.ui.notify("用法: /remember <要记住的内容>", "info");
        return;
      }

      const projectId = getProjectId(ctx.cwd);
      try {
        const result = await csmFetch("/remember", {
          user_id: "pi-user",
          project_id: projectId,
          content: args.trim(),
        });
        const id = result.memory_id;
        ctx.ui.notify(`✅ 记忆已存入 #${id}`, "info");
      } catch (err) {
        ctx.ui.notify(`存入失败: ${(err as Error).message}`, "error");
      }
    },
  });

  /** /csm-health — 查看记忆健康报告 */
  pi.registerCommand("csm-health", {
    description: "查看 MB 记忆系统的健康状态",
    handler: async (_args, ctx) => {
      if (!sidecarAvailable) {
        ctx.ui.notify("CSM sidecar 未就绪", "error");
        return;
      }
      try {
        const report = await csmFetch("/admin/health");
        const total = report.total ?? 0;
        const statuses = report.statuses as Record<string, number> | undefined;
        const active = statuses?.active ?? 0;
        const layers = report.layers as Record<string, number> | undefined;
        const layerInfo = layers
          ? Object.entries(layers)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ")
          : "";

        const lines = [
          `记忆总数: ${total}  活跃: ${active}`,
          `层级分布: ${layerInfo || "无"}`,
          `DeepSeek: ${report.deepseek_configured ? "✅ 已配置" : "⚠ 未配置（需配置才能自动提取记忆）"}`,
        ];
        ctx.ui.notify(lines.join("\n"), "info");
      } catch (err) {
        ctx.ui.notify(`查询失败: ${(err as Error).message}`, "error");
      }
    },
  });

  /** /csm-search <查询> — 搜索记忆库 */
  pi.registerCommand("csm-search", {
    description: "搜索 MB 记忆库",
    handler: async (args, ctx) => {
      if (!sidecarAvailable) {
        ctx.ui.notify("CSM sidecar 未就绪", "error");
        return;
      }
      if (!args || !args.trim()) {
        ctx.ui.notify("用法: /csm-search <查询关键词>", "info");
        return;
      }

      const projectId = getProjectId(ctx.cwd);
      try {
        const result = await csmFetch("/admin/retrieval/test", {
          query: args.trim(),
          project_id: projectId,
          limit: 5,
          mode: "answer_injection",
        });
        const items = (result.items || []) as Array<{
          memory?: {
            id?: number;
            layer?: string;
            content?: string;
            current_strength?: number;
          };
          final_score?: number;
        }>;

        if (items.length === 0) {
          ctx.ui.notify("未找到相关记忆", "info");
          return;
        }

        const lines = items.map(
          (item) =>
            `#${item.memory?.id ?? "?"} [${item.memory?.layer ?? "?"}] ` +
            `score=${item.final_score?.toFixed(3) ?? "?"} ` +
            `${(item.memory?.content ?? "").slice(0, 80)}`,
        );
        ctx.ui.notify(lines.join("\n"), "info");
      } catch (err) {
        ctx.ui.notify(`搜索失败: ${(err as Error).message}`, "error");
      }
    },
  });
}
