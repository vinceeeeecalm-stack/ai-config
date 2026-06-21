import crypto from "node:crypto";

const STATE_KEY = "state.json";
const MAX_AUDIT_ENTRIES = 1000;
const MAX_MOBILE_MESSAGES = 500;
const MAX_DESKTOP_REPLIES = 1000;
const MAX_COMMANDS = 500;
const MAX_DISABLED_DEVICES = 50;

export async function handleRelayRequest({ method, path, query = {}, headers = {}, body = {}, store, env = {} }) {
  const normalizedPath = normalizePath(path);
  if (method === "GET" && normalizedPath === "/api/relay/status") return relayStatus(store, env);
  if (method === "POST" && normalizedPath === "/api/relay/devices/register") return registerDevice(store, env, body);
  if (method === "POST" && normalizedPath === "/api/relay/mobile/device/revoke") return revokeMobileDevice(store, env, headers);
  if (method === "POST" && normalizedPath === "/api/relay/mobile/messages") return postMobileMessage(store, env, headers, body);
  if (method === "GET" && normalizedPath === "/api/relay/mobile/messages") return listMobileMessages(store, env, headers, query);
  if (method === "GET" && normalizedPath === "/api/relay/mobile/transcript") return listMobileTranscript(store, env, headers, query);
  if (method === "GET" && normalizedPath === "/api/relay/mobile/message-status") return getMobileMessageStatus(store, env, headers, query);
  if (method === "GET" && normalizedPath === "/api/relay/desktop/poll") return listDesktopPoll(store, env, headers, query);
  if (method === "GET" && normalizedPath === "/api/relay/desktop/commands") return listDesktopCommands(store, env, headers, query);
  if (method === "POST" && normalizedPath === "/api/relay/desktop/replies") return postDesktopReply(store, env, headers, body);
  if (method === "POST" && normalizedPath === "/api/relay/desktop/reconcile") return postDesktopReconcile(store, env, headers, body);
  if (method === "POST" && normalizedPath === "/api/relay/desktop/devices/cleanup") return postDesktopDeviceCleanup(store, env, headers, body);
  if (method === "POST" && normalizedPath === "/api/relay/desktop/heartbeat") return postDesktopHeartbeat(store, env, headers, body);
  if (method === "POST" && normalizedPath === "/api/relay/desktop/audit") return postDesktopAudit(store, env, headers, body);
  return response(404, { ok: false, error: "Relay endpoint not found.", safety: safety() });
}

async function relayStatus(store, env) {
  const state = await readState(store);
  const heartbeat = normalizeDesktopHeartbeat(state.desktop_heartbeat);
  const counts = commandCounts(state);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    mode: "cloud",
    ready: {
      mobile_registration: Boolean(env.RELAY_PAIRING_CODE),
      desktop_polling: Boolean(env.RELAY_DESKTOP_TOKEN),
      cellular_network_supported: true,
      app_store_distribution_supported: true
    },
    counts: {
      registered_devices: state.devices.length,
      active_devices: state.devices.filter((device) => !device.disabled).length,
      disabled_devices: state.devices.filter((device) => device.disabled).length,
      mobile_messages: state.mobile_messages.length,
      desktop_replies: state.desktop_replies.length,
      queued_commands: counts.queued,
      unresolved_commands: counts.unresolved,
      total_commands: state.commands.length
    },
    desktop_heartbeat: heartbeat,
    desktop_online: isDesktopHeartbeatFresh(heartbeat),
    server_time: now(),
    safety: safety()
  });
}

async function registerDevice(store, env, body) {
  if (!env.RELAY_PAIRING_CODE) return response(503, { ok: false, error: "Relay pairing code is not configured.", safety: safety() });
  if (String(body.pairing_code || body.pairingCode || "") !== env.RELAY_PAIRING_CODE) {
    return response(401, { ok: false, error: "Invalid relay pairing code.", safety: safety() });
  }
  const state = await readState(store);
  const token = `mob_${randomHex(24)}`;
  const clientInstanceId = cleanClientInstanceId(body.client_instance_id || body.clientInstanceId);
  const clientInstanceHash = clientInstanceId ? sha256(clientInstanceId) : "";
  const replacedDevices = clientInstanceHash ? replaceDevicesForClientInstance(state, clientInstanceHash) : [];
  const device = {
    device_id: `dev-${Date.now()}-${randomHex(3)}`,
    display_name: cleanName(body.display_name || body.displayName || "Mobile"),
    token_hash: sha256(token),
    client_instance_hash: clientInstanceHash || null,
    created_at: now(),
    disabled: false
  };
  state.devices.push(device);
  audit(state, "device_registered", {
    device_id: device.device_id,
    display_name: device.display_name,
    replaced_device_ids: replacedDevices.map((item) => item.device_id)
  });
  await writeState(store, state);
  return response(201, {
    ok: true,
    service: "codex-relay-cloud",
    device: publicDevice(device),
    token,
    replaced_devices: replacedDevices.map(publicDevice),
    safety: safety()
  });
}

