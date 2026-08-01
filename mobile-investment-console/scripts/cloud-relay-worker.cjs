#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

const baseUrl = String(process.env.CLOUD_RELAY_BASE_URL || "").replace(/\/$/, "");
const desktopToken = process.env.RELAY_DESKTOP_TOKEN || process.env.CLOUD_RELAY_DESKTOP_TOKEN || "";
const repoRoot = process.env.STANDALONE_RELAY_REPO_ROOT || "/Users/vincentpan/Documents/investing/mobile-investment-console";
const investingRoot = path.resolve(repoRoot, "..");
const statePath = process.env.CLOUD_RELAY_WORKER_STATE || "/private/tmp/codex-relay-cloud-worker-state.json";
const eventsPath = process.env.CLOUD_RELAY_WORKER_EVENTS || "/private/tmp/codex-relay-cloud-worker-events.jsonl";
const executeTasks = process.argv.includes("--execute") || process.env.CLOUD_RELAY_EXECUTE_TASKS === "1";
const loop = process.argv.includes("--loop");
const intervalMs = Number(process.env.CLOUD_RELAY_WORKER_INTERVAL_MS || 2500);
const timeoutMs = Number(process.env.CLOUD_RELAY_TASK_TIMEOUT_MS || 120000);
const codexAssistantEnabled = process.env.CLOUD_RELAY_USE_CODEX_ASSISTANT !== "0";
const codexBin = process.env.CODEX_BIN || "/Applications/Codex.app/Contents/Resources/codex";
const codexTimeoutMs = Number(process.env.CLOUD_RELAY_CODEX_TIMEOUT_MS || 180000);
const codexProgressIntervalMs = Number(process.env.CLOUD_RELAY_CODEX_PROGRESS_INTERVAL_MS || 30000);

const allowedScripts = {
  current_signal_probe: "active-alpha-paper-monitor/scripts/current_signal_probe.py",
  multisource_snapshot: "active-alpha-paper-monitor/scripts/run_multisource_alpha_snapshot.py",
  weekly_goal_lab: "active-alpha-paper-monitor/scripts/weekly_goal_strategy_lab.py"
};

main().catch((error) => {
  console.error(error.stack || error.message || error);
  process.exit(1);
});

async function main() {
  if (!baseUrl) throw new Error("CLOUD_RELAY_BASE_URL is required.");
  if (!desktopToken) throw new Error("RELAY_DESKTOP_TOKEN or CLOUD_RELAY_DESKTOP_TOKEN is required.");
  if (loop) {
    console.log(JSON.stringify({ ok: true, service: "codex-relay-cloud-worker", mode: "loop", base_url: baseUrl, execute_tasks: executeTasks, safety: safety() }));
    for (;;) {
      await processOnce().catch((error) => appendEvent("worker_error", { error: error.message || String(error) }));
      await sleep(intervalMs);
    }
  }
  console.log(JSON.stringify(await processOnce(), null, 2));
}

async function processOnce() {
  const workerState = readWorkerState();
  const result = await relayGet("/api/relay/desktop/commands?cursor=0&limit=200");
  const processed = [];
  const skipped = [];
  await postWorkerHeartbeat({
    pending_count: (result.commands || []).length,
    processed_count: 0,
    note: "local worker polling"
  });
  for (const command of result.commands || []) {
    if (!command.command_id) continue;
    if (workerState.processed_command_ids[command.command_id]) {
      skipped.push({ command_id: command.command_id, reason: "already_processed" });
      continue;
    }
    const outcome = await handleCommand(command);
    const reply = await relayPost("/api/relay/desktop/replies", {
      command_id: command.command_id,
      text: outcome.text,
      worker_status: outcome.status,
      display_name: "Desktop Codex Worker"
    });
    workerState.processed_command_ids[command.command_id] = {
      processed_at: now(),
      type: command.type,
      status: outcome.status,
      reply_id: reply.reply && reply.reply.relay_id
    };
    writeWorkerState(workerState);
    appendEvent("command_processed", { command_id: command.command_id, type: command.type, status: outcome.status, reply_id: reply.reply && reply.reply.relay_id });
    processed.push({ command_id: command.command_id, type: command.type, status: outcome.status, reply_id: reply.reply && reply.reply.relay_id });
  }
  await postWorkerHeartbeat({
    pending_count: Math.max(0, (result.commands || []).length - processed.length - skipped.length),
    processed_count: processed.length,
    note: "local worker idle"
  });
  return { ok: true, service: "codex-relay-cloud-worker", processed_count: processed.length, skipped_count: skipped.length, processed, skipped, execute_tasks: executeTasks, safety: safety() };
}

