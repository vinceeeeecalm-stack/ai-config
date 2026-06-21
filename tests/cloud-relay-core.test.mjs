import test from "node:test";
import assert from "node:assert/strict";
import { handleRelayRequest } from "../src/cloud-relay-core.mjs";

test("cloud relay handles mobile command and desktop worker reply", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const status = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(status.status, 200);
  assert.equal(status.body.ready.mobile_registration, true);
  assert.equal(status.body.ready.desktop_polling, true);
  assert.equal(status.body.safety.live_orders_enabled, false);

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);
  assert.match(registered.body.token, /^mob_/);

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "信号 BTC" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(commands.status, 200);
  assert.equal(commands.body.commands.length, 1);
  assert.equal(commands.body.commands[0].type, "current_signal_probe");
  assert.deepEqual(commands.body.commands[0].symbols, ["BTCUSDT"]);

  const reply = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      command_id: commands.body.commands[0].command_id,
      worker_status: "dry_run_recorded",
      text: "worker ok"
    },
    store,
    env
  });
  assert.equal(reply.status, 201);

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "0" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(inbox.status, 200);
  assert.equal(inbox.body.messages.length, 2);
  assert.equal(inbox.body.messages[1].text, "worker ok");
  assert.equal(inbox.body.messages[1].worker_status, "dry_run_recorded");
});

test("desktop endpoints require desktop token", async () => {
  const result = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    headers: { authorization: "Bearer wrong" },
    store: memoryStore(),
    env: { RELAY_DESKTOP_TOKEN: "desk-123", RELAY_PAIRING_CODE: "pair-123" }
  });
  assert.equal(result.status, 401);
});

test("mobile device revoke disables the current mobile token", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  const before = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(before.status, 200);
  assert.equal(before.body.counts.registered_devices, 1);
  assert.equal(before.body.counts.active_devices, 1);
  assert.equal(before.body.counts.disabled_devices, 0);

  const revoked = await call({
    method: "POST",
    path: "/api/relay/mobile/device/revoke",
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(revoked.status, 200);
  assert.equal(revoked.body.device.disabled, true);
  assert.ok(revoked.body.device.disabled_at);

  const after = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(after.status, 200);
  assert.equal(after.body.counts.registered_devices, 1);
  assert.equal(after.body.counts.active_devices, 0);
  assert.equal(after.body.counts.disabled_devices, 1);

  const sendAfterRevoke = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "状态" },
    store,
    env
  });
  assert.equal(sendAfterRevoke.status, 401);

  const transcriptAfterRevoke = await call({
    method: "GET",
    path: "/api/relay/mobile/transcript",
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(transcriptAfterRevoke.status, 401);
});

test("registering the same mobile client instance replaces its old active token", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const first = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: {
      display_name: "Vincent iPhone",
      pairing_code: "pair-123",
      client_instance_id: "web-instance-001"
    },
    store,
    env
  });
  assert.equal(first.status, 201);
  assert.deepEqual(first.body.replaced_devices, []);

  const second = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: {
      display_name: "Vincent iPhone",
      pairing_code: "pair-123",
      client_instance_id: "web-instance-001"
    },
    store,
    env
  });
  assert.equal(second.status, 201);
  assert.equal(second.body.replaced_devices.length, 1);
  assert.equal(second.body.replaced_devices[0].device_id, first.body.device.device_id);
  assert.equal(second.body.replaced_devices[0].disabled, true);

  const oldTokenSend = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${first.body.token}` },
    body: { text: "状态" },
    store,
    env
  });
  assert.equal(oldTokenSend.status, 401);

  const newTokenSend = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${second.body.token}` },
    body: { text: "状态" },
    store,
    env
  });
  assert.equal(newTokenSend.status, 201);

  const other = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: {
      display_name: "Other Phone",
      pairing_code: "pair-123",
      client_instance_id: "web-instance-002"
    },
    store,
    env
  });
  assert.equal(other.status, 201);
  assert.deepEqual(other.body.replaced_devices, []);

  const status = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(status.status, 200);
  assert.equal(status.body.counts.registered_devices, 3);
  assert.equal(status.body.counts.active_devices, 2);
  assert.equal(status.body.counts.disabled_devices, 1);

  const state = await store.readJson("state.json", {});
  assert.ok(state.devices.every((device) => !/web-instance/.test(JSON.stringify(device))));
  assert.ok(state.devices.find((device) => device.device_id === first.body.device.device_id).client_instance_hash);
});