async function revokeMobileDevice(store, _env, headers) {
  const state = await readState(store);
  const device = requireMobileDevice(state, headers);
  if (!device) return response(401, { ok: false, error: "Invalid relay mobile token.", safety: safety() });
  device.disabled = true;
  device.disabled_at = now();
  audit(state, "device_revoked", { device_id: device.device_id, display_name: device.display_name });
  await writeState(store, state);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    device: publicDevice(device),
    safety: safety()
  });
}

async function postMobileMessage(store, env, headers, body) {
  const state = await readState(store);
  const device = requireMobileDevice(state, headers);
  if (!device) return response(401, { ok: false, error: "Invalid relay mobile token.", safety: safety() });
  const text = cleanText(body.text);
  if (!text) return response(400, { ok: false, error: "text is required.", safety: safety() });
  const clientMessageId = cleanClientMessageId(body.client_message_id || body.clientMessageId);
  const duplicate = clientMessageId
    ? state.mobile_messages.find((item) => item.device_id === device.device_id && item.client_message_id === clientMessageId)
    : null;
  if (duplicate) {
    if (duplicate.text_sha256 !== sha256(text)) {
      return response(409, {
        ok: false,
        error: "client_message_id already exists with different text.",
        safety: safety()
      });
    }
    return response(200, {
      ok: true,
      service: "codex-relay-cloud",
      duplicate: true,
      message: duplicate,
      safety: safety()
    });
  }

  const message = {
    relay_id: `msg-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    direction: "mobile_to_desktop",
    device_id: device.device_id,
    client_message_id: clientMessageId || null,
    display_name: device.display_name,
    text,
    text_sha256: sha256(text)
  };
  const commandResult = processCommand(text, { device, message, state });
  const reply = {
    relay_id: `reply-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    direction: "desktop_to_mobile",
    target_device_id: device.device_id,
    in_reply_to: message.relay_id,
    display_name: "Cloud Relay",
    text: commandResult.reply_text,
    text_sha256: sha256(commandResult.reply_text),
    command_id: commandResult.command?.command_id || null
  };

  state.mobile_messages.push(message);
  if (commandResult.command) state.commands.push(commandResult.command);
  state.desktop_replies.push(reply);
  audit(state, "mobile_message", {
    device_id: device.device_id,
    relay_id: message.relay_id,
    client_message_id: clientMessageId || null,
    command_id: commandResult.command?.command_id || null
  });
  await writeState(store, state);
  return response(201, { ok: true, service: "codex-relay-cloud", duplicate: false, message, safety: safety() });
}

async function listMobileMessages(store, _env, headers, query) {
  const state = await readState(store);
  const device = requireMobileDevice(state, headers);
  if (!device) return response(401, { ok: false, error: "Invalid relay mobile token.", safety: safety() });
  const limit = Math.max(1, Math.min(200, Number(query.limit || 80)));
  const visible = state.desktop_replies.filter((reply) => !reply.target_device_id || reply.target_device_id === device.device_id);
  const requestedCursor = Math.max(0, Number(query.cursor || 0));
  const cursor = Math.min(requestedCursor, visible.length);
  const messages = visible.slice(cursor, cursor + limit);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    cursor,
    next_cursor: cursor + messages.length,
    total_visible: visible.length,
    cursor_reset: requestedCursor !== cursor,
    messages,
    safety: safety()
  });
}

async function listMobileTranscript(store, _env, headers, query) {
  const state = await readState(store);
  const device = requireMobileDevice(state, headers);
  if (!device) return response(401, { ok: false, error: "Invalid relay mobile token.", safety: safety() });
  const limit = Math.max(1, Math.min(200, Number(query.limit || 80)));
  const transcript = transcriptForDevice(state, device);
  const messages = transcript.slice(-limit);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    total_visible: transcript.length,
    messages,
    safety: safety()
  });
}

async function getMobileMessageStatus(store, _env, headers, query) {
  const state = await readState(store);
  const device = requireMobileDevice(state, headers);
  if (!device) return response(401, { ok: false, error: "Invalid relay mobile token.", safety: safety() });
  const relayId = cleanText(query.relay_id || query.relayId || "");
  if (!relayId) return response(400, { ok: false, error: "relay_id is required.", safety: safety() });
  const message = state.mobile_messages.find((item) => item.relay_id === relayId && item.device_id === device.device_id);
  if (!message) return response(404, { ok: false, error: "relay message not found for this device.", safety: safety() });

  const command = state.commands.find((item) => item.relay_id === message.relay_id && item.device_id === device.device_id) || null;
  const replies = repliesForMessage(state, device, message.relay_id);
  const terminalReply = findTerminalReplyForMessage(message, state);
  const workingReply = [...replies].reverse().find((reply) => cleanText(reply.worker_status || "") === "working") || null;
  const latestReply = replies.at(-1) || null;
  const consumed = command ? isCommandConsumedByDesktopPoll(command, state) : false;
  const pending = command ? isCommandPending(command, state) : false;

  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    relay_id: message.relay_id,
    phase: messageReceiptPhase({ command, terminalReply, workingReply, consumed }),
    pending,
    cloud_ack: true,
    desktop: {
      online: isDesktopHeartbeatFresh(normalizeDesktopHeartbeat(state.desktop_heartbeat)),
      consumed,
      heartbeat: normalizeDesktopHeartbeat(state.desktop_heartbeat)
    },
    command: command ? {
      command_id: command.command_id,
      type: command.type,
      created_at: command.created_at,
      worker_status: cleanText(command.worker_status || "")
    } : null,
    latest_reply: latestReply || null,
    terminal_reply: terminalReply || null,
    safety: safety()
  });
}

