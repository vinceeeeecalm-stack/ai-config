#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const label = "com.codex.relay.cloud.netlify-bridge";
const configPath = path.join(installDir, "config", "netlify-bridge.json");
const statePath = path.join(installDir, "state", "netlify-bridge-state.json");
const heartbeatPath = path.join(installDir, "state", "netlify-bridge-heartbeat.json");
const eventsPath = path.join(installDir, "logs", "netlify-bridge-events.jsonl");
const logPath = path.join(installDir, "logs", "netlify-bridge.log");
const errPath = path.join(installDir, "logs", "netlify-bridge.err.log");
const intervalMs = Number(process.env.NETLIFY_BRIDGE_INTERVAL_MS || 5000);
const requestTimeoutMs = Number(process.env.NETLIFY_BRIDGE_REQUEST_TIMEOUT_MS || 45000);
const inlineReplyWaitMs = Number(process.env.NETLIFY_BRIDGE_INLINE_REPLY_WAIT_MS || 12000);
const pendingReplyTtlMs = Number(process.env.NETLIFY_BRIDGE_PENDING_REPLY_TTL_MS || 20 * 60 * 1000);
const reconcileStaleMs = Number(process.env.NETLIFY_BRIDGE_RECONCILE_STALE_MS || 30 * 60 * 1000);

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "configure") return printJson(configure());
  if (command === "install") return printJson(await install());
  if (command === "status") return printJson(await status());
  if (command === "pairing" || command === "pair") return printJson(await pairing());
  if (command === "poll-once") return printJson(await pollOnce());
  if (command === "poll") return pollLoop();
  if (command === "reconcile") return printJson(await reconcileOnce(process.argv[3]));
  if (command === "e2e") return printJson(await e2e(process.argv.slice(3).join(" ") || "信号 BTC"));
  if (command === "uninstall") return printJson(uninstall());
  throw new Error(`Unknown command: ${command}`);
}

function configure() {
  ensureDir(path.dirname(configPath));
  const current = readJson(configPath, {});
  const localSecrets = readJson(path.join(installDir, "config", "secrets.json"), {});
  const config = {
    public_relay_url: requiredEnv("PUBLIC_RELAY_URL", current.public_relay_url),
    public_pairing_code: requiredEnv("RELAY_PAIRING_CODE", current.public_pairing_code),
    public_desktop_token: requiredEnv("RELAY_DESKTOP_TOKEN", current.public_desktop_token),
    local_relay_url: process.env.LOCAL_RELAY_URL || current.local_relay_url || "http://127.0.0.1:8798",
    local_pairing_code: process.env.LOCAL_RELAY_PAIRING_CODE || current.local_pairing_code || localSecrets.pairing_code || ""
  };
  if (!config.local_pairing_code) throw new Error("LOCAL_RELAY_PAIRING_CODE or installed local pairing_code is required.");
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  try { fs.chmodSync(configPath, 0o600); } catch (_error) {}
  return {
    ok: true,
    service: "codex-relay-cloud-netlify-bridge",
    command: "configure",
    config_path: configPath,
    public_relay_url: config.public_relay_url,
    public_pairing_code_preview: previewSecret(config.public_pairing_code),
    public_desktop_token_configured: Boolean(config.public_desktop_token),
    local_relay_url: config.local_relay_url,
    local_pairing_code_preview: previewSecret(config.local_pairing_code),
    safety: safety()
  };
}

async function install() {
  ensureDir(path.join(installDir, "logs"));
  ensureDir(path.join(installDir, "state"));
  ensureDir(launchAgentsDir);
  writePlist();
  await seedStateIfMissing();
  unloadPlist();
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, plistPath()], true);
  runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
  await sleep(2500);
  return status();
}

