const APP_VERSION = "2026.06.21.5";
const RESET_KEYS = [
  "codexRelayCloudToken",
  "codexRelayCloudDevice",
  "codexRelayCloudCursor",
  "codexRelayCloudPending"
];
const resetRequested = new URLSearchParams(location.search).has("reset");
let resetNotice = "";
let resetRevocationToken = "";

if (resetRequested) {
  resetRevocationToken = localStorage.getItem("codexRelayCloudToken") || "";
  clearLocalRelayState();
  resetNotice = "已清除这台手机保存的旧登录。请重新输入当前入口对应的配对码。";
}

const state = {
  token: localStorage.getItem("codexRelayCloudToken") || "",
  device: readJson("codexRelayCloudDevice"),
  cursor: Number(localStorage.getItem("codexRelayCloudCursor") || 0),
  pending: readJson("codexRelayCloudPending") || {},
  seen: new Set(),
  polling: null,
  selfCheck: {
    active: false,
    messageId: "",
    timeout: null
  },
  sendQueue: Promise.resolve(),
  pollFailures: 0,
  lastBackfillAt: 0,
  lastStatus: null,
  lastStatusRefreshAt: 0,
  lastPollAt: 0,
  recentSends: {},
  receiptCheckAt: {},
  relayMode: detectRelayMode()
};

const $ = (selector) => document.querySelector(selector);
const els = {
  relayState: $("#relayState"),
  apiStatus: $("#apiStatus"),
  workerStatus: $("#workerStatus"),
  localStatus: $("#localStatus"),
  queueStatus: $("#queueStatus"),
  syncStatus: $("#syncStatus"),
  safetyStatus: $("#safetyStatus"),
  setupNotice: $("#setupNotice"),
  registerPanel: $("#registerPanel"),
  chatPanel: $("#chatPanel"),
  displayName: $("#displayName"),
  pairingModeTag: $("#pairingModeTag"),
  pairingCodeLabel: $("#pairingCodeLabel"),
  pairingCode: $("#pairingCode"),
  pairingHelpTitle: $("#pairingHelpTitle"),
  pairingCommand: $("#pairingCommand"),
  pairingHelpText: $("#pairingHelpText"),
  registerButton: $("#registerButton"),
  setupResetButton: $("#setupResetButton"),
  registerStatus: $("#registerStatus"),
  versionMeta: $("#versionMeta"),
  appVersion: $("#appVersion"),
  forgetButton: $("#forgetButton"),
  messageList: $("#messageList"),
  messageForm: $("#messageForm"),
  messageText: $("#messageText"),
  deliveryStatus: $("#deliveryStatus"),
  deliveryMeta: $("#deliveryMeta"),
  deliveryLatency: $("#deliveryLatency"),
  selfCheckButton: $("#selfCheckButton"),
  selfCheckSummary: $("#selfCheckSummary"),
  deviceMeta: $("#deviceMeta"),
  checkItems: document.querySelectorAll("[data-check]"),
  quickButtons: document.querySelectorAll("[data-quick-text]")
};

window.addEventListener("load", boot);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    resumeLiveSync();
  }
});
window.addEventListener("focus", resumeLiveSync);
window.addEventListener("online", resumeLiveSync);
els.registerButton.addEventListener("click", registerDevice);
els.setupResetButton.addEventListener("click", () => resetDevice("已清除旧登录。请重新输入当前入口对应的配对码。"));
els.forgetButton.addEventListener("click", () => resetDevice("已重置这台手机。请重新输入当前入口对应的配对码。"));
els.messageForm.addEventListener("submit", sendMessage);
els.selfCheckButton.addEventListener("click", runSelfCheck);
els.quickButtons.forEach((button) => button.addEventListener("click", () => sendText(button.dataset.quickText || "")));