async function listDesktopCommands(store, env, headers, query) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const limit = Math.max(1, Math.min(200, Number(query.limit || 80)));
  const includeDone = String(query.include_done || query.includeDone || "") === "1";
  const commands = includeDone ? state.commands : state.commands.filter((command) => isCommandPending(command, state));
  const requestedCursor = Math.max(0, Number(query.cursor || 0));
  const cursor = Math.min(requestedCursor, commands.length);
  const page = commands.slice(cursor, cursor + limit);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    cursor,
    next_cursor: cursor + page.length,
    total_visible: commands.length,
    cursor_reset: requestedCursor !== cursor,
    commands: page,
    safety: safety()
  });
}

async function listDesktopPoll(store, env, headers, query) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const limit = Math.max(1, Math.min(200, Number(query.limit || 80)));
  const requestedCursor = Math.max(0, Number(query.cursor || 0));
  const cursor = Math.min(requestedCursor, state.mobile_messages.length);
  const messages = state.mobile_messages.slice(cursor, cursor + limit);
  return response(200, {
    ok: true,
    service: "codex-relay-cloud",
    cursor,
    next_cursor: cursor + messages.length,
    total_visible: state.mobile_messages.length,
    cursor_reset: requestedCursor !== cursor,
    messages,
    safety: safety()
  });
}

async function postDesktopReply(store, env, headers, body) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  if (!body.command_id) return postDirectDesktopReply(state, store, body);
  const command = state.commands.find((item) => item.command_id === body.command_id) || null;
  if (!command) return response(404, { ok: false, error: "command_id not found.", safety: safety() });
  const text = cleanText(body.text);
  if (!text) return response(400, { ok: false, error: "text is required.", safety: safety() });
  const reply = {
    relay_id: `worker-reply-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    direction: "desktop_to_mobile",
    target_device_id: command.device_id || body.target_device_id || null,
    in_reply_to: command.relay_id || null,
    display_name: cleanName(body.display_name || "Desktop Codex Worker"),
    text,
    text_sha256: sha256(text),
    worker_for_command_id: command.command_id,
    worker_status: cleanText(body.worker_status || "processed")
  };
  command.worker_status = reply.worker_status;
  command.worker_reply_id = reply.relay_id;
  command.worker_processed_at = reply.created_at;
  state.desktop_replies.push(reply);
  audit(state, "desktop_reply", { command_id: command.command_id, reply_id: reply.relay_id, worker_status: reply.worker_status });
  await writeState(store, state);
  return response(201, { ok: true, service: "codex-relay-cloud", reply, safety: safety() });
}

async function postDirectDesktopReply(state, store, body) {
  const text = cleanText(body.text);
  if (!text) return response(400, { ok: false, error: "text is required.", safety: safety() });
  const sourceMessage = body.in_reply_to
    ? state.mobile_messages.find((item) => item.relay_id === body.in_reply_to)
    : null;
  const reply = {
    relay_id: `worker-reply-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    direction: "desktop_to_mobile",
    target_device_id: body.target_device_id || sourceMessage?.device_id || null,
    in_reply_to: body.in_reply_to || sourceMessage?.relay_id || null,
    display_name: cleanName(body.display_name || "Desktop Codex Worker"),
    text,
    text_sha256: sha256(text),
    worker_for_command_id: null,
    worker_status: cleanText(body.worker_status || "processed")
  };
  markDirectReplyCommand(state, reply);
  state.desktop_replies.push(reply);
  audit(state, "desktop_direct_reply", {
    reply_id: reply.relay_id,
    target_device_id: reply.target_device_id,
    in_reply_to: reply.in_reply_to,
    worker_status: reply.worker_status
  });
  await writeState(store, state);
  return response(201, { ok: true, service: "codex-relay-cloud", reply, safety: safety() });
}

async function postDesktopAudit(store, env, headers, body) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const entry = audit(state, cleanText(body.type || "desktop_audit"), {
    command_id: cleanText(body.command_id || ""),
    note: cleanText(body.note || ""),
    worker_status: cleanText(body.worker_status || "")
  });
  await writeState(store, state);
  return response(201, { ok: true, service: "codex-relay-cloud", audit: entry, safety: safety() });
}

