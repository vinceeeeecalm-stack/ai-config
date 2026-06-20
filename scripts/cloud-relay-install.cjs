#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");

const sourceDir = path.resolve(__dirname, "..");
const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const serverLabel = "com.codex.relay.cloud.server";
const workerLabel = "com.codex.relay.cloud.worker";
const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
const baseUrl = `http://127.0.0.1:${port}`;
const lanUrl = localLanIp() ? `http://${localLanIp()}:${port}` : null;
const repoRoot = process.env.STANDALONE_RELAY_REPO_ROOT || "/Users/vincentpan/Documents/investing/mobile-investment-console";

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "install") return printJson(await install());
  if (command === "status") return printJson(await status());
  if (command === "pairing" || command === "pair") return printJson(await pairing());
  if (command === "uninstall") return printJson(await uninstall());
  if (command === "e2e") return printJson(await e2e(process.argv.slice(3).join(" ") || "信号 BTC"));
  throw new Error(`Unknown command: ${command}`);
}

async function install() {
  ensureDir(installDir);
  ensureDir(path.join(installDir, "config"));
  ensureDir(path.join(installDir, "logs"));
  ensureDir(path.join(installDir, "state"));
  ensureDir(launchAgentsDir);

  copyRuntime();
  const secrets = writeSecrets();
  writePlists();
  loadPlist(serverLabel);
  loadPlist(workerLabel);
  const health = await waitForHealth(12000);

  return {
    ok: true,
    service: "codex-relay-cloud-install",
    command: "install",
    install_dir: installDir,
    app_url: `${baseUrl}/relay-chat.html`,
    lan_app_url: lanUrl ? `${lanUrl}/relay-chat.html` : null,
    pairing_code: secrets.pairing_code,
    desktop_token_configured: true,
    desktop_token_preview: previewSecret(secrets.desktop_token),
    server_label: serverLabel,
    worker_label: workerLabel,
    health,
    safety: safety()
  };
}

async function status() {
  const secrets = readJson(secretPath(), null);
  const health = await probeHealth().catch((error) => ({ ok: false, error: error.message || String(error) }));
  return {
    ok: Boolean(health.ok),
    service: "codex-relay-cloud-install",
    command: "status",
    install_dir: installDir,
    app_url: `${baseUrl}/relay-chat.html`,
    lan_app_url: lanUrl ? `${lanUrl}/relay-chat.html` : null,
    pairing_code: secrets?.pairing_code || null,
    desktop_token_configured: Boolean(secrets?.desktop_token),
    desktop_token_preview: secrets?.desktop_token ? previewSecret(secrets.desktop_token) : null,
    server_label: serverLabel,
    worker_label: workerLabel,
    server_plist: plistPath(serverLabel),
    worker_plist: plistPath(workerLabel),
    launchd: {
      server_loaded: launchctlPrint(serverLabel).ok,
      worker_loaded: launchctlPrint(workerLabel).ok
    },
    health,
    safety: safety()
  };
}

async function pairing() {
  const current = await status();
  return {
    ok: Boolean(current.ok),
    service: "codex-relay-cloud-install",
    command: "pairing",
    app_url: current.app_url,
    lan_app_url: current.lan_app_url,
    pairing_code: current.pairing_code,
    desktop_bridge_online: Boolean(current.launchd?.server_loaded && current.launchd?.worker_loaded && current.health?.ok),
    status_summary: {
      server_loaded: Boolean(current.launchd?.server_loaded),
      worker_loaded: Boolean(current.launchd?.worker_loaded),
      local_ok: Boolean(current.health?.ok)
    },
    steps: [
      "手机和电脑连同一个 Wi-Fi。",
      "用手机打开 lan_app_url。",
      "在 Relay 配对码里输入 pairing_code。",
      "连接后点“联通测试”或发送“报告”。"
    ],
    caution: "局域网配对码只给自己的手机使用；desktop token 不会在这里显示。",
    safety: safety()
  };
}

async function uninstall() {
  const server = unloadPlist(serverLabel);
  const worker = unloadPlist(workerLabel);
  return {
    ok: true,
    service: "codex-relay-cloud-install",
    command: "uninstall",
    install_dir: installDir,
    server,
    worker,
    note: "Install files and state are preserved. Remove CLOUD_RELAY_INSTALL_DIR manually if you want a full purge.",
    safety: safety()
  };
}

