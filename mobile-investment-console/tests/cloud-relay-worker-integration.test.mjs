import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createStandaloneRelayServer } from "../scripts/cloud-relay-standalone-server.mjs";

test("cloud relay worker consumes standalone server commands and posts replies", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-worker-"));
  const server = createStandaloneRelayServer({
    statePath: path.join(tempDir, "state.json"),
    env: {
      RELAY_PAIRING_CODE: "pair-123",
      RELAY_DESKTOP_TOKEN: "desk-123"
    }
  });
  const baseUrl = await listen(server);
  try {
    const registered = await post(`${baseUrl}/api/relay/devices/register`, {
      display_name: "test phone",
      pairing_code: "pair-123"
    });
    await post(`${baseUrl}/api/relay/mobile/messages`, { text: "\u4fe1\u53f7 BTC" }, registered.token);

    const worker = await runNode(["scripts/cloud-relay-worker.cjs"], {
      CLOUD_RELAY_BASE_URL: baseUrl,
      RELAY_DESKTOP_TOKEN: "desk-123",
      CLOUD_RELAY_WORKER_STATE: path.join(tempDir, "worker-state.json"),
      CLOUD_RELAY_WORKER_EVENTS: path.join(tempDir, "worker-events.jsonl"),
      STANDALONE_RELAY_REPO_ROOT: process.cwd()
    });
    assert.equal(worker.code, 0, worker.stderr || worker.stdout);
    assert.match(worker.stdout, /dry_run_recorded/);

    const inbox = await get(`${baseUrl}/api/relay/mobile/messages`, registered.token);
    const workerReply = inbox.messages.find((message) => message.worker_status === "dry_run_recorded");
    assert.ok(workerReply);
    assert.match(workerReply.text, /dry-run cloud queue consumer/);
    const status = await getJson(`${baseUrl}/api/relay/status`);
    assert.equal(status.desktop_heartbeat.bridge, "local-worker");
    assert.equal(status.desktop_online, true);
  } finally {
    await close(server);
  }
});

test("cloud relay worker answers status command with local status", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-worker-status-"));
  const server = createStandaloneRelayServer({
    statePath: path.join(tempDir, "state.json"),
    env: {
      RELAY_PAIRING_CODE: "pair-123",
      RELAY_DESKTOP_TOKEN: "desk-123"
    }
  });
  const baseUrl = await listen(server);
  try {
    const registered = await post(`${baseUrl}/api/relay/devices/register`, {
      display_name: "test phone",
      pairing_code: "pair-123"
    });
    await post(`${baseUrl}/api/relay/mobile/messages`, { text: "\u72b6\u6001" }, registered.token);

    const worker = await runNode(["scripts/cloud-relay-worker.cjs"], {
      CLOUD_RELAY_BASE_URL: baseUrl,
      RELAY_DESKTOP_TOKEN: "desk-123",
      CLOUD_RELAY_WORKER_STATE: path.join(tempDir, "worker-state.json"),
      CLOUD_RELAY_WORKER_EVENTS: path.join(tempDir, "worker-events.jsonl"),
      STANDALONE_RELAY_REPO_ROOT: process.cwd()
    });
    assert.equal(worker.code, 0, worker.stderr || worker.stdout);
    assert.match(worker.stdout, /status_check/);

    const inbox = await get(`${baseUrl}/api/relay/mobile/messages`, registered.token);
    const cloudReply = inbox.messages.find((message) => /# App 状态/.test(message.text));
    assert.ok(cloudReply);
    const workerReply = inbox.messages.find((message) => message.worker_status === "ok" && /# 本机状态/.test(message.text));
    assert.ok(workerReply);
    assert.match(workerReply.text, /worker: online/);
    assert.match(workerReply.text, /队列消费: ok/);
    assert.doesNotMatch(workerReply.text, /desk-|mob_/);
  } finally {
    await close(server);
  }
});

test("cloud relay worker answers natural language portfolio requests with readable text", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-worker-codex-"));
  const fakeCodex = path.join(tempDir, "fake-codex.sh");
  await fs.writeFile(fakeCodex, [
    "#!/bin/sh",
    "out=''",
    "while [ \"$#\" -gt 0 ]; do",
    "  if [ \"$1\" = '--output-last-message' ]; then",
    "    shift",
    "    out=\"$1\"",
    "  fi",
    "  shift",
    "done",
    "cat >/dev/null",
    "printf '%s\\n' '一句话结论: 当前应先做持仓复盘，不自动下单。' '目前持仓/市场含义: 已按手机自然语言请求进入本机 Codex 分析。' '安全边界: live_orders_enabled=false。' > \"$out\"",
    "echo 'noisy codex stdout that must not be sent to phone'",
    "exit 0"
  ].join("\n"));
  await fs.chmod(fakeCodex, 0o755);

  const server = createStandaloneRelayServer({
    statePath: path.join(tempDir, "state.json"),
    env: {
      RELAY_PAIRING_CODE: "pair-123",
      RELAY_DESKTOP_TOKEN: "desk-123"
    }
  });
  const baseUrl = await listen(server);
  try {
    const registered = await post(`${baseUrl}/api/relay/devices/register`, {
      display_name: "test phone",
      pairing_code: "pair-123"
    });
    await post(`${baseUrl}/api/relay/mobile/messages`, {
      text: "\u6839\u636e\u4eca\u5929\u7684\u5e02\u573a\u4fe1\u606f \u5206\u6790\u4e00\u4e0b\u6211\u76ee\u524d\u7684\u6301\u4ed3\u60c5\u51b5"
    }, registered.token);

    const worker = await runNode(["scripts/cloud-relay-worker.cjs"], {
      CLOUD_RELAY_BASE_URL: baseUrl,
      RELAY_DESKTOP_TOKEN: "desk-123",
      CLOUD_RELAY_WORKER_STATE: path.join(tempDir, "worker-state.json"),
      CLOUD_RELAY_WORKER_EVENTS: path.join(tempDir, "worker-events.jsonl"),
      STANDALONE_RELAY_REPO_ROOT: process.cwd(),
      CODEX_BIN: fakeCodex,
      CLOUD_RELAY_CODEX_TIMEOUT_MS: "5000"
    });
    assert.equal(worker.code, 0, worker.stderr || worker.stdout);
    assert.match(worker.stdout, /answered/);

    const inbox = await get(`${baseUrl}/api/relay/mobile/messages`, registered.token);
    const workerReply = inbox.messages.find((message) => message.worker_status === "answered");
    assert.ok(workerReply);
    assert.match(workerReply.text, /一句话结论/);
    assert.match(workerReply.text, /live_orders_enabled=false/);
    assert.doesNotMatch(workerReply.text, /command_id|relaycmd-|noisy codex stdout/);
  } finally {
    await close(server);
  }
});