async function status() {
  const config = readConfig();
  const [publicStatus, localStatus] = await Promise.all([
    fetchJson(`${config.public_relay_url}/api/relay/status`).catch((error) => ({ ok: false, error: error.message || String(error) })),
    fetchJson(`${config.local_relay_url}/api/relay/status`).catch((error) => ({ ok: false, error: error.message || String(error) }))
  ]);
  const heartbeat = readHeartbeat();
  const value = {
    ok: Boolean(publicStatus.ok && localStatus.ok && launchctlPrint().ok),
    service: "codex-relay-cloud-netlify-bridge",
    command: "status",
    label,
    public_url: `${config.public_relay_url}/relay-chat.html`,
    local_url: `${config.local_relay_url}/relay-chat.html`,
    config_path: configPath,
    state_path: statePath,
    heartbeat_path: heartbeatPath,
    log_path: logPath,
    err_path: errPath,
    launchd_loaded: launchctlPrint().ok,
    public_status: summarizeRelayStatus(publicStatus),
    local_status: summarizeRelayStatus(localStatus),
    heartbeat,
    safety: safety()
  };
  return value;
}

async function pairing() {
  const config = readConfig();
  const bridge = await status().catch((error) => ({ ok: false, error: error.message || String(error) }));
  return {
    ok: Boolean(bridge.ok),
    service: "codex-relay-cloud-netlify-bridge",
    command: "pairing",
    public_url: `${config.public_relay_url}/relay-chat.html`,
    device_tab_url: `${config.public_relay_url}/relay-chat.html#registerPanel`,
    pairing_code: config.public_pairing_code,
    desktop_bridge_online: Boolean(bridge.ok),
    status_summary: {
      public_ok: Boolean(bridge.public_status?.ok),
      local_ok: Boolean(bridge.local_status?.ok),
      heartbeat_online: bridge.heartbeat?.status === "online"
    },
    steps: [
      "用手机打开 public_url。",
      "如果没有看到输入框，点底部“设备”或打开 device_tab_url。",
      "在“Relay 配对码”里输入 pairing_code。",
      "注册后点“联通测试”或发送“报告”。"
    ],
    caution: "配对码只给自己的手机使用；不要发到群聊或公开页面。desktop token 不会在这里显示。",
    safety: safety()
  };
}

async function pollLoop() {
  console.log(JSON.stringify({ ok: true, service: "codex-relay-cloud-netlify-bridge", mode: "loop", interval_ms: intervalMs, safety: safety() }));
  await seedStateIfMissing();
  for (;;) {
    try {
      console.log(JSON.stringify(await pollOnce()));
    } catch (error) {
      const failure = writeHeartbeat({ ok: false, status: "failed", reason: error.message || String(error), polled: 0, processed: [] });
      appendEvent("poll_failed", { reason: failure.reason });
      console.error(JSON.stringify(failure));
    }
    await sleep(intervalMs);
  }
}

async function pollOnce() {
  const config = readConfig();
  await seedStateIfMissing(config);
  const state = readState();
  const flushed = await flushPendingReplies(config, state);
  const page = await publicGet(config, `/api/relay/desktop/poll?cursor=${encodeURIComponent(state.cursor || 0)}&limit=20`);
  const processed = [];
  let cursor = Number(page.cursor || state.cursor || 0);
  for (const message of page.messages || []) {
    const outcome = await processViaLocalRelay(config, message, state);
    await postPublicWorkerReply(config, message, outcome);
    cursor += 1;
    processed.push({
      relay_id: message.relay_id,
      device_id: message.device_id,
      local_message_id: outcome.local_message_id,
      local_reply_id: outcome.local_reply_id || null,
      worker_status: outcome.worker_status,
      pending: Boolean(outcome.pending)
    });
  }
  state.cursor = Math.max(cursor, Number(page.next_cursor || cursor || 0));
  state.updated_at = now();
  writeState(state);
  const reconciled = await reconcilePublicCommands(config, reconcileStaleMs);
  await maybePostPublicHeartbeat(config, state, {
    status: "online",
    cursor: state.cursor,
    pending_count: state.pending_replies.length,
    processed_count: processed.length + flushed.length + (reconciled?.closed_count || 0),
    note: state.pending_replies.length ? `${state.pending_replies.length} replies pending local completion` : ""
  });
  const heartbeat = writeHeartbeat({
    ok: true,
    status: "online",
    reason: null,
    cursor: state.cursor,
    polled: (page.messages || []).length,
    processed,
    flushed,
    reconciled,
    pending_count: state.pending_replies.length,
    safety: page.safety || null
  });
  appendEvent("poll", { cursor: state.cursor, polled: (page.messages || []).length, processed_count: processed.length, flushed_count: flushed.length, pending_count: state.pending_replies.length });
  return {
    ok: true,
    service: "codex-relay-cloud-netlify-bridge",
    command: "poll-once",
    polled: (page.messages || []).length,
    processed,
    flushed,
    reconciled,
    cursor: state.cursor,
    heartbeat,
    safety: safety()
  };
}