test("desktop cleanup disables only temporary verification devices", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  await store.writeJson("state.json", {
    devices: [
      {
        device_id: "real-phone",
        display_name: "Vincent iPhone",
        token_hash: "real-token",
        created_at: testTime(1),
        disabled: false
      },
      {
        device_id: "old-temp",
        display_name: "netlify-fixed-e2e-123",
        token_hash: "old-temp-token",
        created_at: testTime(2),
        disabled: false
      },
      {
        device_id: "playwright-temp",
        display_name: "Playwright iPhone",
        token_hash: "playwright-temp-token",
        created_at: testTime(3),
        disabled: false
      },
      {
        device_id: "new-temp",
        display_name: "installed-999",
        token_hash: "new-temp-token",
        created_at: new Date().toISOString(),
        disabled: false
      }
    ],
    mobile_messages: [],
    desktop_replies: [],
    commands: [],
    audit: []
  });

  const unauthorized = await call({
    method: "POST",
    path: "/api/relay/desktop/devices/cleanup",
    headers: { authorization: "Bearer wrong" },
    body: { older_than_ms: 1000 },
    store,
    env
  });
  assert.equal(unauthorized.status, 401);

  const cleanup = await call({
    method: "POST",
    path: "/api/relay/desktop/devices/cleanup",
    headers: { authorization: "Bearer desk-123" },
    body: { older_than_ms: 1000 },
    store,
    env
  });
  assert.equal(cleanup.status, 201);
  assert.equal(cleanup.body.disabled_count, 2);
  assert.deepEqual(cleanup.body.disabled_devices.map((device) => device.device_id).sort(), ["old-temp", "playwright-temp"]);
  assert.equal(cleanup.body.active_devices, 2);

  const firstState = await store.readJson("state.json", {});
  assert.equal(firstState.devices.find((device) => device.device_id === "real-phone").disabled, false);
  assert.equal(firstState.devices.find((device) => device.device_id === "old-temp").disabled, true);
  assert.equal(firstState.devices.find((device) => device.device_id === "playwright-temp").disabled, true);
  assert.equal(firstState.devices.find((device) => device.device_id === "new-temp").disabled, false);
  assert.equal(firstState.audit.at(-1).type, "desktop_cleanup_temporary_devices");

  const cleanupAllTemps = await call({
    method: "POST",
    path: "/api/relay/desktop/devices/cleanup",
    headers: { authorization: "Bearer desk-123" },
    body: { older_than_ms: 0 },
    store,
    env
  });
  assert.equal(cleanupAllTemps.status, 201);
  assert.equal(cleanupAllTemps.body.disabled_count, 1);

  const finalState = await store.readJson("state.json", {});
  assert.equal(finalState.devices.find((device) => device.device_id === "real-phone").disabled, false);
  assert.equal(finalState.devices.find((device) => device.device_id === "new-temp").disabled, true);
});

