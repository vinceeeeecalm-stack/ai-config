#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const label = "com.codex.relay.cloud.localhostrun";
const healthLabel = `${label}.health`;
const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
const localBaseUrl = `http://127.0.0.1:${port}`;
const sshKnownHosts = path.join(installDir, "state", "localhostrun-known-hosts");
const logPath = path.join(installDir, "logs", "localhostrun.log");
const errPath = path.join(installDir, "logs", "localhostrun.err.log");
const healthLogPath = path.join(installDir, "logs", "localhostrun-health.log");
const healthErrPath = path.join(installDir, "logs", "localhostrun-health.err.log");
const statusPath = path.join(installDir, "state", "localhostrun-status.json");
const fetchTimeoutMs = Number(process.env.LOCALHOSTRUN_FETCH_TIMEOUT_MS || 35000);
const fetchAttempts = Number(process.env.LOCALHOSTRUN_FETCH_ATTEMPTS || 3);

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "install") return printJson(await install());
  if (command === "status") return printJson(await status());
  if (command === "repair") return printJson(await repair());
  if (command === "e2e") return printJson(await e2e(process.argv.slice(3).join(" ") || "信号 BTC"));
  if (command === "uninstall") return printJson(await uninstall());
  throw new Error(`Unknown command: ${command}`);
}