async function reconcileOnce(value) {
  const staleAfterMs = parseStaleAfterMs(value, reconcileStaleMs);
  const config = readConfig();
  const reconciled = await reconcilePublicCommands(config, staleAfterMs, { throwOnError: true });
  return {
    ok: true,
    service: "codex-relay-cloud-netlify-bridge",
    command: "reconcile",
    public_url: `${config.public_relay_url}/relay-chat.html`,
    stale_after_ms: staleAfterMs,
    reconciled,
    safety: safety()
  };
}

async function e2e(text) {
  const config = readConfig();
  const registered = await fetchJson(`${config.public_relay_url}/api/relay/devices/register`, {
    method: "POST",
    body: {
      display_name: `netlify-fixed-e2e-${Date.now()}`,
      pairing_code: config.public_pairing_code
    }
  });
  const posted = await fetchJson(`${config.public_relay_url}/api/relay/mobile/messages`, {
    method: "POST",
    token: registered.token,
    body: { text }
  });

  let processed = null;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    processed = await pollOnce();
    if (processed.processed.some((item) => item.relay_id === posted.message.relay_id)) break;
    await sleep(1000);
  }

  const reply = await waitForPublicReply(config, registered.token, posted.message.relay_id, 90000);
  return {
    ok: true,
    service: "codex-relay-cloud-netlify-bridge",
    command: "e2e",
    public_url: `${config.public_relay_url}/relay-chat.html`,
    sent_text: text,
    mobile_token_preview: previewSecret(registered.token),
    public_message_id: posted.message.relay_id,
    reply_id: reply.relay_id,
    reply_sha256: reply.text_sha256 || sha256(reply.text || ""),
    processed,
    safety: safety()
  };
}

function uninstall() {
  const result = unloadPlist();
  return { ok: true, service: "codex-relay-cloud-netlify-bridge", command: "uninstall", label, result, safety: safety() };
}

async function seedStateIfMissing(config = null) {
  if (fs.existsSync(statePath)) return readState();
  const resolvedConfig = config || readConfig();
  const status = await fetchJson(`${resolvedConfig.public_relay_url}/api/relay/status`);
  const cursor = Number(status?.counts?.mobile_messages || 0);
  const state = {
    cursor,
    updated_at: now(),
    initialized_from_public_status: true
  };
  writeState(state);
  appendEvent("state_initialized", { cursor });
  return state;
}

async function processViaLocalRelay(config, publicMessage, state) {
  const registered = await fetchJson(`${config.local_relay_url}/api/relay/devices/register`, {
    method: "POST",
    body: {
      display_name: `netlify:${publicMessage.device_id || "mobile"}`,
      pairing_code: config.local_pairing_code
    }
  });
  const posted = await fetchJson(`${config.local_relay_url}/api/relay/mobile/messages`, {
    method: "POST",
    token: registered.token,
    body: { text: publicMessage.text || "" }
  });
  const reply = await waitForLocalWorkerReply(config, registered.token, posted.message.relay_id, inlineReplyWaitMs);
  if (!reply || reply.worker_status === "working") {
    rememberPendingReply(state, {
      public_relay_id: publicMessage.relay_id,
      public_device_id: publicMessage.device_id,
      local_token: registered.token,
      local_message_id: posted.message.relay_id,
      last_local_reply_id: reply?.relay_id || null,
      local_device_id: registered.device?.device_id || null,
      created_at: now(),
      text_preview: previewText(publicMessage.text || "", 160)
    });
    return {
      local_message_id: posted.message.relay_id,
      local_reply_id: reply?.relay_id || null,
      worker_status: "working",
      reply_text: reply?.text || [
        "# 本机已接到，正在处理",
        "电脑端 worker 已收到这条手机消息；较慢的投资分析会在完成后自动追加最终回复。",
        "如果电脑刚从睡眠恢复，请保持它醒着 1-2 分钟。",
        "安全: live_orders_enabled=false；不会下单、不会转账。"
      ].join("\n"),
      pending: true
    };
  }
  return {
    local_message_id: posted.message.relay_id,
    local_reply_id: reply.relay_id,
    worker_status: reply.worker_status || "processed",
    reply_text: reply.text || "# 本地 worker 已处理，但未返回正文\n安全: live_orders_enabled=false"
  };
}