test("legacy desktop poll and direct replies remain compatible", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "Vincent Phone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "报告" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const unauthorizedPoll = await call({
    method: "GET",
    path: "/api/relay/desktop/poll",
    query: { cursor: "0" },
    headers: { authorization: "Bearer wrong" },
    store,
    env
  });
  assert.equal(unauthorizedPoll.status, 401);

  const polled = await call({
    method: "GET",
    path: "/api/relay/desktop/poll",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(polled.status, 200);
  assert.equal(polled.body.messages.length, 1);
  assert.equal(polled.body.messages[0].text, "报告");
  assert.equal(polled.body.next_cursor, 1);

  const queuedBeforeReply = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(queuedBeforeReply.status, 200);
  assert.equal(queuedBeforeReply.body.counts.queued_commands, 1);

  const workingReply = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      target_device_id: polled.body.messages[0].device_id,
      in_reply_to: polled.body.messages[0].relay_id,
      worker_status: "working",
      text: "本地报告正在处理中"
    },
    store,
    env
  });
  assert.equal(workingReply.status, 201);

  const queuedDuringWork = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(queuedDuringWork.status, 200);
  assert.equal(queuedDuringWork.body.counts.queued_commands, 0);
  assert.equal(queuedDuringWork.body.counts.unresolved_commands, 1);

  const bridgeHeartbeat = await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: {
      bridge: "netlify-local-bridge",
      status: "online",
      cursor: 1,
      pending_count: 0,
      processed_count: 1
    },
    store,
    env
  });
  assert.equal(bridgeHeartbeat.status, 201);

  const consumedButUnresolved = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(consumedButUnresolved.status, 200);
  assert.equal(consumedButUnresolved.body.counts.queued_commands, 0);
  assert.equal(consumedButUnresolved.body.counts.unresolved_commands, 1);

  const directReply = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      target_device_id: polled.body.messages[0].device_id,
      in_reply_to: polled.body.messages[0].relay_id,
      worker_status: "reported",
      text: "本地报告摘要已返回"
    },
    store,
    env
  });
  assert.equal(directReply.status, 201);
  assert.equal(directReply.body.reply.worker_status, "reported");
  assert.equal(directReply.body.reply.worker_for_command_id, null);

  const queuedAfterReply = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(queuedAfterReply.status, 200);
  assert.equal(queuedAfterReply.body.counts.queued_commands, 0);
  assert.equal(queuedAfterReply.body.counts.unresolved_commands, 0);

  const remainingCommands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(remainingCommands.status, 200);
  assert.equal(remainingCommands.body.commands.length, 0);

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "0" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(inbox.status, 200);
  assert.equal(inbox.body.messages.length, 3);
  assert.equal(inbox.body.messages[1].worker_status, "working");
  assert.equal(inbox.body.messages[2].text, "本地报告摘要已返回");
  assert.equal(inbox.body.messages[2].worker_status, "reported");
});

test("mobile messages are idempotent by client_message_id", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  const first = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "信号 BTC", client_message_id: "web-test-001" },
    store,
    env
  });
  assert.equal(first.status, 201);
  assert.equal(first.body.duplicate, false);
  assert.equal(first.body.message.client_message_id, "web-test-001");

  const repeated = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "信号 BTC", client_message_id: "web-test-001" },
    store,
    env
  });
  assert.equal(repeated.status, 200);
  assert.equal(repeated.body.duplicate, true);
  assert.equal(repeated.body.message.relay_id, first.body.message.relay_id);

  const status = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(status.status, 200);
  assert.equal(status.body.counts.mobile_messages, 1);
  assert.equal(status.body.counts.desktop_replies, 1);
  assert.equal(status.body.counts.total_commands, 1);
  assert.equal(status.body.counts.queued_commands, 1);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(commands.status, 200);
  assert.equal(commands.body.commands.length, 1);
  assert.equal(commands.body.commands[0].client_message_id, "web-test-001");

  const conflict = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "信号 ETH", client_message_id: "web-test-001" },
    store,
    env
  });
  assert.equal(conflict.status, 409);
});

test("mobile transcript restores recent bidirectional history for the current device", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const primary = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "Primary iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  const secondary = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "Other Phone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(primary.status, 201);
  assert.equal(secondary.status, 201);

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${primary.body.token}` },
    body: { text: "报告" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const otherSent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${secondary.body.token}` },
    body: { text: "信号 ETH" },
    store,
    env
  });
  assert.equal(otherSent.status, 201);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0", include_done: "1" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  const primaryCommand = commands.body.commands.find((command) => command.relay_id === sent.body.message.relay_id);
  assert.ok(primaryCommand);

  const workerReply = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      command_id: primaryCommand.command_id,
      worker_status: "reported",
      text: "报告摘要已返回"
    },
    store,
    env
  });
  assert.equal(workerReply.status, 201);

  const transcript = await call({
    method: "GET",
    path: "/api/relay/mobile/transcript",
    query: { limit: "80" },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(transcript.status, 200);
  assert.deepEqual(transcript.body.messages.map((message) => message.text), [
    "报告",
    transcript.body.messages[1].text,
    "报告摘要已返回"
  ]);
  assert.match(transcript.body.messages[1].text, /报告请求已入云队列/);
  assert.equal(transcript.body.messages.some((message) => /ETH/.test(message.text)), false);

  const recentOnly = await call({
    method: "GET",
    path: "/api/relay/mobile/transcript",
    query: { limit: "2" },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(recentOnly.status, 200);
  assert.deepEqual(recentOnly.body.messages.map((message) => message.text), [
    transcript.body.messages[1].text,
    "报告摘要已返回"
  ]);

  const unauthorized = await call({
    method: "GET",
    path: "/api/relay/mobile/transcript",
    headers: { authorization: "Bearer wrong" },
    store,
    env
  });
  assert.equal(unauthorized.status, 401);
});

test("natural language portfolio requests route to readable Codex analysis", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "根据今天的市场信息 分析一下我目前的持仓情况" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(commands.status, 200);
  assert.equal(commands.body.commands.length, 1);
  assert.equal(commands.body.commands[0].type, "portfolio_analysis_request");

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "0" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(inbox.status, 200);
  assert.match(inbox.body.messages[0].text, /本机 Codex 分析/);
  assert.doesNotMatch(inbox.body.messages[0].text, /command_id|relaycmd-/);
});

test("status command returns cloud status and queues worker status check", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: { bridge: "netlify-local-bridge", status: "online", pending_count: 0 },
    store,
    env
  });

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "状态" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(commands.status, 200);
  assert.equal(commands.body.commands.length, 1);
  assert.equal(commands.body.commands[0].type, "status_check");

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "0" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(inbox.status, 200);
  assert.match(inbox.body.messages[0].text, /# App 状态/);
  assert.match(inbox.body.messages[0].text, /电脑: 在线/);
  assert.match(inbox.body.messages[0].text, /队列:/);
  assert.doesNotMatch(inbox.body.messages[0].text, /desk-|mob_/);
});