async function install() {
  ensureDir(path.join(installDir, "logs"));
  ensureDir(path.join(installDir, "state"));
  ensureDir(launchAgentsDir);
  rotateLog(logPath);
  rotateLog(errPath);
  rotateLog(healthLogPath);
  rotateLog(healthErrPath);
  writePlist();
  writeHealthPlist();
  loadPlist();
  loadHealthPlist();
  const health = await waitForHealth(45000);
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

async function repair() {
  const before = await status();
  if (before.health?.ok) {
    const value = {
      ...before,
      command: "repair",
      repaired: false,
      action: "none",
      reason: "localhost.run tunnel is already healthy."
    };
    writeStatus(value);
    return value;
  }

  const restart = restartTunnel();
  const health = await waitForHealth(120000);
  const value = {
    ...baseStatus("repair", health),
    repaired: Boolean(health?.ok),
    action: "restart_localhostrun_tunnel",
    restart,
    previous_health: before.health || null
  };
  writeStatus(value);
  return value;
}

async function e2e(text) {
  const health = await waitForHealth(90000);
  const tunnel = baseStatus("status", health);
  writeStatus(tunnel);
  if (!tunnel.health?.ok || !tunnel.public_url) throw new Error("localhost.run tunnel is not healthy.");
  const secrets = readJson(secretPath(), null);
  if (!secrets?.pairing_code) throw new Error("Installed relay pairing code is missing.");
  const registered = await postJson("/api/relay/devices/register", {
    display_name: `localhostrun-${Date.now()}`,
    pairing_code: secrets.pairing_code
  });
  await postJson("/api/relay/mobile/messages", { text }, registered.token);
  const reply = await waitForWorkerReply(registered.token, 75000);
  const finalHealth = await probeHealth();
  const publicBaseUrl = currentPublicBaseUrl();
  const value = {
    ok: true,
    service: "codex-relay-cloud-localhostrun",
    command: "e2e",
    public_url: `${publicBaseUrl}/relay-chat.html`,
    sent_text: text,
    mobile_token_preview: previewSecret(registered.token),
    worker_reply: {
      relay_id: reply.relay_id,
      worker_status: reply.worker_status,
      text_sha256: reply.text_sha256
    },
    health: finalHealth,
    safety: safety()
  };
  writeStatus({ ...tunnel, last_e2e: value });
  return value;
}

async function uninstall() {
  const healthResult = unloadHealthPlist();
  const result = unloadPlist();
  return {
    ok: true,
    service: "codex-relay-cloud-localhostrun",
    command: "uninstall",
    label,
    health_label: healthLabel,
    result,
    health_result: healthResult,
    note: "Logs and status files are preserved.",
    safety: safety()
  };
}

function baseStatus(command, health) {
  const publicBaseUrl = currentPublicBaseUrl();
  return {
    ok: Boolean(publicBaseUrl && health?.ok),
    service: "codex-relay-cloud-localhostrun",
    command,
    label,
    local_url: `${localBaseUrl}/relay-chat.html`,
    public_url: publicBaseUrl ? `${publicBaseUrl}/relay-chat.html` : null,
    plist: plistPath(),
    health_label: healthLabel,
    health_plist: healthPlistPath(),
    launchd_loaded: launchctlPrint().ok,
    health_launchd_loaded: healthLaunchctlPrint().ok,
    status_path: statusPath,
    log_path: logPath,
    err_path: errPath,
    health_log_path: healthLogPath,
    health_err_path: healthErrPath,
    health,
    note: "localhost.run anonymous URLs are public and valid while the SSH tunnel is connected. Create an account/key for a longer-lasting domain.",
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
    <string>/usr/bin/ssh</string>
    <string>-o</string>
    <string>StrictHostKeyChecking=no</string>
    <string>-o</string>
    <string>UserKnownHostsFile=${escapeXml(sshKnownHosts)}</string>
    <string>-o</string>
    <string>ServerAliveInterval=30</string>
    <string>-o</string>
    <string>ServerAliveCountMax=3</string>
    <string>-o</string>
    <string>ExitOnForwardFailure=yes</string>
    <string>-R</string>
    <string>80:localhost:${escapeXml(String(port))}</string>
    <string>nokey@localhost.run</string>
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

function writeHealthPlist() {
  const scriptPath = path.join(installDir, "scripts", "cloud-relay-localhostrun-install.cjs");
  const content = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${escapeXml(healthLabel)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${escapeXml(process.execPath)}</string>
    <string>${escapeXml(scriptPath)}</string>
    <string>repair</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${escapeXml(installDir)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>StandardOutPath</key>
  <string>${escapeXml(healthLogPath)}</string>
  <key>StandardErrorPath</key>
  <string>${escapeXml(healthErrPath)}</string>
</dict>
</plist>
`;
  fs.writeFileSync(healthPlistPath(), content);
}

function loadPlist() {
  unloadPlist();
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, plistPath()], true);
  runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
}

function loadHealthPlist() {
  unloadHealthPlist();
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, healthPlistPath()], true);
  runLaunchctl(["kickstart", "-k", `${target}/${healthLabel}`], false);
}

function unloadPlist() {
  const target = `gui/${process.getuid()}`;
  const result = runLaunchctl(["bootout", target, plistPath()], false);
  return { label, ok: result.status === 0, status: result.status, stderr: result.stderr.trim() };
}

function unloadHealthPlist() {
  const target = `gui/${process.getuid()}`;
  const result = runLaunchctl(["bootout", target, healthPlistPath()], false);
  return { label: healthLabel, ok: result.status === 0, status: result.status, stderr: result.stderr.trim() };
}

function launchctlPrint() {
  const result = runLaunchctl(["print", `gui/${process.getuid()}/${label}`], false);
  return { ok: result.status === 0, status: result.status };
}

function healthLaunchctlPrint() {
  const result = runLaunchctl(["print", `gui/${process.getuid()}/${healthLabel}`], false);
  return { ok: result.status === 0, status: result.status };
}

function restartTunnel() {
  if (!launchctlPrint().ok) {
    writePlist();
    loadPlist();
    return { action: "bootstrap", ok: true };
  }
  const target = `gui/${process.getuid()}`;
  const result = runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
  return { action: "kickstart", ok: result.status === 0, status: result.status, stderr: result.stderr.trim() };
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
  return lastHealth || { ok: false, error: "timeout_waiting_for_localhostrun" };
}

async function waitForWorkerReply(token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await getJson("/api/relay/mobile/messages?cursor=0&limit=200", token);
    const reply = [...(inbox.messages || [])].reverse().find((message) => message.worker_status);
    if (reply) return reply;
    await sleep(500);
  }
  throw new Error("Timed out waiting for localhost.run worker reply.");
}

async function probeHealth() {
  const publicBaseUrl = currentPublicBaseUrl();
  if (!publicBaseUrl) return { ok: false, error: "public_url_not_found" };
  const { response, body } = await fetchJson(`${publicBaseUrl}/api/relay/status`, {}, { attempts: fetchAttempts });
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
  const { response, body } = await fetchJson(`${publicBaseUrl}${pathname}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {}
  }, { attempts: fetchAttempts });
  if (!response.ok || body.ok === false) throw new Error(body.error || `GET ${pathname} failed: ${response.status}`);
  return body;
}

async function postJson(pathname, body, token) {
  const publicBaseUrl = currentPublicBaseUrl();
  const { response, body: parsed } = await fetchJson(`${publicBaseUrl}${pathname}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body)
  }, { attempts: Math.max(1, Math.min(fetchAttempts, 2)) });
  if (!response.ok || parsed.ok === false) throw new Error(parsed.error || `POST ${pathname} failed: ${response.status}`);
  return parsed;
}

async function fetchJson(url, options = {}, settings = {}) {
  const attempts = Math.max(1, Number(settings.attempts || 1));
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), fetchTimeoutMs);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const body = await response.json().catch(() => ({}));
      return { response, body };
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await sleep(Math.min(1000 * attempt, 3000));
    } finally {
      clearTimeout(timer);
    }
  }
  const detail = lastError?.name === "AbortError" ? `timeout_after_${fetchTimeoutMs}ms` : (lastError?.message || String(lastError));
  throw new Error(`fetch ${url} failed: ${detail}`);
}

function currentPublicBaseUrl() {
  return latestPublicUrl();
}

function latestPublicUrl() {
  const text = [safeRead(logPath), safeRead(errPath)].join("\n");
  const matches = [...text.matchAll(/https:\/\/[a-z0-9-]+\.lhr\.life/gi)].map((match) => match[0]);
  return matches.length ? matches[matches.length - 1].replace(/\/+$/, "") : null;
}

function writeStatus(value) {
  ensureDir(path.dirname(statusPath));
  fs.writeFileSync(statusPath, `${JSON.stringify({ ...value, updated_at: new Date().toISOString() }, null, 2)}\n`);
}

function rotateLog(filePath) {
  if (!fs.existsSync(filePath)) return;
  fs.renameSync(filePath, `${filePath}.${Date.now()}.bak`);
}

function plistPath() {
  return path.join(launchAgentsDir, `${label}.plist`);
}

function healthPlistPath() {
  return path.join(launchAgentsDir, `${healthLabel}.plist`);
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