async function handleCommand(command) {
  appendEvent("command_started", { command_id: command.command_id, type: command.type, relay_id: command.relay_id });
  if (command.type === "manual_report_request") return manualReportOutcome(command);
  if (command.type === "portfolio_analysis_request") return codexAssistantOutcome(command, { mode: "investment" });
  if (allowedScripts[command.type]) return monitorOutcome(command);
  if (command.type === "action_decision") {
    return {
      status: "audited",
      text: [
        "# Action 决策已由云 worker 确认记录",
        `action_id: ${command.action_id || "unknown"}`,
        `decision: ${command.decision || "unknown"}`,
        "安全: audit_only_no_live_order"
      ].join("\n")
    };
  }
  if (command.type === "diagnostic") {
    return {
      status: "ok",
      text: [
        "# 云 worker 诊断",
        "队列消费: online",
        `execute_tasks: ${executeTasks}`,
        `repo_root: ${repoRoot}`,
        "安全: live_orders_enabled=false"
      ].join("\n")
    };
  }
  if (command.type === "status_check") {
    return {
      status: "ok",
      text: [
        "# 本机状态",
        "worker: online",
        `execute_tasks: ${executeTasks}`,
        `repo_root: ${repoRoot}`,
        `codex_assistant: ${codexAssistantEnabled ? "enabled" : "disabled"}`,
        `progress_interval_ms: ${codexProgressIntervalMs}`,
        "队列消费: ok",
        "安全: live_orders_enabled=false；不会下单、不会转账"
      ].join("\n")
    };
  }
  return codexAssistantOutcome(command, { mode: "general" });
}

function manualReportOutcome(command) {
  const latest = latestManualReport();
  return {
    status: "reported",
    text: [
      "# 报告请求云 worker 已接管",
      `request_id: ${command.request_id || "unknown"}`,
      latest ? `最新候选报告: ${latest.name}` : "最新候选报告: 未找到",
      latest && latest.path ? `path: ${latest.path}` : null,
      "下一步: 本机 Codex 可继续生成完整投资报告草稿",
      "安全: live_orders_enabled=false；只读报告/写审计，不下单"
    ].filter(Boolean).join("\n")
  };
}

async function monitorOutcome(command) {
  const expectedScript = allowedScripts[command.type];
  const symbols = normalizeSymbols(command.symbols);
  if (command.allowed_script && command.allowed_script !== expectedScript) {
    return {
      status: "blocked_unapproved_script",
      text: [
        "# 监控任务被云 worker 拦截",
        `task_id: ${command.type}`,
        `requested_script: ${command.allowed_script}`,
        `allowed_script: ${expectedScript}`,
        "安全: 非白名单脚本不会执行"
      ].join("\n")
    };
  }
  if (!executeTasks) {
    return {
      status: "dry_run_recorded",
      text: [
        `# ${command.label || command.type} 云 worker 已接管`,
        `monitor_run_id: ${command.monitor_run_id || "unknown"}`,
        `symbols: ${symbols.join(", ") || "none"}`,
        `白名单脚本: ${expectedScript}`,
        "当前模式: dry-run cloud queue consumer，未执行 Python 任务",
        "安全: live_orders_enabled=false；不下单、不转账"
      ].join("\n")
    };
  }
  const runId = command.monitor_run_id || `cloud-worker-${Date.now()}-${randomHex(3)}`;
  const stdoutPath = `/private/tmp/${runId}.stdout.txt`;
  const stderrPath = `/private/tmp/${runId}.stderr.txt`;
  const args = [path.join(investingRoot, expectedScript)];
  if (symbols.length) args.push("--symbols", symbols.join(","));
  const execution = await runProcess("python3", args, { cwd: investingRoot, stdoutPath, stderrPath, timeoutMs });
  return {
    status: execution.ok ? "executed" : "execution_failed",
    text: [
      `# ${command.label || command.type} 云 worker 已执行`,
      `monitor_run_id: ${runId}`,
      `exit_code: ${execution.exit_code}`,
      `stdout: ${stdoutPath}`,
      `stderr: ${stderrPath}`,
      "安全: LIVE_ORDERS_ENABLED=false；仍不下单"
    ].join("\n")
  };
}