async function boot() {
  applyRelayModeCopy();
  renderVersion();
  if (resetRequested) {
    await revokeMobileToken(resetRevocationToken);
    await clearBrowserAppCache();
    history.replaceState(null, "", `${location.pathname}${location.hash || ""}`);
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then((registration) => registration.update()).catch(() => {});
  }
  await refreshStatus();
  if (state.token) {
    showChat();
    startPolling();
  } else {
    showSetup();
  }
  if (resetNotice) {
    showSetup();
    els.registerStatus.textContent = resetNotice;
  }
  if (location.hash === "#registerPanel") setTimeout(() => els.pairingCode.focus(), 250);
}

async function refreshStatus() {
  try {
    const status = await api("/api/relay/status", { skipAuth: true });
    state.lastStatus = status;
    state.lastStatusRefreshAt = Date.now();
    setRelayState("online");
    els.apiStatus.textContent = status.ready.mobile_registration ? "READY" : "NEEDS CODE";
    updateDesktopStatus(status);
    updateQueueStatus(status);
    updateSyncStatus();
    if (!state.token) els.localStatus.textContent = "未配对";
    els.safetyStatus.textContent = status.safety.live_orders_enabled ? "下单开启" : "禁用下单";
    els.setupNotice.textContent = state.token
      ? statusLine(status)
      : state.relayMode.setupNotice;
  } catch (error) {
    setRelayState("offline");
    els.apiStatus.textContent = "OFFLINE";
    els.workerStatus.textContent = "--";
    els.localStatus.textContent = "--";
    els.queueStatus.textContent = "--";
    els.syncStatus.textContent = "失败";
    els.registerStatus.textContent = friendlyError(error);
  }
}

async function registerDevice() {
  const pairingCode = els.pairingCode.value.trim();
  if (!pairingCode) {
    els.registerStatus.textContent = `先填${state.relayMode.expectedCodeName}。${state.relayMode.shortHelp}`;
    els.pairingCode.focus();
    return;
  }
  els.registerButton.disabled = true;
  els.registerStatus.textContent = "正在注册这台手机...";
  try {
    const result = await api("/api/relay/devices/register", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({
        display_name: els.displayName.value.trim() || "Mobile",
        pairing_code: pairingCode
      })
    });
    state.token = result.token;
    state.device = result.device;
    state.cursor = 0;
    state.pending = {};
    state.seen = new Set();
    state.receiptCheckAt = {};
    localStorage.setItem("codexRelayCloudToken", state.token);
    localStorage.setItem("codexRelayCloudDevice", JSON.stringify(state.device));
    localStorage.setItem("codexRelayCloudCursor", "0");
    savePending();
    els.pairingCode.value = "";
    els.registerStatus.textContent = "连接成功。现在可以发送联通测试或报告。";
    showChat();
    startPolling();
  } catch (error) {
    els.registerStatus.textContent = friendlyError(error);
  } finally {
    els.registerButton.disabled = false;
  }
}

function showSetup() {
  els.registerPanel.hidden = false;
  els.chatPanel.hidden = true;
  els.localStatus.textContent = "未配对";
}

function showChat() {
  els.registerPanel.hidden = true;
  els.chatPanel.hidden = false;
  const deviceName = state.device?.display_name || "已注册设备";
  const deviceId = state.device?.device_id || "";
  els.deviceMeta.textContent = deviceId ? `${deviceName} · ${shortId(deviceId)}` : deviceName;
  els.setupNotice.textContent = "已连接。电脑醒着时会自动回复；电脑刚恢复时点“唤醒”。";
}

async function sendMessage(event) {
  event.preventDefault();
  const text = els.messageText.value.trim();
  if (!text) return;
  els.messageText.value = "";
  await sendText(text);
}

async function sendText(text) {
  return sendRelayText(text);
}

async function sendRelayText(text, options = {}) {
  const run = state.sendQueue.then(() => sendRelayTextNow(text, options), () => sendRelayTextNow(text, options));
  state.sendQueue = run.catch(() => {});
  return run;
}