async function waitForLocalWorkerReply(config, token, messageId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await fetchJson(`${config.local_relay_url}/api/relay/mobile/messages?cursor=0&limit=200`, { token });
    const reply = [...(inbox.messages || [])].reverse().find((item) => item.worker_status && item.in_reply_to === messageId);
    if (reply) return reply;
    await sleep(1000);
  }
  return null;
}

async function waitForPublicReply(config, token, messageId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await fetchJson(`${config.public_relay_url}/api/relay/mobile/messages?cursor=0&limit=200`, { token });
    const reply = [...(inbox.messages || [])].reverse().find((item) => {
      if (item.in_reply_to !== messageId) return false;
      if (item.worker_status) return item.worker_status !== "working";
      return !looksLikeWorkingReply(item.text || "");
    });
    if (reply) return reply;
    await sleep(1000);
  }
  throw new Error("Timed out waiting for public Netlify mobile reply.");
}

async function flushPendingReplies(config, state) {
  state.pending_replies = Array.isArray(state.pending_replies) ? state.pending_replies : [];
  const flushed = [];
  const remaining = [];
  for (const pending of state.pending_replies) {
    const ageMs = Date.now() - Date.parse(pending.created_at || now());
    if (Number.isFinite(ageMs) && ageMs > pendingReplyTtlMs) {
      await postPublicWorkerReply(config, {
        relay_id: pending.public_relay_id,
        device_id: pending.public_device_id
      }, {
        worker_status: "queued_timeout",
        reply_text: [
          "# 本机处理超时",
          "这条消息已经进入本机队列，但超过等待时间还没有最终回复。你可以点“唤醒”，或让电脑保持醒着后再发一次。",
          "安全: live_orders_enabled=false；不会下单、不会转账。"
        ].join("\n")
      }).catch((error) => appendEvent("pending_timeout_reply_failed", { public_relay_id: pending.public_relay_id, reason: error.message || String(error) }));
      flushed.push({ public_relay_id: pending.public_relay_id, worker_status: "queued_timeout" });
      continue;
    }
    const reply = await waitForLocalWorkerReply(config, pending.local_token, pending.local_message_id, 1000).catch((error) => {
      appendEvent("pending_local_check_failed", { public_relay_id: pending.public_relay_id, reason: error.message || String(error) });
      return null;
    });
    if (!reply) {
      remaining.push(pending);
      continue;
    }
    if (reply.worker_status === "working") {
      if (pending.last_local_reply_id !== reply.relay_id) {
        await postPublicWorkerReply(config, {
          relay_id: pending.public_relay_id,
          device_id: pending.public_device_id
        }, {
          local_message_id: pending.local_message_id,
          local_reply_id: reply.relay_id,
          worker_status: "working",
          reply_text: reply.text || "# 本机仍在处理\n安全: live_orders_enabled=false"
        });
        pending.last_local_reply_id = reply.relay_id;
        flushed.push({ public_relay_id: pending.public_relay_id, local_reply_id: reply.relay_id, worker_status: "working", pending: true });
      }
      remaining.push(pending);
      continue;
    }
    const outcome = {
      local_message_id: pending.local_message_id,
      local_reply_id: reply.relay_id,
      worker_status: reply.worker_status || "processed",
      reply_text: reply.text || "# 本地 worker 已处理，但未返回正文\n安全: live_orders_enabled=false"
    };
    await postPublicWorkerReply(config, {
      relay_id: pending.public_relay_id,
      device_id: pending.public_device_id
    }, outcome);
    flushed.push({ public_relay_id: pending.public_relay_id, local_reply_id: reply.relay_id, worker_status: outcome.worker_status });
  }
  state.pending_replies = remaining;
  return flushed;
}