async function postDesktopReconcile(store, env, headers, body) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const staleAfterMs = staleAfterMsFromBody(body);
  const closed = [];
  for (const command of state.commands) {
    if (!isCommandReconcileCandidate(command, state, staleAfterMs)) continue;
    const reply = staleClosureReply(command);
    command.worker_status = reply.worker_status;
    command.worker_reply_id = reply.relay_id;
    command.worker_processed_at = reply.created_at;
    state.desktop_replies.push(reply);
    audit(state, "desktop_reconcile_stale_command", {
      command_id: command.command_id,
      reply_id: reply.relay_id,
      worker_status: reply.worker_status
    });
    closed.push({
      command_id: command.command_id,
      relay_id: command.relay_id,
      worker_status: reply.worker_status,
      reply_id: reply.relay_id
    });
  }
  await writeState(store, state);
  return response(201, {
    ok: true,
    service: "codex-relay-cloud",
    command: "desktop_reconcile",
    stale_after_ms: staleAfterMs,
    closed_count: closed.length,
    closed,
    safety: safety()
  });
}

async function postDesktopDeviceCleanup(store, env, headers, body) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const olderThanMs = cleanupOlderThanMs(body);
  const disabled = [];
  const nowMs = Date.now();
  for (const device of state.devices) {
    if (device.disabled || !isTemporaryDevice(device)) continue;
    const createdAtMs = Date.parse(device.created_at || "");
    const ageMs = Number.isFinite(createdAtMs) ? nowMs - createdAtMs : Number.POSITIVE_INFINITY;
    if (ageMs < olderThanMs) continue;
    device.disabled = true;
    device.disabled_at = now();
    device.disabled_reason = "temporary_device_cleanup";
    disabled.push(publicDevice(device));
  }
  if (disabled.length) {
    audit(state, "desktop_cleanup_temporary_devices", {
      older_than_ms: olderThanMs,
      disabled_device_ids: disabled.map((device) => device.device_id)
    });
  }
  await writeState(store, state);
  return response(201, {
    ok: true,
    service: "codex-relay-cloud",
    command: "desktop_device_cleanup",
    older_than_ms: olderThanMs,
    disabled_count: disabled.length,
    disabled_devices: disabled,
    active_devices: state.devices.filter((device) => !device.disabled).length,
    safety: safety()
  });
}

async function postDesktopHeartbeat(store, env, headers, body) {
  if (!requireDesktop(env, headers)) return response(401, { ok: false, error: "Invalid relay desktop token.", safety: safety() });
  const state = await readState(store);
  const heartbeat = {
    updated_at: now(),
    status: cleanText(body.status || "online").slice(0, 40) || "online",
    bridge: cleanText(body.bridge || body.service || "desktop-bridge").slice(0, 80),
    cursor: Number.isFinite(Number(body.cursor)) ? Number(body.cursor) : null,
    pending_count: Number.isFinite(Number(body.pending_count)) ? Number(body.pending_count) : 0,
    processed_count: Number.isFinite(Number(body.processed_count)) ? Number(body.processed_count) : 0,
    note: cleanText(body.note || "").slice(0, 240),
    safety: safety()
  };
  state.desktop_heartbeat = heartbeat;
  await writeState(store, state);
  return response(201, { ok: true, service: "codex-relay-cloud", heartbeat, safety: safety() });
}

