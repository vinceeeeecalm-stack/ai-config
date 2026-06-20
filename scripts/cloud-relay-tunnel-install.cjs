#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const label = "com.codex.relay.cloud.tunnel";
const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
const localBaseUrl = `http://127.0.0.1:${port}`;
const cloudflaredPath = process.env.CLOUDFLARED_PATH || findCloudflared();
const logPath = path.join(installDir, "logs", "tunnel.log");
const errPath = path.join(installDir, "logs", "tunnel.err.log");
const statusPath = path.join(installDir, "state", "tunnel-status.json");
const tunnelProtocol = process.env.CLOUDFLARED_PROTOCOL || "http2";

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
  if (!cloudflaredPath) throw new Error("cloudflared was not found.");
  ensureDir(path.join(installDir, "logs"));
  ensureDir(path.join(installDir, "state"));
  ensureDir(launchAgentsDir);
  rotateLog(logPath);
  rotateLog(errPath);
  writePlist();
  loadPlist();
  const result = await waitForPublicUrl(45000);
  const value = {
    ok: Boolean(result.public_url && result.health?.ok),
    service: "codex-relay-cloud-tunnel",
    command: "install",
    label,
    cloudflared_path: cloudflaredPath,
    protocol: tunnelProtocol,
    local_url: `${localBaseUrl}/relay-chat.html`,
    public_url: result.public_url ? `${result.public_url}/relay-chat.html` : null,
    status_path: statusPath,
    log_path: logPath,
    err_path: errPath,
    health: result.health,
    note: "Quick Tunnel URLs are public but not guaranteed to stay fixed.",
    safety: safety()
  };
  writeStatus(value);
  return value;
}

async function status() {
  const publicBaseUrl = latestPublicUrl();
  const health = publicBaseUrl ? await probeHealth(publicBaseUrl).catch((error) => ({ ok: false, error: error.message || String(error) })) : null;
  const value = {
    ok: Boolean(publicBaseUrl && health?.ok),
    service: "codex-relay-cloud-tunnel",
    command: "status",
    label,
    cloudflared_path: cloudflaredPath,
    protocol: tunnelProtocol,
    plist: plistPath(),
    launchd_loaded: launchctlPrint().ok,
    local_url: `${localBaseUrl}/relay-chat.html`,
    public_url: publicBaseUrl ? `${publicBaseUrl}/relay-chat.html` : null,
    status_path: statusPath,
    log_path: logPath,
    err_path: errPath,
    health,
    note: "Quick Tunnel URLs are public but not guaranteed to stay fixed.",
    safety: safety()
  };
  writeStatus(value);
  return value;
}

async function e2e(text) {
  const tunnel = await status();
  if (!tunnel.public_url || !tunnel.health?.ok) throw new Error("Public tunnel is not healthy.");
  const publicBaseUrl = tunnel.public_url.replace(/\/relay-chat\.html$/, "");
  const secrets = readJson(secretPath(), null);
  if (!secrets?.pairing_code) throw new Error("Installed relay pairing code is missing.");
  const registered = await postJson(publicBaseUrl, "/api/relay/devices/register", {
    display_name: `public-${Date.now()}`,
    pairing_code: secrets.pairing_code
  });
  await postJson(publicBaseUrl, "/api/relay/mobile/messages", { text }, registered.token);
  const reply = await waitForWorkerReply(publicBaseUrl, registered.token, 20000);
  const health = await probeHealth(publicBaseUrl);
  const value = {
    ok: true,
    service: "codex-relay-cloud-tunnel",
    command: "e2e",
    public_url: tunnel.public_url,
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
    service: "codex-relay-cloud-tunnel",
    command: "uninstall",
    label,
    result,
    note: "Logs and status files are preserved.",
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
    <string>${escapeXml(cloudflaredPath)}</string>
    <string>tunnel</string>
    <string>--no-autoupdate</string>
    <string>--protocol</string>
    <string>${escapeXml(tunnelProtocol)}</string>
    <string>--url</string>
    <string>${escapeXml(localBaseUrl)}</string>
  </array>
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

async function waitForPublicUrl(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastHealth = null;
  while (Date.now() < deadline) {
    const publicBaseUrl = latestPublicUrl();
    if (publicBaseUrl) {
      lastHealth = await probeHealth(publicBaseUrl).catch((error) => ({ ok: false, error: error.message || String(error) }));
      if (lastHealth.ok) return { public_url: publicBaseUrl, health: lastHealth };
    }
    await sleep(500);
  }
  return { public_url: latestPublicUrl(), health: lastHealth || { ok: false, error: "timeout_waiting_for_tunnel" } };
}

function latestPublicUrl() {
  const text = [safeRead(logPath), safeRead(errPath)].join("\n");
  const matches = [...text.matchAll(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/gi)].map((match) => match[0]);
  return matches.length ? matches[matches.length - 1].replace(/\/+$/, "") : null;
}

async function waitForWorkerReply(publicBaseUrl, token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await getJson(publicBaseUrl, "/api/relay/mobile/messages?cursor=0&limit=200", token);
    const reply = [...(inbox.messages || [])].reverse().find((message) => message.worker_status);
    if (reply) return reply;
    await sleep(500);
  }
  throw new Error("Timed out waiting for public worker reply.");
}

async function probeHealth(baseUrl) {
  const hostname = new URL(baseUrl).hostname;
  const dns = await resolveHost(hostname);
  if (!dns.ok) return { ok: false, dns, error: "dns_not_resolved" };
  const response = await fetch(`${baseUrl}/api/relay/status`);
  const body = await response.json().catch(() => ({}));
  return {
    ok: response.ok && body.ok !== false,
    dns,
    status_code: response.status,
    ready: body.ready || null,
    counts: body.counts || null,
    server_time: body.server_time || null
  };
}

async function resolveHost(hostname) {
  const dns = require("node:dns/promises");
  try {
    const [v4, v6] = await Promise.allSettled([dns.resolve4(hostname), dns.resolve6(hostname)]);
    const addresses = [
      ...(v4.status === "fulfilled" ? v4.value : []),
      ...(v6.status === "fulfilled" ? v6.value : [])
    ];
    return { ok: addresses.length > 0, addresses };
  } catch (error) {
    return { ok: false, error: error.message || String(error), addresses: [] };
  }
}

async function getJson(baseUrl, pathname, token) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {}
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `GET ${pathname} failed: ${response.status}`);
  return body;
}

async function postJson(baseUrl, pathname, body, token) {
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

function writeStatus(value) {
  ensureDir(path.dirname(statusPath));
  fs.writeFileSync(statusPath, `${JSON.stringify({ ...value, updated_at: new Date().toISOString() }, null, 2)}\n`);
}

function rotateLog(filePath) {
  if (!fs.existsSync(filePath)) return;
  const backup = `${filePath}.${Date.now()}.bak`;
  fs.renameSync(filePath, backup);
}

function findCloudflared() {
  for (const candidate of ["/opt/homebrew/bin/cloudflared", "/opt/homebrew/opt/cloudflared/bin/cloudflared", "/usr/local/bin/cloudflared"]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  const result = spawnSync("zsh", ["-lc", "command -v cloudflared"], { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : "";
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
