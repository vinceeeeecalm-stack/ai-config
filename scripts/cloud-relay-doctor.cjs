#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const installDir = process.env.CLOUD_RELAY_INSTALL_DIR || path.join(os.homedir(), "Library", "Application Support", "CodexRelayCloud");
const scriptDir = __dirname;
const localBaseUrl = String(process.env.CLOUD_RELAY_BASE_URL || "http://127.0.0.1:8798").replace(/\/$/, "");
const bridgeConfigPath = path.join(installDir, "config", "netlify-bridge.json");
const heartbeatPath = path.join(installDir, "state", "netlify-bridge-heartbeat.json");
const localhostrunStatusPath = path.join(installDir, "state", "localhostrun-status.json");
const runE2e = process.argv.includes("--e2e");

main().catch((error) => {
  printJson({
    ok: false,
    service: "codex-relay-cloud-doctor",
    error: error.message || String(error),
    safety: safety()
  });
  process.exit(1);
});

async function main() {
  const checks = [];
  await checkLocalStatus(checks);
  await checkPairingPage(checks);
  await checkPairingApi(checks);
  await checkDocs(checks);
  await checkPublicBridge(checks);
  await checkPublicTunnel(checks);
  await checkAwakeHelper(checks);
  if (runE2e) {
    await checkLocalE2e(checks);
    await checkPublicE2e(checks);
    await checkPublicContinuity(checks);
  }

  const hardFailures = checks.filter((item) => item.required && item.status !== "pass");
  printJson({
    ok: hardFailures.length === 0,
    service: "codex-relay-cloud-doctor",
    command: "doctor",
    ran_e2e: runE2e,
    install_dir: installDir,
    local_base_url: localBaseUrl,
    summary: {
      total: checks.length,
      passed: checks.filter((item) => item.status === "pass").length,
      warn: checks.filter((item) => item.status === "warn").length,
      failed: checks.filter((item) => item.status === "fail").length,
      required_failed: hardFailures.length
    },
    checks,
    safety: safety()
  });
  if (hardFailures.length > 0) process.exit(1);
}

async function checkLocalStatus(checks) {
  await record(checks, "local_status", true, async () => {
    const status = await fetchJson(`${localBaseUrl}/api/relay/status`);
    return {
      ok: Boolean(status.ok && status.ready?.mobile_registration && status.ready?.desktop_polling),
      evidence: {
        ready: status.ready,
        counts: status.counts,
        live_orders_enabled: status.safety?.live_orders_enabled
      }
    };
  });
}

async function checkPairingPage(checks) {
  await record(checks, "desktop_pairing_page", true, async () => {
    const page = await fetchText(`${localBaseUrl}/pairing`);
    const vendor = await fetchText(`${localBaseUrl}/vendor/qrcode-generator.js`);
    const qrVendorLoaded = vendor.includes("var qrcode = function") && vendor.includes("createSvgTag");
    return {
      ok: page.includes("桌面配对台") && page.includes("qrcode-generator") && qrVendorLoaded,
      evidence: {
        pairing_page_has_title: page.includes("桌面配对台"),
        pairing_page_loads_qr_vendor: page.includes("qrcode-generator"),
        qr_vendor_loaded: qrVendorLoaded
      }
    };
  });
}

async function checkPairingApi(checks) {
  await record(checks, "loopback_pairing_api", true, async () => {
    const pairing = await fetchJson(`${localBaseUrl}/api/relay/local/pairing`);
    const raw = JSON.stringify(pairing);
    return {
      ok: Boolean(pairing.ok && pairing.pairing_code && pairing.lan_app_urls?.length && pairing.desktop_token_configured && !raw.includes("desk_")),
      evidence: {
        app_url: pairing.app_url,
        first_lan_url: pairing.lan_app_urls?.[0] || null,
        public_url: pairing.public_url || null,
        pairing_code_configured: Boolean(pairing.pairing_code),
        desktop_token_configured: Boolean(pairing.desktop_token_configured),
        leaks_desktop_token_prefix: raw.includes("desk_")
      }
    };
  });
}

async function checkDocs(checks) {
  await record(checks, "implementation_docs", true, async () => {
    const githubDoc = readText(path.join(installDir, "docs", "github-app-patterns.md"));
    const runbook = readText(path.join(installDir, "docs", "mobile-relay-mvp-runbook.md"));
    const githubRefs = [
      "pwa-builder/pwa-starter",
      "vite-pwa/vite-plugin-pwa",
      "GoogleChrome/workbox",
      "tauri-apps/tauri"
    ];
    return {
      ok: githubRefs.every((item) => githubDoc.includes(item)) && runbook.includes("http://127.0.0.1:8798/pairing") && runbook.includes("唤醒"),
      evidence: {
        github_refs_present: githubRefs.filter((item) => githubDoc.includes(item)),
        runbook_mentions_pairing_page: runbook.includes("http://127.0.0.1:8798/pairing"),
        runbook_mentions_wake_button: runbook.includes("唤醒"),
        runbook_mentions_sleep_boundary: runbook.includes("Sleep And Wake Boundary")
      }
    };
  });
}

