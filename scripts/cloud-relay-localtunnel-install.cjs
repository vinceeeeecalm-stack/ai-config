#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const label = "com.codex.relay.cloud.localtunnel";
const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
const subdomain = process.env.LOCALTUNNEL_SUBDOMAIN || "codex-relay-vince";
const requestedBaseUrl = `https://${subdomain}.loca.lt`;
const ltBin = path.join(installDir, "node_modules", "localtunnel", "bin", "lt.js");
const logPath = path.join(installDir, "logs", "localtunnel.log");
const errPath = path.join(installDir, "logs", "localtunnel.err.log");
const statusPath = path.join(installDir, "state", "localtunnel-status.json");

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "install") return printJson(await install());
  if (command === "status") return printJson(await status());
  if (command === "e2e") return printJson(await e2e(process.argv.slice(3).join(" ") || "信号 BTC"));
  if (command === "uninstall") return printJson(await uninstall());
  throw new Error(`Unknown command: ${command}`);
}

async function install() {
  if (!fs.existsSync(ltBin)) throw new Error(`localtunnel is not installed at ${ltBin}. Run npm install localtunnel first.`);
  ensureDir(path.join(installDir, "logs"));
  ensureDir(path.join(installDir, "state"));
  ensureDir(launchAgentsDir);
  rotateLog(logPath);
  rotateLog(errPath);
  writePlist();
  loadPlist();
  const health = await waitForHealth(30000);
  const value = baseStatus("install", health);
  writeStatus(value);
  return value;
}

async function status() {
  const health = await probeHealth().catch((error) => ({ ok: false, error: error.message || String(error) }));
  const value = baseStatus("status", health);
  writeStatus(value);
  return value;
}

async function e2e(text) {
  const tunnel = await status();
  if (!tunnel.health?.ok) throw new Error("localtunnel is not healthy.");
  const secrets = readJson(secretPath(), null);
  if (!secrets?.pairing_code) throw new Error("Installed relay pairing code is missing.");
  const registered = await postJson("/api/relay/devices/register", {
    display_name: `localtunnel-${Date.now()}`,
    pairing_code: secrets.pairing_code
  });
  await postJson("/api/relay/mobile/messages", { text }, registered.token);
  const reply = await waitForWorkerReply(registered.token, 20000);
  const health = await probeHealth();
  const publicBaseUrl = currentPublicBaseUrl();
  const value = {
    ok: true,
    service: "codex-relay-cloud-localtunnel",
    command: "e2e",
    public_url: `${publicBaseUrl}/relay-chat.html`,
    sent_text: text,
    mobile_token_preview: previewSecret(registered.token),
    worker_reply: {
      relay_id: reply.relay_id,
      worker_status: reply.worker_status,
      text_sha256: reply.text_sha256
    },
    health,
    safety: safety()
  };
  writeStatus({ ...tunnel, last_e2e: value });
  return value;
}

async function uninstall() {
  const result = unloadPlist();
  return {
    ok: true,
    service: "codex-relay-cloud-localtunnel",
    command: "uninstall",
    label,
    result,
    note: "Logs, node_modules, and status files are preserved.",
    safety: safety()
  };
}

function baseStatus(command, health) {
  const publicBaseUrl = currentPublicBaseUrl();
  return {
    ok: Boolean(health?.ok),
    service: "codex-relay-cloud-localtunnel",
    command,
    label,
    subdomain,
    requested_url: `${requestedBaseUrl}/relay-chat.html`,
    local_url: `http://127.0.0.1:${port}/relay-chat.html`,
    public_url: `${publicBaseUrl}/relay-chat.html`,
    lt_bin: ltBin,
    plist: plistPath(),
    launchd_loaded: launchctlPrint().ok,
    status_path: statusPath,
    log_path: logPath,
    err_path: errPath,
    health,
    note: "localtunnel URLs are public; the requested subdomain is best-effort. The actual public_url is parsed from localtunnel output.",
    safety: safety()
  };
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
    <string>${escapeXml(ltBin)}</string>
    <string>--port</string>
    <string>${escapeXml(String(port))}</string>
    <string>--local-host</string>
    <string>127.0.0.1</string>
    <string>--subdomain</string>
    <string>${escapeXml(subdomain)}</string>
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

function loadPlist() {
  unloadPlist();
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, plistPath()], true);
  runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
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

async function waitForHealth(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastHealth = null;
  while (Date.now() < deadline) {
    lastHealth = await probeHealth().catch((error) => ({ ok: false, error: error.message || String(error) }));
    if (lastHealth.ok) return lastHealth;
    await sleep(500);
  }
  return lastHealth || { ok: false, error: "timeout_waiting_for_localtunnel" };
}

async function waitForWorkerReply(token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await getJson("/api/relay/mobile/messages?cursor=0&limit=200", token);
    const reply = [...(inbox.messages || [])].reverse().find((message) => message.worker_status);
    if (reply) return reply;
    await sleep(500);
  }
  throw new Error("Timed out waiting for localtunnel worker reply.");
}

async function probeHealth() {
  const publicBaseUrl = currentPublicBaseUrl();
  const response = await fetch(`${publicBaseUrl}/api/relay/status`);
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
  const publicBaseUrl = currentPublicBaseUrl();
  const response = await fetch(`${publicBaseUrl}${pathname}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {}
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `GET ${pathname} failed: ${response.status}`);
  return body;
}

async function postJson(pathname, body, token) {
  const publicBaseUrl = currentPublicBaseUrl();
  const response = await fetch(`${publicBaseUrl}${pathname}`, {
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

function writeStatus(value) {
  ensureDir(path.dirname(statusPath));
  fs.writeFileSync(statusPath, `${JSON.stringify({ ...value, updated_at: new Date().toISOString() }, null, 2)}\n`);
}

function rotateLog(filePath) {
  if (!fs.existsSync(filePath)) return;
  fs.renameSync(filePath, `${filePath}.${Date.now()}.bak`);
}

function currentPublicBaseUrl() {
  return latestPublicUrl() || requestedBaseUrl;
}

function latestPublicUrl() {
  const text = [safeRead(logPath), safeRead(errPath)].join("\n");
  const matches = [...text.matchAll(/https:\/\/[a-z0-9-]+\.loca\.lt/gi)].map((match) => match[0]);
  return matches.length ? matches[matches.length - 1].replace(/\/+$/, "") : null;
}

function plistPath() {
  return path.join(launchAgentsDir, `${label}.plist`);
}

function secretPath() {
  return path.join(installDir, "config", "secrets.json");
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
}

function safeRead(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch (_error) {
    return "";
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