async function codexAssistantOutcome(command, options = {}) {
  appendEvent("codex_assistant_started", { command_id: command.command_id, type: command.type, mode: options.mode || "general" });
  await postProgressReply(command, progressReplyText(command, options)).catch((error) => {
    appendEvent("codex_assistant_progress_failed", {
      command_id: command.command_id,
      type: command.type,
      error: error.message || String(error)
    });
  });
  const progressTicker = startProgressTicker(command, options);
  let result;
  try {
    result = await runCodexAssistant(command, options).catch((error) => ({
      ok: false,
      error: error.message || String(error)
    }));
  } finally {
    stopProgressTicker(progressTicker);
  }
  if (result.ok && result.text) {
    return {
      status: "answered",
      text: cleanMobileReply(result.text)
    };
  }
  appendEvent("codex_assistant_degraded", {
    command_id: command.command_id,
    type: command.type,
    error: result.error || "unknown"
  });
  return {
    status: "answered_degraded",
    text: fallbackAssistantReply(command, result.error || "Codex assistant did not return a final answer.")
  };
}

async function postProgressReply(command, text) {
  if (!command.relay_id || !command.device_id || !text) return null;
  return relayPost("/api/relay/desktop/replies", {
    target_device_id: command.device_id,
    in_reply_to: command.relay_id,
    worker_status: "working",
    display_name: "Desktop Codex Worker",
    text
  });
}

function startProgressTicker(command, options = {}) {
  if (!command.relay_id || !command.device_id || codexProgressIntervalMs <= 0) return null;
  const startedAt = Date.now();
  let count = 0;
  const timer = setInterval(() => {
    count += 1;
    postProgressReply(command, stillWorkingReplyText(command, options, {
      count,
      elapsedSeconds: Math.max(1, Math.round((Date.now() - startedAt) / 1000))
    })).catch((error) => {
      appendEvent("codex_assistant_progress_failed", {
        command_id: command.command_id,
        type: command.type,
        progress_count: count,
        error: error.message || String(error)
      });
    });
  }, codexProgressIntervalMs);
  if (typeof timer.unref === "function") timer.unref();
  return timer;
}

function stopProgressTicker(timer) {
  if (timer) clearInterval(timer);
}

function progressReplyText(command, options = {}) {
  if ((options.mode || "") === "investment") {
    return [
      "# 已开始本机投资分析",
      "我正在让桌面 Codex 按只读模式处理你的持仓/市场问题。完整分析可能需要 1-3 分钟。",
      "如果本地账本或最新行情读取失败，我会直接告诉你原因，不会再返回内部编号。",
      "安全: live_orders_enabled=false；不会下单、不会转账。"
    ].join("\n");
  }
  return [
    "# 已开始本机处理",
    "桌面 Codex 正在生成可读回复；如果失败，会说明原因和下一步。",
    "安全: live_orders_enabled=false"
  ].join("\n");
}

function stillWorkingReplyText(_command, options = {}, progress = {}) {
  const elapsed = progress.elapsedSeconds || 0;
  if ((options.mode || "") === "investment") {
    return [
      "# 仍在处理持仓分析",
      `已运行约 ${elapsed}s。本机 Codex 还在读取资料或生成结论，我会继续追最终回复。`,
      "这期间不要重复发送同一条；如果电脑睡眠，保持醒着后会继续。",
      "安全: live_orders_enabled=false；不会下单、不会转账。"
    ].join("\n");
  }
  return [
    "# 仍在处理",
    `已运行约 ${elapsed}s。桌面 Codex 还在生成回复，我会继续追最终结果。`,
    "安全: live_orders_enabled=false"
  ].join("\n");
}