async function reconcilePublicCommands(config, staleAfterMs, options = {}) {
  if (!Number.isFinite(staleAfterMs) || staleAfterMs < 0) return null;
  try {
    return await publicPost(config, "/api/relay/desktop/reconcile", {
      stale_after_ms: Math.max(0, Math.round(staleAfterMs))
    });
  } catch (error) {
    appendEvent("public_reconcile_failed", { reason: error.message || String(error) });
    if (options.throwOnError) throw error;
    return {
      ok: false,
      error: error.message || String(error),
      closed_count: 0
    };
  }
}

function rememberPendingReply(state, pending) {
  state.pending_replies = Array.isArray(state.pending_replies) ? state.pending_replies : [];
  if (!state.pending_replies.some((item) => item.public_relay_id === pending.public_relay_id)) {
    state.pending_replies.push(pending);
  }
}

function parseStaleAfterMs(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  const normalized = String(value).trim();
  if (/^(now|all|force)$/i.test(normalized)) return 0;
  const parsed = Number(normalized);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, Math.round(parsed));
}

async function postPublicWorkerReply(config, publicMessage, outcome) {
  return publicPost(config, "/api/relay/desktop/replies", {
    target_device_id: publicMessage.device_id,
    in_reply_to: publicMessage.relay_id,
    worker_status: outcome.worker_status || "processed",
    display_name: "Desktop Codex Worker",
    text: outcome.reply_text
  });
}

async function postPublicHeartbeat(config, fields) {
  return publicPost(config, "/api/relay/desktop/heartbeat", {
    bridge: "netlify-local-bridge",
    ...fields
  });
}

async function maybePostPublicHeartbeat(config, state, fields) {
  const disabledUntil = Date.parse(state.public_heartbeat_disabled_until || "");
  if (Number.isFinite(disabledUntil) && Date.now() < disabledUntil) return null;
  try {
    return await postPublicHeartbeat(config, fields);
  } catch (error) {
    const reason = error.message || String(error);
    if (/Unknown relay route|HTTP 404/i.test(reason)) {
      state.public_heartbeat_disabled_until = new Date(Date.now() + 10 * 60 * 1000).toISOString();
      state.updated_at = now();
      writeState(state);
      appendEvent("public_heartbeat_unsupported", { reason, retry_after: state.public_heartbeat_disabled_until });
      return null;
    }
    appendEvent("public_heartbeat_failed", { reason });
    return null;
  }
}

async function publicGet(config, pathname) {
  return fetchJson(`${config.public_relay_url}${pathname}`, { desktopToken: config.public_desktop_token });
}

