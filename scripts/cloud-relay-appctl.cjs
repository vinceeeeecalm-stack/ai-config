#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

const repoRoot = path.resolve(__dirname, "..");
const serverScript = path.join(repoRoot, "scripts", "cloud-relay-standalone-server.mjs");
const workerScript = path.join(repoRoot, "scripts", "cloud-relay-worker.cjs");
const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
const baseUrl = process.env.CLOUD_RELAY_BASE_URL || `http://127.0.0.1:${port}`;
const statusPath = process.env.CLOUD_RELAY_APPCTL_STATUS || "/private/tmp/codex-relay-cloud-standalone-status.json";
const secretPath = process.env.CLOUD_RELAY_APPCTL_SECRET || "/private/tmp/codex-relay-cloud-standalone-secrets.json";
const statePath = process.env.CLOUD_RELAY_STATE_PATH || "/private/tmp/codex-relay-cloud-standalone-state.json";
const workerStatePath = process.env.CLOUD_RELAY_WORKER_STATE || "/private/tmp/codex-relay-cloud-standalone-worker-state.json";
const workerEventsPath = process.env.CLOUD_RELAY_WORKER_EVENTS || "/private/tmp/codex-relay-cloud-standalone-worker-events.jsonl";
const logPath = process.env.CLOUD_RELAY_APPCTL_LOG || "/private/tmp/codex-relay-cloud-standalone.log";

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "start" || command === "ensure") return printJson(await ensureStarted());
  if (command === "status") return printJson(await status());
  if (command === "e2e") return printJson(await e2e(process.argv.slice(3).join(" ") || "信号 BTC"));
  if (command === "worker-once") return printJson(await runWorkerOnce());
  if (command === "stop") return printJson(await stop());
  throw new Error(`Unknown command: ${command}`);
}

async function ensureStarted() {
  const current = await status().catch(() => null);
  if (current?.ok && current?.health?.ok) return current;

  const secrets = ensureSecrets();
  ensureParent(logPath);
  const log = fs.openSync(logPath, "a");
  const child = spawn(process.execPath, [serverScript], {
    cwd: repoRoot,
    detached: true,
    env: {
      ...process.env,
      CLOUD_RELAY_PORT: String(port),
      PORT: String(port),
      RELAY_PAIRING_CODE: secrets.pairing_code,
      RELAY_DESKTOP_TOKEN: secrets.desktop_token,
      CLOUD_RELAY_STATE_PATH: statePath
    },
    stdio: ["ignore", log, log]
  });
  child.unref();

  const health = await waitForHealth(baseUrl, 8000);
  const value = writeStatus({
    ok: true,
    service: "codex-relay-cloud-appctl",
    command: "start",
    pid: child.pid,
    base_url: baseUrl,
    app_url: `${baseUrl}/relay-chat.html`,
    pairing_code: secrets.pairing_code,
    desktop_token_configured: true,
    desktop_token_preview: previewSecret(secrets.desktop_token),
    secret_path: secretPath,
    state_path: statePath,
    worker_state_path: workerStatePath,
    worker_events_path: workerEventsPath,
    log_path: logPath,
    health,
    safety: safety()
  });
  return value;
}

async function status() {
  const persisted = readJson(statusPath, {});
  const secrets = readJson(secretPath, null);
  const health = await probeHealth(baseUrl).catch((error) => ({ ok: false, error: error.message || String(error) }));
  const pid = persisted.pid || null;
  return writeStatus({
    ok: Boolean(health.ok),
    service: "codex-relay-cloud-appctl",
    command: "status",
    pid,
    pid_alive: pid ? pidAlive(pid) : false,
    base_url: baseUrl,
    app_url: `${baseUrl}/relay-chat.html`,
    pairing_code: secrets?.pairing_code || persisted.pairing_code || null,
    desktop_token_configured: Boolean(secrets?.desktop_token),
    desktop_token_preview: secrets?.desktop_token ? previewSecret(secrets.desktop_token) : null,
    secret_path: secretPath,
    state_path: statePath,
    worker_state_path: workerStatePath,
    worker_events_path: workerEventsPath,
    log_path: logPath,
    health,
    safety: safety()
  });
}