test("cloud relay worker posts repeated progress for slow portfolio analysis", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-worker-slow-codex-"));
  const fakeCodex = path.join(tempDir, "fake-slow-codex.sh");
  await fs.writeFile(fakeCodex, [
    "#!/bin/sh",
    "out=''",
    "while [ \"$#\" -gt 0 ]; do",
    "  if [ \"$1\" = '--output-last-message' ]; then",
    "    shift",
    "    out=\"$1\"",
    "  fi",
    "  shift",
    "done",
    "cat >/dev/null",
    "sleep 1",
    "printf '%s\\n' '一句话结论: 慢任务最终完成。' '安全边界: live_orders_enabled=false。' > \"$out\"",
    "exit 0"
  ].join("\n"));
  await fs.chmod(fakeCodex, 0o755);

  const server = createStandaloneRelayServer({
    statePath: path.join(tempDir, "state.json"),
    env: {
      RELAY_PAIRING_CODE: "pair-123",
      RELAY_DESKTOP_TOKEN: "desk-123"
    }
  });
  const baseUrl = await listen(server);
  try {
    const registered = await post(`${baseUrl}/api/relay/devices/register`, {
      display_name: "test phone",
      pairing_code: "pair-123"
    });
    await post(`${baseUrl}/api/relay/mobile/messages`, { text: "帮我分析一下持仓情况" }, registered.token);

    const worker = await runNode(["scripts/cloud-relay-worker.cjs"], {
      CLOUD_RELAY_BASE_URL: baseUrl,
      RELAY_DESKTOP_TOKEN: "desk-123",
      CLOUD_RELAY_WORKER_STATE: path.join(tempDir, "worker-state.json"),
      CLOUD_RELAY_WORKER_EVENTS: path.join(tempDir, "worker-events.jsonl"),
      STANDALONE_RELAY_REPO_ROOT: process.cwd(),
      CODEX_BIN: fakeCodex,
      CLOUD_RELAY_CODEX_TIMEOUT_MS: "5000",
      CLOUD_RELAY_CODEX_PROGRESS_INTERVAL_MS: "200"
    });
    assert.equal(worker.code, 0, worker.stderr || worker.stdout);

    const inbox = await get(`${baseUrl}/api/relay/mobile/messages`, registered.token);
    const progressReplies = inbox.messages.filter((message) => message.worker_status === "working");
    assert.ok(progressReplies.length >= 2, `expected at least 2 progress replies, saw ${progressReplies.length}`);
    assert.match(progressReplies.at(-1).text, /仍在处理持仓分析/);
    const finalReply = inbox.messages.find((message) => message.worker_status === "answered");
    assert.ok(finalReply);
    assert.match(finalReply.text, /慢任务最终完成/);
  } finally {
    await close(server);
  }
});

function runNode(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, {
      cwd: process.cwd(),
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve(`http://127.0.0.1:${address.port}`);
    });
  });
}

function close(server) {
  return new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

async function get(url, token) {
  const response = await fetch(url, { headers: { authorization: `Bearer ${token}` } });
  if (!response.ok) assert.fail(await response.text());
  return response.json();
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) assert.fail(await response.text());
  return response.json();
}

async function post(url, body, token) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body)
  });
  if (!response.ok) assert.fail(await response.text());
  return response.json();
}