async function sendRelayTextNow(text, options = {}) {
  if (!state.token) {
    els.deliveryStatus.textContent = "请先连接";
    if (options.throwOnError) throw new Error("请先连接");
    return null;
  }
  const duplicateKey = sendDuplicateKey(text);
  const duplicate = state.recentSends[duplicateKey];
  if (!options.allowDuplicate && duplicate && Date.now() - duplicate.started_at < 2500) {
    els.deliveryStatus.textContent = "已忽略重复点击";
    els.deliveryMeta.textContent = text;
    els.deliveryLatency.textContent = "--";
    return duplicate.message || null;
  }
  const clientMessageId = options.clientMessageId || createClientMessageId();
  state.recentSends[duplicateKey] = {
    started_at: Date.now(),
    client_message_id: clientMessageId,
    message: null
  };
  els.deliveryStatus.textContent = "发送中";
  els.deliveryMeta.textContent = text;
  try {
    const result = await api("/api/relay/mobile/messages", {
      method: "POST",
      body: JSON.stringify({ text, client_message_id: clientMessageId })
    });
    appendMessage(result.message);
    state.recentSends[duplicateKey].message = result.message;
    state.pending[result.message.relay_id] = state.pending[result.message.relay_id] || Date.now();
    savePending();
    els.deliveryStatus.textContent = result.duplicate ? "发送已确认" : "等待桌面 worker";
    els.localStatus.textContent = "处理中";
    setTimeout(pollMessages, 350);
    return result.message;
  } catch (error) {
    delete state.recentSends[duplicateKey];
    els.deliveryStatus.textContent = friendlyError(error);
    if (options.throwOnError) throw error;
    return null;
  }
}

async function runSelfCheck() {
  if (!state.token) {
    els.registerStatus.textContent = "先完成配对，再运行连接自检。";
    showSetup();
    return;
  }
  clearSelfCheckTimeout();
  state.selfCheck.active = true;
  state.selfCheck.messageId = "";
  els.selfCheckButton.disabled = true;
  els.selfCheckSummary.textContent = "正在唤醒链路...";
  setCheck("api", "checking", "正在请求 /api/relay/status");
  setCheck("device", "checking", "正在确认本机保存的手机 token");
  setCheck("worker", "idle", "等待发送诊断指令");

  try {
    await refreshStatus();
    setCheck("api", "ok", "云端 API 可访问");
    setCheck("device", "ok", state.device?.device_id ? `已配对: ${state.device.device_id}` : "已保存手机凭证");
    setCheck("worker", "checking", "已发送“诊断”，等待本机 worker 回写");
    const message = await sendRelayText("诊断", { throwOnError: true });
    state.selfCheck.messageId = message?.relay_id || "";
    state.selfCheck.timeout = setTimeout(() => {
      if (!state.selfCheck.active) return;
      state.selfCheck.active = false;
      els.selfCheckButton.disabled = false;
      setCheck("worker", "warn", "30 秒内未收到 worker 回写");
      els.selfCheckSummary.textContent = "消息已排队。如果电脑在睡眠或刚开盖，保持醒着后会继续追最终回复。";
    }, 30000);
  } catch (error) {
    state.selfCheck.active = false;
    els.selfCheckButton.disabled = false;
    setCheck("api", "bad", friendlyError(error));
    els.selfCheckSummary.textContent = "自检失败，请按提示修复后重试。";
  }
}

function startPolling() {
  if (state.polling) clearInterval(state.polling);
  hydrateTranscript().finally(() => pollMessages());
  state.polling = setInterval(pollMessages, 2000);
}

async function hydrateTranscript() {
  if (!state.token) return;
  try {
    const result = await api("/api/relay/mobile/transcript?limit=80");
    const messages = result.messages || [];
    messages.forEach(appendMessage);
    if (messages.length) {
      els.deliveryStatus.textContent = "最近对话已恢复";
      els.deliveryMeta.textContent = `${messages.length} 条最近双向消息`;
      els.deliveryLatency.textContent = "--";
    }
  } catch (_error) {
    // Realtime polling still works on older relay deployments without transcript support.
  }
}