async function e2e(text) {
  const started = await ensureStarted();
  const secrets = ensureSecrets();
  const registered = await postJson("/api/relay/devices/register", {
    display_name: `appctl-${Date.now()}`,
    pairing_code: secrets.pairing_code
  });
  await postJson("/api/relay/mobile/messages", { text }, registered.token);
  const worker = await runWorkerOnce();
  const inbox = await getJson("/api/relay/mobile/messages?cursor=0&limit=200", registered.token);
  const workerReply = [...(inbox.messages || [])].reverse().find((message) => message.worker_status);
  if (!workerReply) throw new Error("Worker reply was not visible in the mobile inbox.");
  return {
    ok: true,
    service: "codex-relay-cloud-appctl",
    command: "e2e",
    app_url: started.app_url,
    sent_text: text,
    mobile_token_preview: previewSecret(registered.token),
    worker,
    worker_reply: {
      relay_id: workerReply.relay_id,
      worker_status: workerReply.worker_status,
      text_sha256: workerReply.text_sha256
    },
    safety: safety()
  };
}

function runWorkerOnce() {
  const secrets = ensureSecrets();
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [workerScript], {
      cwd: repoRoot,
      env: {
        ...process.env,
        CLOUD_RELAY_BASE_URL: baseUrl,
        RELAY_DESKTOP_TOKEN: secrets.desktop_token,
        CLOUD_RELAY_WORKER_STATE: workerStatePath,
        CLOUD_RELAY_WORKER_EVENTS: workerEventsPath,
        STANDALONE_RELAY_REPO_ROOT: repoRoot
      },
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(stderr || stdout || `cloud relay worker exited with code ${code}`));
        return;
      }
      const parsed = JSON.parse(stdout || "{}");
      resolve({
        ok: true,
        service: "codex-relay-cloud-appctl",
        command: "worker-once",
        worker_result: parsed,
        safety: safety()
      });
    });
  });
}

async function stop() {
  const persisted = readJson(statusPath, {});
  if (!persisted.pid) return { ok: true, stopped: false, reason: "no_pid", safety: safety() };
  if (!pidAlive(persisted.pid)) return { ok: true, stopped: false, reason: "not_running", pid: persisted.pid, safety: safety() };
  process.kill(persisted.pid, "SIGTERM");
  return { ok: true, stopped: true, pid: persisted.pid, safety: safety() };
}

async function waitForHealth(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const health = await probeHealth(url);
      if (health.ok) return health;
    } catch (error) {
      lastError = error;
    }
    await sleep(200);
  }
  throw new Error(`Standalone cloud relay did not become healthy: ${lastError?.message || "timeout"}`);
}

async function probeHealth(url) {
  const response = await fetch(`${url}/api/relay/status`);
  const body = await response.json().catch(() => ({}));
  return {
    ok: response.ok && body.ok !== false,
    status_code: response.status,
    ready: body.ready || null,
    counts: body.counts || null,
    server_time: body.server_time || null
  };
}

async function getJson(pathname, token) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {}
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `GET ${pathname} failed: ${response.status}`);
  return body;
}

async function postJson(pathname, body, token) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body)
  });
  const parsed = await response.json().catch(() => ({}));
  if (!response.ok || parsed.ok === false) throw new Error(parsed.error || `POST ${pathname} failed: ${response.status}`);
  return parsed;
}

function ensureSecrets() {
  const existing = readJson(secretPath, {});
  const value = {
    pairing_code: process.env.RELAY_PAIRING_CODE || existing.pairing_code || `relay-${randomHex(4)}`,
    desktop_token: process.env.RELAY_DESKTOP_TOKEN || process.env.CLOUD_RELAY_DESKTOP_TOKEN || existing.desktop_token || `desk_${randomHex(24)}`
  };
  ensureParent(secretPath);
  fs.writeFileSync(secretPath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  try {
    fs.chmodSync(secretPath, 0o600);
  } catch (_error) {
    // Best-effort on filesystems that do not support chmod.
  }
  return value;
}

function writeStatus(value) {
  const payload = {
    ...value,
    updated_at: new Date().toISOString()
  };
  ensureParent(statusPath);
  fs.writeFileSync(statusPath, `${JSON.stringify(payload, null, 2)}\n`);
  return payload;
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (_error) {
    return false;
  }
}

function previewSecret(value) {
  const text = String(value || "");
  if (text.length <= 10) return "***";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function ensureParent(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString("hex");
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