function processCommand(value, context) {
  const text = cleanText(value);
  const command_id = `relaycmd-${Date.now()}-${randomHex(3)}`;

  if (/^(状态|\/status|status)$/i.test(text)) {
    const command = baseCommand(command_id, "status_check", text, context, { status: "queued_for_desktop_worker" });
    return {
      command,
      reply_text: cloudStatusReplyText(context.state)
    };
  }

  if (/^(诊断|\/diag|diag|health)$/i.test(text)) {
    const command = baseCommand(command_id, "diagnostic", text, context, { status: "queued_for_desktop_worker" });
    return {
      command,
      reply_text: "# App 诊断 已入云队列\n桌面 worker 会返回二段状态。\n安全: live_orders_enabled=false"
    };
  }

  if (/^(报告|\/report|report)$/i.test(text)) {
    const request_id = `manual-report-${Date.now()}-${randomHex(3)}`;
    const command = baseCommand(command_id, "manual_report_request", text, context, {
      request_id,
      target_queue: "manual-report-requests",
      status: "queued_for_desktop_worker",
      report_type: "daily_manual_report"
    });
    return {
      command,
      reply_text: [
        "# 报告请求已入云队列",
        `request_id: ${request_id}`,
        "桌面 worker 会读取本地报告并回写摘要。",
        "安全: live_orders_enabled=false；只生成/读取报告，不下单"
      ].join("\n")
    };
  }

  if (isPortfolioAnalysisRequest(text)) {
    const request_id = `portfolio-analysis-${Date.now()}-${randomHex(3)}`;
    const command = baseCommand(command_id, "portfolio_analysis_request", text, context, {
      request_id,
      target_queue: "assistant-investment-analysis",
      status: "queued_for_desktop_codex",
      report_type: "portfolio_analysis"
    });
    return {
      command,
      reply_text: [
        "# 收到，正在交给本机 Codex 分析",
        "我会读取本地投资资料和可用市场信息，生成手机可读的中文结论。",
        "完成后会直接回到这里；不会下单、不会转账。",
        "安全: live_orders_enabled=false"
      ].join("\n")
    };
  }

  if (/^(信号|\/signal|signal)(?:\s|$)/i.test(text)) {
    return queueMonitorCommand({ command_id, type: "current_signal_probe", label: "当前信号扫描", script: "active-alpha-paper-monitor/scripts/current_signal_probe.py", text, symbols: parseSymbols(text, "BTCUSDT"), context });
  }

  if (/^(多源|\/multi|multi|snapshot)(?:\s|$)/i.test(text)) {
    return queueMonitorCommand({ command_id, type: "multisource_snapshot", label: "多源 Alpha 快照", script: "active-alpha-paper-monitor/scripts/run_multisource_alpha_snapshot.py", text, symbols: parseSymbols(text, "BTCUSDT,SOLUSDT"), context });
  }

  if (/^(周目标|\/weekly|weekly)(?:\s|$)/i.test(text)) {
    return queueMonitorCommand({ command_id, type: "weekly_goal_lab", label: "周目标策略实验室", script: "active-alpha-paper-monitor/scripts/weekly_goal_strategy_lab.py", text, symbols: parseSymbols(text, "BTCUSDT,SOLUSDT"), context });
  }

  const decision = text.match(/^(确认|稍后|忽略)\s+(act-[A-Za-z0-9_-]+)/);
  if (decision) {
    const decisionMap = { "确认": "confirm", "稍后": "later", "忽略": "ignore" };
    const command = baseCommand(command_id, "action_decision", text, context, {
      action_id: decision[2],
      decision: decisionMap[decision[1]] || decision[1],
      effect: "audit_only_no_live_order"
    });
    return {
      command,
      reply_text: `# Action 决策已入云审计队列\n${decision[2]}: ${decision[1]}\n安全: 仅审计，不下单`
    };
  }

  const command = baseCommand(command_id, "assistant_inbox_message", text, context, {
    target_queue: "assistant-inbox",
    status: "queued_for_desktop_codex"
  });
  return {
    command,
    reply_text: [
      "# 收到，已转给本机 Codex",
      "桌面 worker 会尝试生成可读回复；如果需要投资分析，可直接说“分析持仓”或“今天市场怎么看”。",
      "安全: live_orders_enabled=false"
    ].join("\n")
  };
}

function cloudStatusReplyText(state) {
  const normalized = normalizeState(state);
  const heartbeat = normalizeDesktopHeartbeat(normalized.desktop_heartbeat);
  const desktopOnline = isDesktopHeartbeatFresh(heartbeat);
  const counts = commandCounts(normalized);
  const heartbeatAge = heartbeat?.updated_at ? secondsAgo(heartbeat.updated_at) : null;
  return [
    "# App 状态",
    "云端: online",
    `电脑: ${desktopOnline ? "在线" : "离线或刚睡眠"}`,
    heartbeatAge === null ? "最近心跳: 暂无" : `最近心跳: ${heartbeatAge}s 前`,
    `队列: ${counts.queued} 条待处理 / ${counts.total} 条总指令`,
    counts.unresolved ? `未完成回写: ${counts.unresolved} 条（已被电脑消费或正在处理中）` : null,
    `手机设备: ${normalized.devices.length}`,
    "你可以发送“诊断”做完整 worker 回写测试，或发送“报告/分析持仓”。",
    "安全: live_orders_enabled=false；不会下单、不会转账"
  ].filter(Boolean).join("\n");
}

function isPortfolioAnalysisRequest(text) {
  const value = cleanText(text);
  if (!value) return false;
  const hasPortfolioIntent = /(持仓|仓位|组合|资产|账户|portfolio|position|holding)/i.test(value);
  const hasAnalysisIntent = /(分析|市场|今天|今日|怎么看|情况|建议|配置|调仓|复盘|报告|风险|买|卖|减仓|加仓)/i.test(value);
  const asksMarketReport = /(今天|今日|当前|现在).*(市场|行情|大盘|crypto|美股|币|投资)/i.test(value);
  return (hasPortfolioIntent && hasAnalysisIntent) || asksMarketReport;
}

function queueMonitorCommand({ command_id, type, label, script, text, symbols, context }) {
  const monitor_run_id = `monitor-request-${Date.now()}-${randomHex(3)}`;
  const command_preview = `python3 ${script} --symbols ${symbols.join(",")}`;
  const command = baseCommand(command_id, type, text, context, {
    monitor_run_id,
    task_id: type,
    label,
    status: "queued_for_desktop_worker",
    symbols,
    command_preview,
    allowed_script: script
  });
  return {
    command,
    reply_text: [
      `# ${label} 已入云队列`,
      `monitor_run_id: ${monitor_run_id}`,
      `symbols: ${symbols.join(", ")}`,
      `白名单脚本: ${script}`,
      "安全: live_orders_enabled=false；只排队分析任务，不下单"
    ].join("\n")
  };
}