async function pollMessages() {
  if (!state.token) return;
  try {
    const result = await api(`/api/relay/mobile/messages?cursor=${state.cursor}&limit=80`);
    if (result.cursor_reset) {
      state.cursor = Number(result.cursor || 0);
      localStorage.setItem("codexRelayCloudCursor", String(state.cursor));
    }
    result.messages.forEach(appendMessage);
    state.cursor = result.next_cursor;
    localStorage.setItem("codexRelayCloudCursor", String(state.cursor));
    state.pollFailures = 0;
    state.lastPollAt = Date.now();
    setRelayState("online");
    if (!Object.keys(state.pending).length && state.token) {
      els.localStatus.textContent = result.messages.length ? "已同步" : "待命";
    }
    updateSyncStatus();
    updatePendingAgeHint();
    if (Date.now() - state.lastStatusRefreshAt > 15000) refreshStatus();
    if (Object.keys(state.pending).length) {
      maybeBackfillPendingReplies();
      refreshPendingReceipts();
    }
  } catch (error) {
    state.pollFailures += 1;
    setRelayState("offline");
    els.syncStatus.textContent = "重连";
    if (/401|invalid|token/i.test(error.message)) {
      await resetDevice("这台手机的登录已失效，已自动清除。请重新配对。");
    }
    else if (state.pollFailures >= 2) els.localStatus.textContent = "重连中";
  }
}

async function maybeBackfillPendingReplies(force = false) {
  if (!state.token) return;
  const hasPending = Object.keys(state.pending).length > 0;
  if (!hasPending && !force) return;
  const currentTime = Date.now();
  if (!force && currentTime - state.lastBackfillAt < 10000) return;
  state.lastBackfillAt = currentTime;
  try {
    const result = await api("/api/relay/mobile/messages?cursor=0&limit=200");
    (result.messages || []).forEach(appendMessage);
  } catch (_error) {
    // Normal polling owns user-facing reconnect state.
  }
}

async function refreshPendingReceipts(force = false) {
  if (!state.token) return;
  const relayIds = Object.keys(state.pending);
  if (!relayIds.length) return;
  const currentTime = Date.now();
  for (const relayId of relayIds.slice(0, 4)) {
    if (!force && currentTime - (state.receiptCheckAt[relayId] || 0) < 5000) continue;
    state.receiptCheckAt[relayId] = currentTime;
    try {
      const receipt = await api(`/api/relay/mobile/message-status?relay_id=${encodeURIComponent(relayId)}`);
      renderReceipt(receipt);
    } catch (error) {
      if (/not found/i.test(error.message)) {
        delete state.pending[relayId];
        savePending();
      }
    }
  }
}

function renderReceipt(receipt) {
  if (!receipt?.relay_id || !state.pending[receipt.relay_id]) return;
  const reply = receipt.terminal_reply || (receipt.phase === "working" ? receipt.latest_reply : null);
  if (reply) appendMessage(reply);
  const seconds = pendingSeconds(receipt.relay_id);
  els.deliveryStatus.textContent = receiptPhaseLabel(receipt.phase);
  els.deliveryMeta.textContent = receiptPhaseDetail(receipt);
  els.deliveryLatency.textContent = `${seconds}s`;
  if (receipt.phase === "completed") return;
  if (receipt.phase === "working") els.localStatus.textContent = "处理中";
  else if (receipt.phase === "consumed") els.localStatus.textContent = "已接收";
  else if (receipt.phase === "queued") els.localStatus.textContent = receipt.desktop?.online ? "等待拉取" : "电脑睡眠";
}

function appendMessage(message) {
  if (!message || state.seen.has(message.relay_id)) return;
  state.seen.add(message.relay_id);
  resolvePending(message);
  const article = document.createElement("article");
  article.className = `message is-${message.direction}`;
  article.dataset.workerStatus = message.worker_status || "";
  const meta = document.createElement("b");
  meta.textContent = message.direction === "mobile_to_desktop"
    ? formatMessageTime(message.created_at)
    : `${message.display_name || "Relay"} · ${formatMessageTime(message.created_at)}`;
  const body = document.createElement("span");
  body.textContent = message.text;
  article.append(meta, body);
  els.messageList.append(article);
  els.messageList.scrollTop = els.messageList.scrollHeight;
}