async function e2e(text) {
  const secrets = readJson(secretPath(), null);
  if (!secrets?.pairing_code || !secrets?.desktop_token) throw new Error("Installed relay secrets are missing. Run install first.");
  await waitForHealth(12000);
  const registered = await postJson("/api/relay/devices/register", {
    display_name: `installed-${Date.now()}`,
    pairing_code: secrets.pairing_code
  });
  await postJson("/api/relay/mobile/messages", { text }, registered.token);
  const reply = await waitForWorkerReply(registered.token, 12000);
  const health = await probeHealth();
  return {
    ok: true,
    service: "codex-relay-cloud-install",
    command: "e2e",
    app_url: `${baseUrl}/relay-chat.html`,
    lan_app_url: lanUrl ? `${lanUrl}/relay-chat.html` : null,
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
}

function copyRuntime() {
  const entries = ["package.json", "package-lock.json", "netlify.toml", "public", "scripts", "src", "netlify", "tests", "docs"];
  for (const entry of entries) {
    const source = path.join(sourceDir, entry);
    if (!fs.existsSync(source)) continue;
    fs.cpSync(source, path.join(installDir, entry), { recursive: true, force: true });
  }
}

function writeSecrets() {
  const existing = readJson(secretPath(), {});
  const tempAppctlSecrets = readJson("/private/tmp/codex-relay-cloud-standalone-secrets.json", {});
  const secrets = {
    pairing_code: process.env.RELAY_PAIRING_CODE || existing.pairing_code || tempAppctlSecrets.pairing_code || `relay-${randomHex(4)}`,
    desktop_token: process.env.RELAY_DESKTOP_TOKEN || process.env.CLOUD_RELAY_DESKTOP_TOKEN || existing.desktop_token || tempAppctlSecrets.desktop_token || `desk_${randomHex(24)}`,
    repo_root: repoRoot
  };
  fs.writeFileSync(secretPath(), `${JSON.stringify(secrets, null, 2)}\n`, { mode: 0o600 });
  try {
    fs.chmodSync(secretPath(), 0o600);
  } catch (_error) {
    // chmod can fail on unusual filesystems; the file is still created without logging the token.
  }
  return secrets;
}

function writePlists() {
  writePlist(serverLabel, {
    programArguments: [process.execPath, path.join(installDir, "scripts", "cloud-relay-launch-server.cjs")],
    environment: {
      CLOUD_RELAY_PORT: String(port),
      CLOUD_RELAY_STATE_PATH: path.join(installDir, "state", "relay-state.json"),
      CLOUD_RELAY_SECRET_PATH: secretPath(),
      CLOUD_RELAY_PUBLIC_DIR: path.join(installDir, "public"),
      PUBLIC_RELAY_URL: publicRelayUrl()
    },
    stdout: path.join(installDir, "logs", "server.log"),
    stderr: path.join(installDir, "logs", "server.err.log")
  });
  writePlist(workerLabel, {
    programArguments: [process.execPath, path.join(installDir, "scripts", "cloud-relay-launch-worker.cjs")],
    environment: {
      CLOUD_RELAY_BASE_URL: baseUrl,
      CLOUD_RELAY_WORKER_STATE: path.join(installDir, "state", "worker-state.json"),
      CLOUD_RELAY_WORKER_EVENTS: path.join(installDir, "logs", "worker-events.jsonl"),
      CLOUD_RELAY_SECRET_PATH: secretPath(),
      STANDALONE_RELAY_REPO_ROOT: repoRoot
    },
    stdout: path.join(installDir, "logs", "worker.log"),
    stderr: path.join(installDir, "logs", "worker.err.log")
  });
}

function writePlist(label, options) {
  const env = Object.entries(options.environment || {}).map(([key, value]) => `
    <key>${escapeXml(key)}</key>
    <string>${escapeXml(value)}</string>`).join("");
  const args = options.programArguments.map((arg) => `
    <string>${escapeXml(arg)}</string>`).join("");
  const content = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${escapeXml(label)}</string>
  <key>ProgramArguments</key>
  <array>${args}
  </array>
  <key>WorkingDirectory</key>
  <string>${escapeXml(installDir)}</string>
  <key>EnvironmentVariables</key>
  <dict>${env}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${escapeXml(options.stdout)}</string>
  <key>StandardErrorPath</key>
  <string>${escapeXml(options.stderr)}</string>
</dict>
</plist>
`;
  fs.writeFileSync(plistPath(label), content);
}

function publicRelayUrl() {
  return readJson(path.join(installDir, "config", "netlify-bridge.json"), {}).public_relay_url || "";
}

function loadPlist(label) {
  unloadPlist(label);
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, plistPath(label)], true);
  runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
}

function unloadPlist(label) {
  const target = `gui/${process.getuid()}`;
  const result = runLaunchctl(["bootout", target, plistPath(label)], false);
  return { label, ok: result.status === 0, status: result.status, stderr: result.stderr.trim() };
}

function launchctlPrint(label) {
  const result = runLaunchctl(["print", `gui/${process.getuid()}/${label}`], false);
  return { ok: result.status === 0, status: result.status };
}

function runLaunchctl(args, fail) {
  const result = spawnSync("launchctl", args, { encoding: "utf8" });
  if (fail && result.status !== 0) {
    throw new Error(`launchctl ${args.join(" ")} failed: ${result.stderr || result.stdout}`);
  }
  return result;
}

async function waitForWorkerReply(token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const inbox = await getJson("/api/relay/mobile/messages?cursor=0&limit=200", token);
    const reply = [...(inbox.messages || [])].reverse().find((message) => message.worker_status);
    if (reply) return reply;
    await sleep(500);
  }
  throw new Error("Timed out waiting for installed worker reply.");
}

async function waitForHealth(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const health = await probeHealth();
      if (health.ok) return health;
    } catch (error) {
      lastError = error;
    }
    await sleep(250);
  }
  throw new Error(`Installed relay did not become healthy: ${lastError?.message || "timeout"}`);
}

async function probeHealth() {
  const response = await fetch(`${baseUrl}/api/relay/status`);
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

function plistPath(label) {
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

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function previewSecret(value) {
  const text = String(value || "");
  if (text.length <= 10) return "***";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString("hex");
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function localLanIp() {
  const interfaces = os.networkInterfaces();
  for (const entries of Object.values(interfaces)) {
    for (const entry of entries || []) {
      if (entry.family === "IPv4" && !entry.internal) return entry.address;
    }
  }
  return null;
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