async function runCodexAssistant(command, options = {}) {
  if (!codexAssistantEnabled) return { ok: false, error: "Codex assistant is disabled by CLOUD_RELAY_USE_CODEX_ASSISTANT=0." };
  if (!fs.existsSync(codexBin)) return { ok: false, error: `Codex binary not found: ${codexBin}` };

  const runId = safeFileToken(command.command_id || `codex-${Date.now()}`);
  const outputPath = `/private/tmp/${runId}.codex-final.txt`;
  const stdoutPath = `/private/tmp/${runId}.codex-stdout.log`;
  const stderrPath = `/private/tmp/${runId}.codex-stderr.log`;
  const prompt = buildCodexPrompt(command, options);
  const execution = await runProcessWithInput(codexBin, [
    "exec",
    "--sandbox", "read-only",
    "--skip-git-repo-check",
    "--ephemeral",
    "-C", investingRoot,
    "--output-last-message", outputPath,
    "-"
  ], {
    cwd: investingRoot,
    stdinText: prompt,
    stdoutPath,
    stderrPath,
    timeoutMs: codexTimeoutMs,
    env: { ...process.env, LIVE_ORDERS_ENABLED: "false" }
  });
  const text = readSmallFile(outputPath, 24000).trim();
  if (text) return { ok: true, text, execution };
  const stderrTail = readTailFile(stderrPath, 20000);
  const failureContext = summarizeCodexFailure(stderrTail);
  return {
    ok: false,
    error: execution.timed_out
      ? `Codex assistant timed out after ${Math.round(codexTimeoutMs / 1000)}s.${failureContext ? ` ${failureContext}` : ""}`
      : `Codex assistant exited without a final answer. exit_code=${execution.exit_code}${failureContext ? ` ${failureContext}` : ""}`
  };
}

function buildCodexPrompt(command, options = {}) {
  const userText = cleanForPrompt(command.text || "");
  const mode = options.mode || "general";
  if (mode === "investment") {
    return [
      "你是手机 Web relay 背后的本机 Codex 投资助手。用户在手机上发来一条投资/持仓分析请求。",
      "",
      "请尽量使用 manual-investment-strategy-operator skill 的规则和本地只读资料，像桌面端一样给出有用答复；但这次输出要适合手机阅读。",
      "硬性边界：只读；不下单；不转账；不请求交易所、券商、钱包写权限；保持 live_orders_enabled=false。",
      "如果最新行情、持仓账本或报告无法读取，请明确说明降级原因，不要编造数据或价格。",
      "不要输出 command_id、relay_id、内部队列名、调试日志。除非用户必须手动打开，否则不要堆本地文件路径。",
      "",
      "回复格式，控制在 1200 个中文字符以内：",
      "1. 一句话结论",
      "2. 目前持仓/市场含义",
      "3. 现在建议做什么/不做什么",
      "4. 数据缺口或需要电脑端完整报告的条件",
      "5. 安全边界",
      "",
      `手机用户原文：${userText}`
    ].join("\n");
  }
  return [
    "你是手机 Web relay 背后的本机 Codex 助手。请用中文直接回答手机用户。",
    "不要输出 command_id、relay_id、内部队列名或调试日志。",
    "如果请求涉及投资、资金、交易，必须只读分析，不下单、不转账，并说明 live_orders_enabled=false。",
    "如果无法完成，请用人能看懂的话解释原因和下一步。",
    "回复控制在 1000 个中文字符以内。",
    "",
    `手机用户原文：${userText}`
  ].join("\n");
}

function fallbackAssistantReply(command, reason) {
  const text = command.text || "";
  const investment = command.type === "portfolio_analysis_request" || /(持仓|仓位|组合|市场|行情|投资|美股|crypto|币)/i.test(text);
  if (investment) {
    const readableReason = humanizeFailure(reason);
    const context = portfolioReadinessSummary();
    return [
      "# 我收到你的持仓分析请求了",
      "这次不会再只回内部编号。当前本机 Codex 深度分析没有成功完成，所以先给你一个可读的降级回复：",
      "",
      "结论: 现在不能把这条手机消息升级成新的买卖建议；需要先确认最新行情和本地持仓账本读取正常。",
      "现在做法: 先保持观察，不自动买卖；如果要完整报告，请在电脑端修复本地账本读取后重试，或在手机发送“报告”读取已有报告摘要。",
      context ? `本地资料状态: ${context}` : null,
      `降级原因: ${readableReason}`,
      "安全: live_orders_enabled=false；不会下单、不会转账。"
    ].filter(Boolean).join("\n");
  }
  return [
    "# 我收到你的消息了",
    "本机 Codex 这次没有成功生成完整回复，但消息已经被安全处理，不会再用内部编号当作答复。",
    `降级原因: ${humanizeFailure(reason)}`,
    "你可以换成更明确的指令，例如“报告”“诊断”“分析持仓”。",
    "安全: live_orders_enabled=false"
  ].join("\n");
}