function baseCommand(command_id, type, text, context, fields = {}) {
  return {
    command_id,
    created_at: now(),
    source: "codex-relay-cloud",
    type,
    text,
    relay_id: context.message?.relay_id || null,
    client_message_id: context.message?.client_message_id || null,
    device_id: context.device?.device_id || null,
    display_name: context.device?.display_name || null,
    ...fields,
    safety: safety()
  };
}

function replaceDevicesForClientInstance(state, clientInstanceHash) {
  const replaced = [];
  for (const device of state.devices) {
    if (device.disabled) continue;
    if (!device.client_instance_hash || device.client_instance_hash !== clientInstanceHash) continue;
    device.disabled = true;
    device.disabled_at = now();
    device.disabled_reason = "replaced_by_same_client_instance";
    replaced.push(device);
  }
  return replaced;
}

function isTemporaryDevice(device) {
  const name = cleanText(device?.display_name || "");
  return /^(installed-\d+|appctl-\d+|localtunnel-\d+|localhostrun-\d+|localhost-run-\d+|public-\d+|public-dns-override-\d+|content-check-\d+|exact-e2e-\d+|progress-e2e-\d+|final-e2e-\d+|netlify-fixed-e2e-\d+|netlify:|test phone$|receipt check$|public receipt check$|public instance check$|public chat continuity|playwright iphone$|tunnel iphone$)/i.test(name);
}

function requireMobileDevice(state, headers) {
  const token = bearer(headers);
  if (!token) return null;
  const hash = sha256(token);
  return state.devices.find((device) => device.token_hash === hash && !device.disabled) || null;
}

function requireDesktop(env, headers) {
  const expected = env.RELAY_DESKTOP_TOKEN;
  const actual = bearer(headers);
  return Boolean(expected && actual && safeEqual(expected, actual));
}

async function readState(store) {
  const value = await store.readJson(STATE_KEY, null);
  return normalizeState(value);
}

async function writeState(store, state) {
  const normalized = normalizeState(state);
  normalized.updated_at = now();
  await store.writeJson(STATE_KEY, compactState(normalized));
}

function normalizeState(value) {
  const state = value && typeof value === "object" ? value : {};
  return {
    version: 1,
    created_at: state.created_at || now(),
    updated_at: state.updated_at || now(),
    devices: Array.isArray(state.devices) ? state.devices : [],
    mobile_messages: Array.isArray(state.mobile_messages) ? state.mobile_messages : [],
    desktop_replies: Array.isArray(state.desktop_replies) ? state.desktop_replies : [],
    commands: Array.isArray(state.commands) ? state.commands : [],
    audit: normalizeAudit(state.audit),
    desktop_heartbeat: normalizeDesktopHeartbeat(state.desktop_heartbeat)
  };
}

function normalizeAudit(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((entry) => entry && typeof entry === "object" && entry.type !== "desktop_heartbeat")
    .slice(-MAX_AUDIT_ENTRIES);
}

function compactState(state) {
  const original = normalizeState(state);
  const requiredCommands = new Set();
  for (const command of original.commands) {
    if (isCommandPending(command, original)) requiredCommands.add(command.command_id);
  }

  const commands = keepRecentWithRequired(
    original.commands,
    MAX_COMMANDS,
    (command) => command.command_id,
    (command) => requiredCommands.has(command.command_id)
  );
  const retainedCommandIds = new Set(commands.map((command) => command.command_id).filter(Boolean));
  const requiredRelayIds = new Set(commands.map((command) => command.relay_id).filter(Boolean));

  const desktopReplies = keepRecentWithRequired(
    original.desktop_replies,
    MAX_DESKTOP_REPLIES,
    (reply) => reply.relay_id,
    (reply) => {
      if (cleanText(reply?.worker_status || "") === "working") return true;
      if (reply.worker_for_command_id && retainedCommandIds.has(reply.worker_for_command_id)) return true;
      if (reply.in_reply_to && requiredRelayIds.has(reply.in_reply_to)) return true;
      return false;
    }
  );
  desktopReplies.forEach((reply) => {
    if (reply.in_reply_to) requiredRelayIds.add(reply.in_reply_to);
  });

  const mobileMessages = keepRecentWithRequired(
    original.mobile_messages,
    MAX_MOBILE_MESSAGES,
    (message) => message.relay_id,
    (message) => requiredRelayIds.has(message.relay_id)
  );

  const referencedDeviceIds = new Set();
  commands.forEach((command) => {
    if (command.device_id) referencedDeviceIds.add(command.device_id);
  });
  mobileMessages.forEach((message) => {
    if (message.device_id) referencedDeviceIds.add(message.device_id);
  });
  desktopReplies.forEach((reply) => {
    if (reply.target_device_id) referencedDeviceIds.add(reply.target_device_id);
  });

  const activeDevices = original.devices.filter((device) => !device.disabled);
  const disabledDevices = keepRecentWithRequired(
    original.devices.filter((device) => device.disabled),
    MAX_DISABLED_DEVICES,
    (device) => device.device_id,
    (device) => referencedDeviceIds.has(device.device_id)
  );

  return {
    ...original,
    devices: [...activeDevices, ...disabledDevices],
    mobile_messages: mobileMessages,
    desktop_replies: desktopReplies,
    commands
  };
}