function resolvePending(message) {
  const started = state.pending[message.in_reply_to];
  if (!started) return;
  const seconds = Math.max(1, Math.round((Date.now() - started) / 1000));
  const workerReply = Boolean(message.worker_for_command_id || message.worker_status);
  const stillWorking = message.worker_status === "working";
  els.deliveryStatus.textContent = workerReply
    ? (stillWorking ? "电脑处理中" : "桌面已回复")
    : "云端已确认";
  els.deliveryMeta.textContent = message.text;
  els.deliveryLatency.textContent = `${seconds}s`;
  els.localStatus.textContent = workerReply
    ? (stillWorking ? "处理中" : "已回写")
    : "云端确认";
  if (workerReply && !stillWorking) {
    delete state.pending[message.in_reply_to];
    savePending();
  }
  if (workerReply && !stillWorking && state.selfCheck.active && message.in_reply_to === state.selfCheck.messageId) {
    clearSelfCheckTimeout();
    state.selfCheck.active = false;
    els.selfCheckButton.disabled = false;
    setCheck("worker", "ok", `${message.worker_status || "ok"} · ${seconds}s`);
    els.selfCheckSummary.textContent = "自检通过：手机消息已回到本机 worker，并成功回写到手机。";
  }
}

async function resetDevice(message) {
  clearSelfCheckTimeout();
  const tokenToRevoke = state.token;
  await revokeMobileToken(tokenToRevoke);
  state.token = "";
  state.device = null;
  state.cursor = 0;
  state.pending = {};
  state.seen = new Set();
  state.receiptCheckAt = {};
  clearLocalRelayState();
  await clearBrowserAppCache();
  els.messageList.textContent = "";
  els.deliveryStatus.textContent = "等待发送";
  els.deliveryMeta.textContent = "电脑醒着时会自动回复；电脑刚恢复时点“唤醒”。";
  els.deliveryLatency.textContent = "--";
  showSetup();
  setRelayState("checking");
  els.localStatus.textContent = "未配对";
  els.registerStatus.textContent = message;
  await refreshStatus();
  setTimeout(() => els.pairingCode.focus(), 120);
}

async function api(url, options = {}) {
  const headers = { "content-type": "application/json" };
  if (state.token && !options.skipAuth) headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || "Relay request failed");
  return body;
}

