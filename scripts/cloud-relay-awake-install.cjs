#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const launchAgentsDir = path.join(os.homedir(), "Library", "LaunchAgents");
const label = "com.codex.relay.cloud.awake";
const logPath = path.join(installDir, "logs", "awake.log");
const errPath = path.join(installDir, "logs", "awake.err.log");

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error), safety: safety() }, null, 2));
  process.exit(1);
});

async function main() {
  const command = process.argv[2] || "status";
  if (command === "install") return printJson(await install());
  if (command === "status") return printJson(status("status"));
  if (command === "uninstall") return printJson(uninstall());
  throw new Error(`Unknown command: ${command}`);
}

async function install() {
  ensureDir(path.join(installDir, "logs"));
  ensureDir(launchAgentsDir);
  writePlist();
  unloadPlist();
  const target = `gui/${process.getuid()}`;
  runLaunchctl(["bootstrap", target, plistPath()], true);
  runLaunchctl(["kickstart", "-k", `${target}/${label}`], false);
  await sleep(1000);
  return status("install");
}

function status(command) {
  const launchd = launchctlPrint();
  return {
    ok: Boolean(launchd.ok),
    service: "codex-relay-cloud-awake",
    command,
    label,
    mode: "caffeinate -ims",
    plist: plistPath(),
    launchd_loaded: launchd.ok,
    log_path: logPath,
    err_path: errPath,
    power: powerSnapshot(),
    note: "Keeps the Mac from idle sleeping while the user session is active. It cannot wake a Mac that is already asleep, and it cannot override normal lid-closed sleep without clamshell/power hardware support.",
    safety: safety()
  };
}

function uninstall() {
  const result = unloadPlist();
  return {
    ok: true,
    service: "codex-relay-cloud-awake",
    command: "uninstall",
    label,
    result,
    note: "Relay files are preserved. This only stops the caffeinate keep-awake LaunchAgent.",
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
    <string>/usr/bin/caffeinate</string>
    <string>-ims</string>
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

function powerSnapshot() {
  const result = spawnSync("pmset", ["-g", "custom"], { encoding: "utf8" });
  if (result.status !== 0) return { ok: false, error: (result.stderr || result.stdout || "pmset failed").trim() };
  return {
    ok: true,
    sleep_minutes: {
      battery: parsePmsetValue(result.stdout, "Battery Power", "sleep"),
      ac: parsePmsetValue(result.stdout, "AC Power", "sleep")
    },
    wake_on_network: {
      battery: parsePmsetValue(result.stdout, "Battery Power", "womp"),
      ac: parsePmsetValue(result.stdout, "AC Power", "womp")
    }
  };
}

function parsePmsetValue(text, section, key) {
  const sectionIndex = text.indexOf(`${section}:`);
  if (sectionIndex < 0) return null;
  const nextSectionIndex = text.indexOf("Power:", sectionIndex + section.length + 1);
  const chunk = text.slice(sectionIndex, nextSectionIndex < 0 ? undefined : nextSectionIndex);
  const match = chunk.match(new RegExp(`^\\s*${escapeRegex(key)}\\s+([^\\s]+)`, "m"));
  return match ? match[1] : null;
}

function plistPath() {
  return path.join(launchAgentsDir, `${label}.plist`);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function escapeRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
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