function portfolioReadinessSummary() {
  const parts = [];
  const latest = latestManualReport();
  if (latest?.name) {
    parts.push(`最新手动报告是 ${latest.name}，修改时间 ${formatLocalTime(latest.mtime_ms)}。`);
  } else {
    parts.push("未找到可用手动报告。");
  }

  const ledgers = [
    path.join(investingRoot, "unified-longterm-alpha-investor", "config", "portfolio_ledger.json"),
    path.join(repoRoot, "config", "current_position_overrides.json"),
    path.join(investingRoot, "manual-investment-strategy-operator", "recommendations", "recommendation_history.json")
  ];
  const notLocal = ledgers
    .map((filePath) => localFileStatus(filePath))
    .filter((item) => item.exists && item.dataless)
    .map((item) => path.basename(item.path));
  if (notLocal.length) {
    parts.push(`${notLocal.join("、")} 目前像是 iCloud/文件提供器占位文件，本地块数为 0，完整读取可能卡住。`);
  }
  return parts.join(" ");
}

function localFileStatus(filePath) {
  try {
    const stats = fs.statSync(filePath);
    return {
      path: filePath,
      exists: true,
      size: stats.size,
      blocks: stats.blocks,
      dataless: stats.size > 0 && stats.blocks === 0
    };
  } catch (_error) {
    return { path: filePath, exists: false, size: 0, blocks: 0, dataless: false };
  }
}

function formatLocalTime(ms) {
  if (!ms) return "未知";
  try {
    return new Date(ms).toLocaleString("zh-CN", { hour12: false });
  } catch (_error) {
    return "未知";
  }
}

function humanizeFailure(reason) {
  const value = String(reason || "unknown");
  if (/Resource deadlock avoided|portfolio_ledger|账本读取/i.test(value)) return "本地持仓账本读取被 macOS 文件层阻断，出现 Resource deadlock avoided；完整成本、数量和当前仓位无法可靠验证。";
  if (/timed out/i.test(value)) return "本机分析超时，可能是本地报告/账本读取或模型调用太慢。";
  if (/not found/i.test(value)) return "本机 Codex 命令没有找到。";
  if (/fetch failed|connection refused|websocket/i.test(value)) return "本机 Codex 联网或模型连接短暂失败。";
  if (/disabled/i.test(value)) return "当前环境关闭了 Codex 深度助手。";
  return value.slice(0, 240);
}

function summarizeCodexFailure(stderrText) {
  const text = String(stderrText || "");
  if (!text) return "";
  if (/Resource deadlock avoided/i.test(text)) return "Detected local file read failure: Resource deadlock avoided.";
  if (/portfolio_ledger\.json/i.test(text)) return "Detected portfolio ledger read failure.";
  if (/failed to connect|Connection refused|fetch failed|websocket/i.test(text)) return "Detected Codex model/network connection retries.";
  return "";
}

function cleanMobileReply(text) {
  const filtered = String(text || "")
    .replace(/\r/g, "")
    .split("\n")
    .filter((line) => !/^\s*(command_id|relay_id|worker-reply|msg-\d|relaycmd-)/i.test(line))
    .join("\n")
    .trim();
  const withoutDebug = filtered.replace(/```(?:json|text)?\s*\{[\s\S]*?(?:command_id|relay_id)[\s\S]*?```/gi, "").trim();
  return limitText(withoutDebug || "本机 Codex 已处理，但没有生成可显示内容。", 3600);
}

function cleanForPrompt(text) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, 2000);
}

function limitText(text, maxChars) {
  const value = String(text || "");
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars - 28).trim()}\n\n[已为手机端截断]`;
}

function safeFileToken(value) {
  return String(value || "relay").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 120);
}

function readSmallFile(filePath, maxBytes) {
  try {
    const stats = fs.statSync(filePath);
    if (stats.size > maxBytes) {
      const fd = fs.openSync(filePath, "r");
      const buffer = Buffer.alloc(maxBytes);
      const bytesRead = fs.readSync(fd, buffer, 0, maxBytes, 0);
      fs.closeSync(fd);
      return buffer.subarray(0, bytesRead).toString("utf8");
    }
    return fs.readFileSync(filePath, "utf8");
  } catch (_error) {
    return "";
  }
}

function readTailFile(filePath, maxBytes) {
  try {
    const stats = fs.statSync(filePath);
    const start = Math.max(0, stats.size - maxBytes);
    const length = Math.min(maxBytes, stats.size);
    const fd = fs.openSync(filePath, "r");
    const buffer = Buffer.alloc(length);
    const bytesRead = fs.readSync(fd, buffer, 0, length, start);
    fs.closeSync(fd);
    return buffer.subarray(0, bytesRead).toString("utf8");
  } catch (_error) {
    return "";
  }
}

async function relayGet(pathname) {
  return relayFetch(pathname, { method: "GET" });
}

async function relayPost(pathname, body) {
  return relayFetch(pathname, { method: "POST", body: JSON.stringify(body) });
}

async function postWorkerHeartbeat(fields = {}) {
  await relayPost("/api/relay/desktop/heartbeat", {
    bridge: "local-worker",
    status: "online",
    ...fields
  }).catch((error) => appendEvent("worker_heartbeat_failed", { error: error.message || String(error) }));
}

async function relayFetch(pathname, options) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${desktopToken}`,
      ...(options.headers || {})
    }
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `Relay request failed: ${response.status}`);
  return body;
}