async function revokeMobileToken(token) {
  if (!token) return;
  try {
    await fetch("/api/relay/mobile/device/revoke", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`
      },
      body: "{}"
    });
  } catch (_error) {
    // Local reset must still succeed if the token was already invalid or the network is offline.
  }
}

function setRelayState(value) {
  els.relayState.textContent = value;
  els.relayState.dataset.state = value;
}

function resumeLiveSync() {
  refreshStatus();
  pollMessages();
  maybeBackfillPendingReplies(true);
  refreshPendingReceipts(true);
}

function updateDesktopStatus(status) {
  const heartbeat = status.desktop_heartbeat;
  if (!status.ready.desktop_polling) {
    els.workerStatus.textContent = "NO TOKEN";
    return;
  }
  if (!heartbeat?.updated_at) {
    els.workerStatus.textContent = "READY";
    if (state.token) els.localStatus.textContent = "待命";
    return;
  }
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(heartbeat.updated_at)) / 1000));
  const fresh = status.desktop_online === true || seconds < 90;
  els.workerStatus.textContent = fresh ? "在线" : "离线";
  if (state.token && !Object.keys(state.pending).length) {
    els.localStatus.textContent = fresh ? "待命" : "电脑睡眠";
  }
}

function updateQueueStatus(status) {
  const counts = status.counts || {};
  const queued = Number(counts.queued_commands || 0);
  const unresolved = Number(counts.unresolved_commands || 0);
  if (!Number.isFinite(queued + unresolved)) {
    els.queueStatus.textContent = "--";
    return;
  }
  if (!queued && !unresolved) {
    els.queueStatus.textContent = "0";
    return;
  }
  els.queueStatus.textContent = [
    queued ? `${queued}待` : "",
    unresolved ? `${unresolved}未回` : ""
  ].filter(Boolean).join("/");
}

function updateSyncStatus() {
  if (!state.lastPollAt && !state.lastStatusRefreshAt) {
    els.syncStatus.textContent = "--";
    return;
  }
  const last = Math.max(state.lastPollAt || 0, state.lastStatusRefreshAt || 0);
  const seconds = Math.max(0, Math.round((Date.now() - last) / 1000));
  els.syncStatus.textContent = seconds < 2 ? "刚刚" : `${seconds}s`;
}

function updatePendingAgeHint() {
  const entries = Object.entries(state.pending);
  if (!entries.length) return;
  const oldest = entries.reduce((min, [, started]) => Math.min(min, Number(started) || Date.now()), Date.now());
  const seconds = Math.max(1, Math.round((Date.now() - oldest) / 1000));
  if (seconds < 30) return;
  els.deliveryStatus.textContent = seconds < 90 ? "仍在等待" : "等待较久";
  els.deliveryMeta.textContent = "消息还在队列或电脑处理中；可点“唤醒”或发“状态”查看。";
  els.deliveryLatency.textContent = `${seconds}s`;
}

function pendingSeconds(relayId) {
  const started = Number(state.pending[relayId] || Date.now());
  return Math.max(1, Math.round((Date.now() - started) / 1000));
}

function receiptPhaseLabel(phase) {
  if (phase === "completed") return "桌面已回复";
  if (phase === "working") return "电脑处理中";
  if (phase === "consumed") return "电脑已接收";
  if (phase === "queued") return "云端已收";
  return "云端已确认";
}

function receiptPhaseDetail(receipt) {
  if (receipt.phase === "completed") return receipt.terminal_reply?.text || "worker 已完成回写。";
  if (receipt.phase === "working") return receipt.latest_reply?.text || "本机 worker 已开始处理。";
  if (receipt.phase === "consumed") return "电脑 bridge 已取走消息，正在等待本机 worker 终态回写。";
  if (receipt.phase === "queued") {
    return receipt.desktop?.online
      ? "云端已收到，等待电脑 bridge 拉取。"
      : "云端已收到；电脑可能睡眠或离线，醒来后会继续处理。";
  }
  return "云端已确认这条消息。";
}

function statusLine(status) {
  const count = status.counts?.desktop_replies || 0;
  const queued = Number(status.counts?.queued_commands || 0);
  const unresolved = Number(status.counts?.unresolved_commands || 0);
  const queueText = queued || unresolved
    ? `队列 ${queued} 条待处理，${unresolved} 条未回写。`
    : "队列干净。";
  const heartbeat = status.desktop_heartbeat;
  if (!heartbeat?.updated_at) return `已配对。${queueText} 云端有 ${count} 条回复记录。`;
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(heartbeat.updated_at)) / 1000));
  if (status.desktop_online) return `电脑在线，约 ${seconds}s 前同步。${queueText}`;
  return `电脑可能在睡眠或离线。${queueText} 新消息会先排队，电脑醒来后继续处理。`;
}

function formatMessageTime(value) {
  try {
    return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (_error) {
    return "";
  }
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 14 ? `${text.slice(0, 8)}...${text.slice(-4)}` : text;
}

function setCheck(name, status, text) {
  const item = [...els.checkItems].find((element) => element.dataset.check === name);
  if (!item) return;
  item.dataset.status = status;
  const label = item.querySelector("span");
  if (label) label.textContent = text;
}

function clearSelfCheckTimeout() {
  if (state.selfCheck.timeout) clearTimeout(state.selfCheck.timeout);
  state.selfCheck.timeout = null;
}

function friendlyError(error) {
  const message = error?.message || String(error);
  if (/pairing code/i.test(message)) return pairingCodeError();
  if (/mobile token|401|invalid.*token/i.test(message)) return "这台手机的登录已失效，请重置设备后重新配对。";
  if (/failed to fetch|network/i.test(message)) return "手机当前连不上云端 relay，请检查网络后重试。";
  return message;
}

function applyRelayModeCopy() {
  const mode = state.relayMode;
  document.documentElement.dataset.relayMode = mode.id;
  els.pairingModeTag.textContent = mode.tag;
  els.pairingCodeLabel.textContent = mode.expectedCodeName;
  els.pairingCode.placeholder = mode.placeholder;
  els.pairingHelpTitle.textContent = mode.helpTitle;
  els.pairingCommand.textContent = mode.command;
  els.pairingHelpText.textContent = mode.longHelp;
  els.registerStatus.textContent = mode.defaultStatus;
}

function renderVersion() {
  const mode = state.relayMode;
  const label = `${APP_VERSION} · ${mode.tag}`;
  els.versionMeta.textContent = `当前页面: ${label}`;
  els.appVersion.textContent = APP_VERSION;
}

function clearLocalRelayState() {
  RESET_KEYS.forEach((key) => localStorage.removeItem(key));
}

async function clearBrowserAppCache() {
  try {
    if ("serviceWorker" in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations
        .filter((registration) => registration.scope.startsWith(location.origin))
        .map((registration) => registration.unregister()));
    }
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys
        .filter((key) => /codex-relay|mobile-investment-console/i.test(key))
        .map((key) => caches.delete(key)));
    }
  } catch (_error) {
    // Reset still works if the browser blocks cache or service worker access.
  }
}

function detectRelayMode() {
  const hostname = location.hostname.toLowerCase();
  const isLoopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]" || hostname === "::1";
  const isLanIp = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostname);
  const isLanName = hostname.endsWith(".local") || hostname.endsWith(".lan");
  const isPublic = hostname.endsWith(".netlify.app") || /^https:$/.test(location.protocol);

  if (isLoopback || isLanIp || isLanName || !isPublic) {
    return {
      id: "lan",
      tag: "本机 LAN",
      expectedCodeName: "本机 LAN 配对码",
      placeholder: "relay-...",
      helpTitle: "电脑端取本机码",
      command: "node scripts/cloud-relay-install.cjs pairing",
      setupNotice: "当前是本机 LAN 入口。手机和电脑需在同一 Wi-Fi，并使用 relay- 开头的本机配对码。",
      defaultStatus: "连接后可在同一 Wi-Fi 内和本机 Codex 双向沟通。",
      shortHelp: "请在电脑打开 http://127.0.0.1:8798/pairing，使用同屏显示的 relay- 开头配对码。",
      longHelp: "手机打开 LAN 地址时，只接受 relay- 开头的本机码；如果你手里是 pair_ 开头，请改用公网 Netlify 页面。",
      mismatchHelp: "当前页面只接受 relay- 开头的本机码。请在电脑打开 http://127.0.0.1:8798/pairing，扫二维码后输入同屏显示的码。"
    };
  }

  return {
    id: "public",
    tag: "公网 Netlify",
    expectedCodeName: "公网配对码",
    placeholder: "pair_...",
    helpTitle: "电脑端取公网码",
    command: "node scripts/cloud-relay-netlify-bridge.cjs pairing",
    setupNotice: "当前是公网入口。手机可用蜂窝网络，但必须使用 pair_ 开头的公网配对码。",
    defaultStatus: "连接后可通过公网 relay 和本机 Codex 双向沟通。",
    shortHelp: "请在电脑运行 netlify bridge pairing 命令，使用输出里的 pair_ 开头配对码。",
    longHelp: "公网 Netlify 页面只接受 pair_ 开头的公网码；如果你手里是 relay- 开头，请改扫电脑 /pairing 页的 LAN 二维码。",
    mismatchHelp: "当前页面只接受 pair_ 开头的公网码。请运行上方命令复制 pairing_code；relay- 开头的码要在 LAN 页面使用。"
  };
}

function pairingCodeError() {
  return `配对码不匹配。${state.relayMode.mismatchHelp}`;
}

function readJson(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch (_error) {
    return null;
  }
}

function savePending() {
  localStorage.setItem("codexRelayCloudPending", JSON.stringify(state.pending));
}

function sendDuplicateKey(text) {
  return cleanInlineText(text).toLowerCase();
}

function cleanInlineText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return `web-${crypto.randomUUID()}`;
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