test("mobile message status reports the desktop lifecycle for the owning device", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const primary = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "Primary iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  const secondary = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "Other Phone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(primary.status, 201);
  assert.equal(secondary.status, 201);

  const sent = await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${primary.body.token}` },
    body: { text: "报告" },
    store,
    env
  });
  assert.equal(sent.status, 201);

  const queued = await call({
    method: "GET",
    path: "/api/relay/mobile/message-status",
    query: { relay_id: sent.body.message.relay_id },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(queued.status, 200);
  assert.equal(queued.body.phase, "queued");
  assert.equal(queued.body.pending, true);
  assert.equal(queued.body.cloud_ack, true);
  assert.equal(queued.body.desktop.consumed, false);
  assert.equal(queued.body.command.type, "manual_report_request");
  assert.match(queued.body.latest_reply.text, /报告请求已入云队列/);
  assert.equal(queued.body.safety.live_orders_enabled, false);
  assert.doesNotMatch(JSON.stringify(queued.body), /mob_|desk-/);

  const forbidden = await call({
    method: "GET",
    path: "/api/relay/mobile/message-status",
    query: { relay_id: sent.body.message.relay_id },
    headers: { authorization: `Bearer ${secondary.body.token}` },
    store,
    env
  });
  assert.equal(forbidden.status, 404);

  const polled = await call({
    method: "GET",
    path: "/api/relay/desktop/poll",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(polled.status, 200);

  await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: { bridge: "netlify-local-bridge", status: "online", cursor: 1, pending_count: 0 },
    store,
    env
  });

  const consumed = await call({
    method: "GET",
    path: "/api/relay/mobile/message-status",
    query: { relay_id: sent.body.message.relay_id },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(consumed.status, 200);
  assert.equal(consumed.body.phase, "consumed");
  assert.equal(consumed.body.desktop.consumed, true);
  assert.equal(consumed.body.pending, true);

  const command = consumed.body.command;
  const working = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      command_id: command.command_id,
      worker_status: "working",
      text: "本地报告正在处理中"
    },
    store,
    env
  });
  assert.equal(working.status, 201);

  const workingStatus = await call({
    method: "GET",
    path: "/api/relay/mobile/message-status",
    query: { relay_id: sent.body.message.relay_id },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(workingStatus.status, 200);
  assert.equal(workingStatus.body.phase, "working");
  assert.equal(workingStatus.body.latest_reply.worker_status, "working");
  assert.equal(workingStatus.body.pending, true);

  const completedReply = await call({
    method: "POST",
    path: "/api/relay/desktop/replies",
    headers: { authorization: "Bearer desk-123" },
    body: {
      command_id: command.command_id,
      worker_status: "reported",
      text: "报告摘要已返回"
    },
    store,
    env
  });
  assert.equal(completedReply.status, 201);

  const completed = await call({
    method: "GET",
    path: "/api/relay/mobile/message-status",
    query: { relay_id: sent.body.message.relay_id },
    headers: { authorization: `Bearer ${primary.body.token}` },
    store,
    env
  });
  assert.equal(completed.status, 200);
  assert.equal(completed.body.phase, "completed");
  assert.equal(completed.body.pending, false);
  assert.equal(completed.body.terminal_reply.worker_status, "reported");
  assert.equal(completed.body.terminal_reply.text, "报告摘要已返回");
});

test("desktop reconcile closes stale consumed commands without closing fresh queue items", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "报告" },
    store,
    env
  });

  const freshReconcile = await call({
    method: "POST",
    path: "/api/relay/desktop/reconcile",
    headers: { authorization: "Bearer desk-123" },
    body: { stale_after_ms: 0 },
    store,
    env
  });
  assert.equal(freshReconcile.status, 201);
  assert.equal(freshReconcile.body.closed_count, 0);

  const freshStatus = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(freshStatus.body.counts.queued_commands, 1);
  assert.equal(freshStatus.body.counts.unresolved_commands, 0);

  const polled = await call({
    method: "GET",
    path: "/api/relay/desktop/poll",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(polled.status, 200);
  assert.equal(polled.body.messages.length, 1);

  await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: { bridge: "netlify-local-bridge", status: "online", cursor: 1, pending_count: 0 },
    store,
    env
  });

  const consumedStatus = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(consumedStatus.body.counts.queued_commands, 0);
  assert.equal(consumedStatus.body.counts.unresolved_commands, 1);

  const reconciled = await call({
    method: "POST",
    path: "/api/relay/desktop/reconcile",
    headers: { authorization: "Bearer desk-123" },
    body: { stale_after_ms: 0 },
    store,
    env
  });
  assert.equal(reconciled.status, 201);
  assert.equal(reconciled.body.closed_count, 1);
  assert.equal(reconciled.body.closed[0].worker_status, "stale_consumed_closed");

  const finalStatus = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(finalStatus.body.counts.queued_commands, 0);
  assert.equal(finalStatus.body.counts.unresolved_commands, 0);

  const commands = await call({
    method: "GET",
    path: "/api/relay/desktop/commands",
    query: { cursor: "0" },
    headers: { authorization: "Bearer desk-123" },
    store,
    env
  });
  assert.equal(commands.status, 200);
  assert.equal(commands.body.commands.length, 0);

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "0" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  const closure = inbox.body.messages.find((message) => message.worker_status === "stale_consumed_closed");
  assert.ok(closure);
  assert.match(closure.text, /历史消息已收口/);
});

test("mobile message cursor is clamped after app or storage reset", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const registered = await call({
    method: "POST",
    path: "/api/relay/devices/register",
    body: { display_name: "iPhone", pairing_code: "pair-123" },
    store,
    env
  });
  assert.equal(registered.status, 201);

  await call({
    method: "POST",
    path: "/api/relay/mobile/messages",
    headers: { authorization: `Bearer ${registered.body.token}` },
    body: { text: "诊断" },
    store,
    env
  });

  const inbox = await call({
    method: "GET",
    path: "/api/relay/mobile/messages",
    query: { cursor: "999", limit: "80" },
    headers: { authorization: `Bearer ${registered.body.token}` },
    store,
    env
  });
  assert.equal(inbox.status, 200);
  assert.equal(inbox.body.cursor_reset, true);
  assert.equal(inbox.body.cursor, 1);
  assert.equal(inbox.body.next_cursor, 1);
});

test("desktop heartbeat is visible in relay status", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  const heartbeat = await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: {
      bridge: "netlify-local-bridge",
      status: "online",
      cursor: 42,
      pending_count: 2,
      processed_count: 3
    },
    store,
    env
  });
  assert.equal(heartbeat.status, 201);

  const status = await call({ method: "GET", path: "/api/relay/status", store, env });
  assert.equal(status.status, 200);
  assert.equal(status.body.desktop_online, true);
  assert.equal(status.body.desktop_heartbeat.bridge, "netlify-local-bridge");
  assert.equal(status.body.desktop_heartbeat.pending_count, 2);
});

test("desktop heartbeat does not grow audit log", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };

  await store.writeJson("state.json", {
    audit: [
      { type: "desktop_heartbeat", created_at: "2026-01-01T00:00:00.000Z" },
      { type: "mobile_message", created_at: "2026-01-01T00:00:01.000Z" }
    ]
  });

  for (let index = 0; index < 5; index += 1) {
    const heartbeat = await call({
      method: "POST",
      path: "/api/relay/desktop/heartbeat",
      headers: { authorization: "Bearer desk-123" },
      body: {
        bridge: "local-worker",
        status: "online",
        pending_count: index
      },
      store,
      env
    });
    assert.equal(heartbeat.status, 201);
  }

  const state = await store.readJson("state.json", {});
  assert.deepEqual(state.audit.map((entry) => entry.type), ["mobile_message"]);
  assert.equal(state.desktop_heartbeat.pending_count, 4);
});

test("state compaction keeps pending work and bounded recent history", async () => {
  const store = memoryStore();
  const env = {
    RELAY_PAIRING_CODE: "pair-123",
    RELAY_DESKTOP_TOKEN: "desk-123"
  };
  const devices = [
    {
      device_id: "active-device",
      display_name: "Active Phone",
      token_hash: "active-token-hash",
      created_at: testTime(1000),
      disabled: false
    },
    ...Array.from({ length: 80 }, (_, index) => ({
      device_id: `disabled-${index}`,
      display_name: `Disabled ${index}`,
      token_hash: `disabled-token-${index}`,
      created_at: testTime(index),
      disabled_at: testTime(index + 1),
      disabled: true
    }))
  ];
  const mobileMessages = Array.from({ length: 620 }, (_, index) => ({
    relay_id: `msg-${index}`,
    created_at: testTime(index),
    direction: "mobile_to_desktop",
    device_id: index === 0 ? "disabled-0" : "active-device",
    display_name: "Phone",
    text: index === 0 ? "old pending" : `message ${index}`,
    text_sha256: `hash-${index}`
  }));
  const commands = Array.from({ length: 620 }, (_, index) => ({
    command_id: `cmd-${index}`,
    created_at: testTime(index),
    source: "codex-relay-cloud",
    type: "status_check",
    text: index === 0 ? "old pending" : `message ${index}`,
    relay_id: `msg-${index}`,
    device_id: index === 0 ? "disabled-0" : "active-device",
    worker_status: index === 0 ? "" : "ok",
    safety: { live_orders_enabled: false }
  }));
  const desktopReplies = Array.from({ length: 1200 }, (_, index) => {
    const messageIndex = index < 200 ? 1 : 320 + (index % 300);
    return {
      relay_id: `reply-${index}`,
      created_at: testTime(index + 1200),
      direction: "desktop_to_mobile",
      target_device_id: "active-device",
      in_reply_to: `msg-${messageIndex}`,
      display_name: "Desktop Codex Worker",
      text: `reply ${index}`,
      text_sha256: `reply-hash-${index}`,
      worker_for_command_id: `cmd-${messageIndex}`,
      worker_status: "ok"
    };
  });

  await store.writeJson("state.json", {
    devices,
    mobile_messages: mobileMessages,
    desktop_replies: desktopReplies,
    commands,
    audit: []
  });

  const heartbeat = await call({
    method: "POST",
    path: "/api/relay/desktop/heartbeat",
    headers: { authorization: "Bearer desk-123" },
    body: {
      bridge: "netlify-local-bridge",
      status: "online",
      cursor: 620,
      pending_count: 0
    },
    store,
    env
  });
  assert.equal(heartbeat.status, 201);

  const state = await store.readJson("state.json", {});
  assert.ok(state.mobile_messages.length <= 500);
  assert.ok(state.desktop_replies.length <= 1000);
  assert.ok(state.commands.length <= 500);
  assert.ok(state.devices.filter((device) => device.disabled).length <= 50);
  assert.ok(state.devices.find((device) => device.device_id === "active-device"));
  assert.ok(state.devices.find((device) => device.device_id === "disabled-0"));
  assert.ok(state.mobile_messages.find((message) => message.relay_id === "msg-0"));
  assert.ok(state.commands.find((command) => command.command_id === "cmd-0"));
  assert.equal(state.commands.some((command) => command.command_id === "cmd-1"), false);
});

async function call(input) {
  return handleRelayRequest({
    method: input.method,
    path: input.path,
    query: input.query || {},
    headers: input.headers || {},
    body: input.body || {},
    store: input.store,
    env: input.env
  });
}

function memoryStore() {
  const values = new Map();
  return {
    async readJson(key, fallback) {
      return values.has(key) ? structuredClone(values.get(key)) : fallback;
    },
    async writeJson(key, value) {
      values.set(key, structuredClone(value));
    }
  };
}

function testTime(offsetSeconds) {
  return new Date(Date.UTC(2026, 0, 1, 0, 0, offsetSeconds)).toISOString();
}