async function publicPost(config, pathname, body) {
  return fetchJson(`${config.public_relay_url}${pathname}`, { method: "POST", desktopToken: config.public_desktop_token, body });
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
  try {
    const headers = { "content-type": "application/json" };
    if (options.desktopToken) headers.authorization = `Bearer ${options.desktopToken}`;
    if (options.token) headers.authorization = `Bearer ${options.token}`;
    const response = await fetch(url, {
      method: options.method || "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(`fetch ${url} timed out after ${requestTimeoutMs}ms`);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function writePlist() {
  const content = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${escapeXml(label)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${escapeXml(process.execPath)}</string>
    <string>${escapeXml(path.join(installDir, "scripts", "cloud-relay-netlify-bridge.cjs"))}</string>
    <string>poll</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${escapeXml(installDir)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${escapeXml(logPath)}</string>
  <key>StandardErrorPath</key>
  <string>${escapeXml(errPath)}</string>
</dict>
</plist>
`;
  fs.writeFileSync(plistPath(), content);
}

function unloadPlist() {
  const target = `gui/${process.getuid()}`;
  const result = runLaunchctl(["bootout", target, plistPath()], false);
  return { label, ok: result.status === 0, status: result.status, stderr: result.stderr.trim() };
}

function launchctlPrint() {
  const result = runLaunchctl(["print", `gui/${process.getuid()}/${label}`], false);
  return { ok: result.status === 0, status: result.status };
}

function runLaunchctl(args, fail) {
  const result = spawnSync("launchctl", args, { encoding: "utf8" });
  if (fail && result.status !== 0) throw new Error(`launchctl ${args.join(" ")} failed: ${result.stderr || result.stdout}`);
  return result;
}

function plistPath() {
  return path.join(launchAgentsDir, `${label}.plist`);
}

function readConfig() {
  const config = readJson(configPath, null);
  if (!config) throw new Error(`Netlify bridge config is missing: ${configPath}`);
  for (const key of ["public_relay_url", "public_pairing_code", "public_desktop_token", "local_relay_url", "local_pairing_code"]) {
    if (!String(config[key] || "").trim()) throw new Error(`Netlify bridge config missing ${key}.`);
  }
  return {
    public_relay_url: String(config.public_relay_url).replace(/\/$/, ""),
    public_pairing_code: String(config.public_pairing_code),
    public_desktop_token: String(config.public_desktop_token),
    local_relay_url: String(config.local_relay_url).replace(/\/$/, ""),
    local_pairing_code: String(config.local_pairing_code)
  };
}

function requiredEnv(name, fallback) {
  const value = String(process.env[name] || fallback || "").trim();
  if (!value) throw new Error(`${name} is required.`);
  return value;
}

function readState() {
  const state = readJson(statePath, { cursor: 0 });
  return {
    cursor: Number(state.cursor || 0),
    updated_at: state.updated_at || null,
    initialized_from_public_status: Boolean(state.initialized_from_public_status),
    pending_replies: Array.isArray(state.pending_replies) ? state.pending_replies : [],
    public_heartbeat_disabled_until: state.public_heartbeat_disabled_until || null
  };
}

function writeState(value) {
  ensureDir(path.dirname(statePath));
  fs.writeFileSync(statePath, `${JSON.stringify(value, null, 2)}\n`);
}

function readHeartbeat() {
  return readJson(heartbeatPath, null);
}

function writeHeartbeat(value) {
  const heartbeat = {
    ok: Boolean(value.ok),
    service: "codex-relay-cloud-netlify-bridge",
    status: value.status || (value.ok ? "online" : "failed"),
    updated_at: now(),
    cursor: Number(value.cursor || readState().cursor || 0),
    polled: Number(value.polled || 0),
    processed_count: Array.isArray(value.processed) ? value.processed.length : 0,
    processed: Array.isArray(value.processed) ? value.processed.slice(-10) : [],
    flushed_count: Array.isArray(value.flushed) ? value.flushed.length : 0,
    flushed: Array.isArray(value.flushed) ? value.flushed.slice(-10) : [],
    pending_count: Number(value.pending_count || 0),
    reason: value.reason || null,
    safety: value.safety || safety()
  };
  ensureDir(path.dirname(heartbeatPath));
  fs.writeFileSync(heartbeatPath, `${JSON.stringify(heartbeat, null, 2)}\n`);
  return heartbeat;
}

function appendEvent(type, fields) {
  ensureDir(path.dirname(eventsPath));
  fs.appendFileSync(eventsPath, `${JSON.stringify({ event_id: `netlify-bridge-${Date.now()}-${crypto.randomBytes(3).toString("hex")}`, created_at: now(), type, safety: safety(), ...fields })}\n`);
}

function summarizeRelayStatus(value) {
  if (!value || !value.ok) return { ok: false, error: value?.error || "unavailable" };
  return {
    ok: true,
    ready: value.ready || null,
    counts: value.counts || null,
    safety: value.safety || null,
    server_time: value.server_time || null
  };
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function previewSecret(value) {
  const text = String(value || "");
  if (text.length <= 10) return "***";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function previewText(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 3)}...`;
}

function looksLikeWorkingReply(value) {
  return /(正在处理|仍在处理|已开始本机|本机已接到|完整分析可能需要)/i.test(String(value || ""));
}

function now() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function printJson(value) {
  console.log(JSON.stringify(value, null, 2));
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