function keepRecentWithRequired(items, maxItems, keyFn, requiredFn) {
  const requiredKeys = new Set();
  items.forEach((item) => {
    const key = keyFn(item);
    if (key && requiredFn(item)) requiredKeys.add(key);
  });

  const keepKeys = new Set(requiredKeys);
  const sorted = items
    .map((item, index) => ({ item, index, time: Date.parse(item?.created_at || item?.disabled_at || "") }))
    .sort((left, right) => {
      const timeDelta = (Number.isFinite(right.time) ? right.time : 0) - (Number.isFinite(left.time) ? left.time : 0);
      return timeDelta || right.index - left.index;
    });

  for (const { item } of sorted) {
    if (keepKeys.size >= maxItems && !requiredKeys.has(keyFn(item))) continue;
    const key = keyFn(item);
    if (key) keepKeys.add(key);
  }

  return items.filter((item) => keepKeys.has(keyFn(item)));
}

function normalizeDesktopHeartbeat(value) {
  if (!value || typeof value !== "object") return null;
  return {
    updated_at: cleanText(value.updated_at || ""),
    status: cleanText(value.status || "unknown").slice(0, 40),
    bridge: cleanText(value.bridge || "desktop-bridge").slice(0, 80),
    cursor: Number.isFinite(Number(value.cursor)) ? Number(value.cursor) : null,
    pending_count: Number.isFinite(Number(value.pending_count)) ? Number(value.pending_count) : 0,
    processed_count: Number.isFinite(Number(value.processed_count)) ? Number(value.processed_count) : 0,
    note: cleanText(value.note || "").slice(0, 240),
    safety: safety()
  };
}

function isDesktopHeartbeatFresh(heartbeat) {
  if (!heartbeat?.updated_at) return false;
  const ageMs = Date.now() - Date.parse(heartbeat.updated_at);
  return Number.isFinite(ageMs) && ageMs >= 0 && ageMs < 90000 && heartbeat.status !== "failed";
}

function isCommandPending(command, state) {
  const status = cleanText(command?.worker_status || "");
  if (status) return status === "working";
  return !findTerminalReplyForCommand(command, state);
}

function commandCounts(state) {
  const counts = { queued: 0, unresolved: 0, total: state.commands.length };
  for (const command of state.commands) {
    if (!isCommandPending(command, state)) continue;
    if (isCommandWorking(command, state) || isCommandConsumedByDesktopPoll(command, state)) {
      counts.unresolved += 1;
    } else {
      counts.queued += 1;
    }
  }
  return counts;
}

function transcriptForDevice(state, device) {
  const combined = [];
  (state.mobile_messages || []).forEach((message, index) => {
    if (message.device_id !== device.device_id) return;
    combined.push({ ...message, _relay_sequence: index * 2 });
  });
  (state.desktop_replies || []).forEach((reply, index) => {
    if (reply.target_device_id && reply.target_device_id !== device.device_id) return;
    combined.push({ ...reply, _relay_sequence: index * 2 + 1 });
  });
  return combined
    .sort((left, right) => {
      const timeDelta = Date.parse(left.created_at || "") - Date.parse(right.created_at || "");
      if (timeDelta) return timeDelta;
      return left._relay_sequence - right._relay_sequence;
    })
    .map(({ _relay_sequence, ...message }) => message);
}

function repliesForMessage(state, device, relayId) {
  return (state.desktop_replies || []).filter((reply) => {
    if (reply.in_reply_to !== relayId) return false;
    if (reply.target_device_id && reply.target_device_id !== device.device_id) return false;
    return true;
  });
}

function findTerminalReplyForMessage(message, state) {
  return [...(state.desktop_replies || [])].reverse().find((reply) => {
    const status = cleanText(reply?.worker_status || "");
    if (!status || status === "working") return false;
    if (reply.in_reply_to !== message.relay_id) return false;
    if (reply.target_device_id && reply.target_device_id !== message.device_id) return false;
    return true;
  }) || null;
}

function messageReceiptPhase({ command, terminalReply, workingReply, consumed }) {
  if (terminalReply) return "completed";
  if (workingReply || cleanText(command?.worker_status || "") === "working") return "working";
  if (consumed) return "consumed";
  if (command) return "queued";
  return "cloud_confirmed";
}

function isCommandWorking(command, state) {
  if (cleanText(command?.worker_status || "") === "working") return true;
  return (state.desktop_replies || []).some((reply) => {
    if (cleanText(reply?.worker_status || "") !== "working") return false;
    if (command.command_id && reply.worker_for_command_id === command.command_id) return true;
    if (command.relay_id && reply.in_reply_to === command.relay_id) return true;
    return false;
  });
}

function isCommandConsumedByDesktopPoll(command, state) {
  const heartbeat = normalizeDesktopHeartbeat(state.desktop_heartbeat);
  if (!heartbeat || heartbeat.pending_count > 0 || heartbeat.cursor === null) return false;
  const index = (state.mobile_messages || []).findIndex((message) => message.relay_id === command.relay_id);
  return index >= 0 && heartbeat.cursor > index;
}