function latestManualReport() {
  const reportsDir = path.join(investingRoot, "manual-investment-strategy-operator", "reports");
  try {
    return fs.readdirSync(reportsDir)
      .filter((name) => /\.md$/i.test(name))
      .map((name) => {
        const fullPath = path.join(reportsDir, name);
        return { name, path: fullPath, mtime_ms: fs.statSync(fullPath).mtimeMs };
      })
      .sort((a, b) => b.mtime_ms - a.mtime_ms)[0] || null;
  } catch (_error) {
    return null;
  }
}

function runProcess(command, args, options) {
  ensureParent(options.stdoutPath);
  const stdout = fs.openSync(options.stdoutPath, "a");
  const stderr = fs.openSync(options.stderrPath, "a");
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: { ...process.env, LIVE_ORDERS_ENABLED: "false" },
      stdio: ["ignore", stdout, stderr]
    });
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      child.kill("SIGTERM");
      resolve({ ok: false, exit_code: null, timed_out: true });
    }, options.timeoutMs);
    child.on("exit", (code) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ ok: code === 0, exit_code: code, timed_out: false });
    });
    child.on("error", (error) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ ok: false, exit_code: null, error: error.message || String(error), timed_out: false });
    });
  });
}

function runProcessWithInput(command, args, options) {
  ensureParent(options.stdoutPath);
  ensureParent(options.stderrPath);
  const stdout = fs.openSync(options.stdoutPath, "a");
  const stderr = fs.openSync(options.stderrPath, "a");
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      stdio: ["pipe", stdout, stderr]
    });
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { fs.closeSync(stdout); } catch (_error) {}
      try { fs.closeSync(stderr); } catch (_error) {}
      resolve(result);
    };
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      finish({ ok: false, exit_code: null, timed_out: true });
    }, options.timeoutMs);
    child.on("exit", (code) => finish({ ok: code === 0, exit_code: code, timed_out: false }));
    child.on("error", (error) => finish({ ok: false, exit_code: null, error: error.message || String(error), timed_out: false }));
    child.stdin.on("error", () => {});
    child.stdin.end(options.stdinText || "");
  });
}

function readWorkerState() {
  try {
    const parsed = JSON.parse(fs.readFileSync(statePath, "utf8"));
    return { version: 1, processed_command_ids: parsed.processed_command_ids || {} };
  } catch (error) {
    if (error.code === "ENOENT") return { version: 1, processed_command_ids: {} };
    throw error;
  }
}

function writeWorkerState(value) {
  ensureParent(statePath);
  fs.writeFileSync(statePath, `${JSON.stringify(value, null, 2)}\n`);
}

function appendEvent(type, fields) {
  appendJsonl(eventsPath, {
    event_id: `cloud-worker-event-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    source: "codex-relay-cloud-worker",
    type,
    ...fields,
    safety: safety()
  });
}

function appendJsonl(filePath, entry) {
  ensureParent(filePath);
  fs.appendFileSync(filePath, `${JSON.stringify(entry)}\n`);
}

function normalizeSymbols(value) {
  const raw = Array.isArray(value) ? value : String(value || "").split(/[,\s，、]+/);
  return [...new Set(raw.map((item) => String(item || "").trim().toUpperCase()).filter(Boolean).map((item) => item.replace(/[^A-Z0-9]/g, "")).filter(Boolean).map((item) => /USDT$|USD$|USDC$/.test(item) ? item : `${item}USDT`))].slice(0, 8);
}

function ensureParent(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString("hex");
}

function now() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safety() {
  return {
    live_orders_enabled: false,
    withdrawals_enabled: false,
    relay_executes_trades: false,
    stores_exchange_credentials: false,
    human_confirmation_required: true
  };
}