async function checkPublicBridge(checks) {
  await record(checks, "public_bridge_status", false, async () => {
    const config = readJson(bridgeConfigPath, null);
    if (!config?.public_relay_url) {
      return { ok: false, warning: true, evidence: { reason: "Netlify bridge config missing" } };
    }
    const status = await fetchJson(`${String(config.public_relay_url).replace(/\/$/, "")}/api/relay/status`);
    const heartbeat = readJson(heartbeatPath, null);
    return {
      ok: Boolean(status.ok && heartbeat?.status === "online"),
      warning: !(status.ok && heartbeat?.status === "online"),
      evidence: {
        public_url: `${String(config.public_relay_url).replace(/\/$/, "")}/relay-chat.html`,
        public_ready: status.ready || null,
        public_counts: status.counts || null,
        heartbeat_status: heartbeat?.status || null,
        heartbeat_updated_at: heartbeat?.updated_at || null
      }
    };
  });
}

async function checkPublicTunnel(checks) {
  await record(checks, "public_tunnel_status", false, async () => {
    const status = readJson(localhostrunStatusPath, null);
    if (!status?.public_url) return { ok: false, warning: true, evidence: { reason: "localhost.run tunnel status missing" } };
    const health = await fetchJson(String(status.public_url).replace(/\/relay-chat\.html$/, "/api/relay/status"));
    return {
      ok: Boolean(status.ok && health.ok),
      warning: !(status.ok && health.ok),
      evidence: {
        public_url: status.public_url,
        launchd_loaded: Boolean(status.launchd_loaded),
        health_launchd_loaded: Boolean(status.health_launchd_loaded),
        ready: health.ready || null,
        counts: health.counts || null
      }
    };
  });
}

async function checkAwakeHelper(checks) {
  await record(checks, "awake_helper_status", false, async () => {
    const output = runNodeScript("cloud-relay-awake-install.cjs", ["status"]);
    return {
      ok: Boolean(output.ok && output.launchd_loaded),
      warning: !(output.ok && output.launchd_loaded),
      evidence: {
        mode: output.mode,
        launchd_loaded: Boolean(output.launchd_loaded),
        sleep_minutes: output.power?.sleep_minutes || null,
        wake_on_network: output.power?.wake_on_network || null
      }
    };
  });
}

async function checkLocalE2e(checks) {
  await record(checks, "local_phone_to_worker_e2e", true, async () => {
    const output = runNodeScript("cloud-relay-install.cjs", ["e2e", "诊断"]);
    return {
      ok: Boolean(output.ok && output.worker_reply?.worker_status),
      evidence: {
        sent_text: output.sent_text,
        worker_status: output.worker_reply?.worker_status || null,
        live_orders_enabled: output.safety?.live_orders_enabled
      }
    };
  });
}

async function checkPublicE2e(checks) {
  await record(checks, "public_phone_to_local_worker_e2e", false, async () => {
    const output = runNodeScript("cloud-relay-netlify-bridge.cjs", ["e2e", "报告"]);
    return {
      ok: Boolean(output.ok && output.processed?.processed?.[0]?.worker_status),
      warning: !(output.ok && output.processed?.processed?.[0]?.worker_status),
      evidence: {
        sent_text: output.sent_text,
        public_url: output.public_url,
        worker_status: output.processed?.processed?.[0]?.worker_status || null,
        live_orders_enabled: output.safety?.live_orders_enabled
      }
    };
  });
}

async function checkPublicContinuity(checks) {
  await record(checks, "public_phone_to_local_worker_continuity", false, async () => {
    const output = runNodeScript("cloud-relay-netlify-bridge.cjs", ["continuity"]);
    const ok = Boolean(output.ok && output.sent_count === output.completed_count && output.completed_count >= 5);
    return {
      ok,
      warning: !ok,
      evidence: {
        public_url: output.public_url,
        sent_count: output.sent_count,
        completed_count: output.completed_count,
        final_phases: (output.results || []).map((item) => item.final_phase),
        live_orders_enabled: output.safety?.live_orders_enabled
      }
    };
  });
}

async function record(checks, name, required, fn) {
  try {
    const result = await fn();
    checks.push({
      name,
      required,
      status: result.ok ? "pass" : (result.warning ? "warn" : "fail"),
      evidence: result.evidence || {}
    });
  } catch (error) {
    checks.push({
      name,
      required,
      status: required ? "fail" : "warn",
      evidence: {
        error: error.message || String(error)
      }
    });
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || `GET ${url} failed: ${response.status}`);
  return body;
}

async function fetchText(url) {
  const response = await fetch(url);
  const body = await response.text();
  if (!response.ok) throw new Error(`GET ${url} failed: ${response.status}`);
  return body;
}

function runNodeScript(fileName, args) {
  const result = spawnSync(process.execPath, [path.join(scriptDir, fileName), ...args], {
    cwd: installDir,
    encoding: "utf8",
    timeout: 120000,
    maxBuffer: 1024 * 1024 * 10
  });
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || `${fileName} failed`).trim());
  }
  return JSON.parse(result.stdout);
}

function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
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