function findTerminalReplyForCommand(command, state) {
  if (!command) return null;
  return (state.desktop_replies || []).find((reply) => {
    const status = cleanText(reply?.worker_status || "");
    if (!status || status === "working") return false;
    if (command.command_id && reply.worker_for_command_id === command.command_id) return true;
    if (command.relay_id && reply.in_reply_to === command.relay_id) return true;
    return false;
  }) || null;
}

function markDirectReplyCommand(state, reply) {
  if (!reply?.in_reply_to || reply.worker_status === "working") return null;
  const command = state.commands.find((item) => item.relay_id === reply.in_reply_to) || null;
  if (!command) return null;
  command.worker_status = reply.worker_status;
  command.worker_reply_id = reply.relay_id;
  command.worker_processed_at = reply.created_at;
  return command;
}

function isCommandReconcileCandidate(command, state, staleAfterMs) {
  if (!isCommandPending(command, state)) return false;
  if (!isCommandConsumedByDesktopPoll(command, state)) return false;
  if (findTerminalReplyForCommand(command, state)) return false;
  return commandAgeMs(command) >= staleAfterMs;
}

function commandAgeMs(command) {
  const createdMs = Date.parse(command?.created_at || "");
  if (!Number.isFinite(createdMs)) return -1;
  return Math.max(0, Date.now() - createdMs);
}

function staleAfterMsFromBody(body) {
  const raw = Number(body.stale_after_ms ?? body.staleAfterMs ?? 30 * 60 * 1000);
  if (!Number.isFinite(raw)) return 30 * 60 * 1000;
  return Math.max(0, Math.min(Math.round(raw), 7 * 24 * 60 * 60 * 1000));
}

function cleanupOlderThanMs(body) {
  const raw = Number(body.older_than_ms ?? body.olderThanMs ?? 10 * 60 * 1000);
  if (!Number.isFinite(raw)) return 10 * 60 * 1000;
  return Math.max(0, Math.min(Math.round(raw), 30 * 24 * 60 * 60 * 1000));
}

function staleClosureReply(command) {
  const text = [
    "# 历史消息已收口",
    "这条消息此前已被桌面 bridge 消费，但没有留下终态回写；现在已标记为历史未闭环收口。",
    "新消息不受影响。你可以重新发送“状态”“诊断”或具体请求。",
    "安全: live_orders_enabled=false；不会下单、不会转账"
  ].join("\n");
  return {
    relay_id: `worker-reply-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    direction: "desktop_to_mobile",
    target_device_id: command.device_id || null,
    in_reply_to: command.relay_id || null,
    display_name: "Desktop Codex Worker",
    text,
    text_sha256: sha256(text),
    worker_for_command_id: command.command_id || null,
    worker_status: "stale_consumed_closed"
  };
}

function secondsAgo(value) {
  const ageMs = Date.now() - Date.parse(value);
  if (!Number.isFinite(ageMs) || ageMs < 0) return 0;
  return Math.round(ageMs / 1000);
}

function audit(state, type, fields = {}) {
  const entry = {
    audit_id: `audit-${Date.now()}-${randomHex(3)}`,
    created_at: now(),
    source: "codex-relay-cloud",
    type,
    ...fields,
    safety: safety()
  };
  state.audit.push(entry);
  return entry;
}

function parseSymbols(text, fallback) {
  const withoutCommand = cleanText(text)
    .replace(/^(信号|\/signal|signal|多源|\/multi|multi|snapshot|周目标|\/weekly|weekly)(?:\s|$)/i, "")
    .trim();
  const raw = withoutCommand || fallback;
  return [...new Set(raw
    .split(/[,\s，、]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
    .map((item) => item.replace(/[^A-Z0-9]/g, ""))
    .filter(Boolean)
    .map((item) => /USDT$|USD$|USDC$/.test(item) ? item : `${item}USDT`))]
    .slice(0, 8);
}

function publicDevice(device) {
  return {
    device_id: device.device_id,
    display_name: device.display_name,
    created_at: device.created_at,
    disabled_at: device.disabled_at || null,
    disabled: Boolean(device.disabled)
  };
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

function response(status, body) {
  return { status, body };
}

function normalizePath(path) {
  return String(path || "").replace(/\/+$/, "") || "/";
}

function bearer(headers) {
  const raw = headers.authorization || headers.Authorization || "";
  return String(raw).replace(/^Bearer\s+/i, "").trim();
}

function safeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 4000);
}

function cleanClientMessageId(value) {
  return String(value || "").replace(/[^A-Za-z0-9._:-]/g, "").trim().slice(0, 120);
}

function cleanClientInstanceId(value) {
  return String(value || "").replace(/[^A-Za-z0-9._:-]/g, "").trim().slice(0, 160);
}

function cleanName(value) {
  return String(value || "Mobile").replace(/\s+/g, " ").trim().slice(0, 80) || "Mobile";
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString("hex");
}

function now() {
  return new Date().toISOString();
}
