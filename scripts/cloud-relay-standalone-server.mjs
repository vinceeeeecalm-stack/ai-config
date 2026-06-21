#!/usr/bin/env node
import http from "node:http";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { handleRelayRequest } from "../src/cloud-relay-core.mjs";

const modulePath = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(modulePath), "..");

export function createStandaloneRelayServer(options = {}) {
  const publicDir = options.publicDir || process.env.CLOUD_RELAY_PUBLIC_DIR || path.join(repoRoot, "public");
  const statePath = options.statePath || process.env.CLOUD_RELAY_STATE_PATH || "/private/tmp/codex-relay-cloud-standalone-state.json";
  const env = {
    RELAY_PAIRING_CODE: options.env?.RELAY_PAIRING_CODE ?? process.env.RELAY_PAIRING_CODE ?? "",
    RELAY_DESKTOP_TOKEN: options.env?.RELAY_DESKTOP_TOKEN ?? process.env.RELAY_DESKTOP_TOKEN ?? process.env.CLOUD_RELAY_DESKTOP_TOKEN ?? "",
    PUBLIC_RELAY_URL: options.env?.PUBLIC_RELAY_URL ?? process.env.PUBLIC_RELAY_URL ?? "",
    CHECK_REMOTE_APP_VERSIONS: options.env?.CHECK_REMOTE_APP_VERSIONS ?? process.env.CHECK_REMOTE_APP_VERSIONS ?? "1",
    VERSION_FETCH_TIMEOUT_MS: options.env?.VERSION_FETCH_TIMEOUT_MS ?? process.env.VERSION_FETCH_TIMEOUT_MS ?? "3000",
    LOCALHOSTRUN_STATUS_PATH: options.env?.LOCALHOSTRUN_STATUS_PATH
      ?? process.env.LOCALHOSTRUN_STATUS_PATH
      ?? path.join(path.dirname(statePath), "localhostrun-status.json")
  };
  const store = fileStore(statePath);
  let stateQueue = Promise.resolve();

  function withStateQueue(task) {
    const run = stateQueue.then(task, task);
    stateQueue = run.catch(() => {});
    return run;
  }

  return http.createServer(async (req, res) => {
    try {
      const requestUrl = new URL(req.url || "/", "http://127.0.0.1");
      if (req.method === "GET" && requestUrl.pathname === "/api/relay/local/pairing") {
        const result = await withStateQueue(() => localPairingStatus(req, store, env, publicDir));
        sendJson(res, result.status, result.body, req.method === "HEAD");
        return;
      }
      if (req.method === "GET" && requestUrl.pathname === "/api/relay/app-info") {
        const result = await withStateQueue(() => publicAppInfo(req, store, env, publicDir));
        sendJson(res, result.status, result.body, req.method === "HEAD");
        return;
      }
      if (requestUrl.pathname.startsWith("/api/relay/")) {
        const body = await readJsonBody(req);
        const result = await withStateQueue(() => handleRelayRequest({
          method: req.method || "GET",
          path: requestUrl.pathname,
          query: Object.fromEntries(requestUrl.searchParams.entries()),
          headers: req.headers,
          body,
          store,
          env
        }));
        sendJson(res, result.status, result.body, req.method === "HEAD");
        return;
      }
      await serveStatic(publicDir, requestUrl.pathname, req, res);
    } catch (error) {
      sendJson(res, 500, {
        ok: false,
        error: error.message || String(error),
        safety: safety()
      });
    }
  });
}

async function localPairingStatus(req, store, env, publicDir) {
  if (!isLoopbackAddress(req.socket.remoteAddress)) {
    return {
      status: 403,
      body: {
        ok: false,
        error: "Local pairing details are only available from this Mac via 127.0.0.1.",
        safety: safety()
      }
    };
  }
  const status = await handleRelayRequest({
    method: "GET",
    path: "/api/relay/status",
    store,
    env
  });
  const origin = requestOrigin(req);
  const publicRelay = await publicRelayInfo(env);
  const appVersions = await appVersionInfo(publicDir, publicRelay, env);
  return {
    status: 200,
    body: {
      ok: true,
      service: "codex-relay-local-pairing",
      app_url: `${origin}/relay-chat.html`,
      pairing_page_url: `${origin}/pairing.html`,
      lan_app_urls: lanAppUrls(req),
      public_url: publicRelay.primary_url,
      public_url_source: publicRelay.primary_source,
      stable_public_url: publicRelay.stable_url,
      temporary_tunnel_url: publicRelay.temporary_url,
      public_urls: publicRelay.urls,
      app_versions: appVersions,
      pairing_code: env.RELAY_PAIRING_CODE || null,
      desktop_token_configured: Boolean(env.RELAY_DESKTOP_TOKEN),
      ready: status.body.ready,
      counts: status.body.counts,
      safety: safety()
    }
  };
}

async function publicRelayInfo(env) {
  const stableUrl = relayAppUrl(env.PUBLIC_RELAY_URL);
  const tunnelStatus = await readJsonIfExists(env.LOCALHOSTRUN_STATUS_PATH);
  const temporaryUrl = relayAppUrl(tunnelStatus?.public_url);
  const urls = [];
  if (stableUrl) urls.push({ kind: "stable", label: "Netlify", url: stableUrl });
  if (temporaryUrl && temporaryUrl !== stableUrl) urls.push({ kind: "temporary", label: "localhost.run", url: temporaryUrl });
  return {
    primary_url: stableUrl || temporaryUrl || null,
    primary_source: stableUrl ? "stable" : temporaryUrl ? "temporary" : null,
    stable_url: stableUrl || null,
    temporary_url: temporaryUrl || null,
    urls
  };
}

async function publicAppInfo(req, store, env, publicDir) {
  const status = await handleRelayRequest({
    method: "GET",
    path: "/api/relay/status",
    store,
    env
  });
  const origin = requestOrigin(req);
  const publicRelay = await publicRelayInfo(env);
  const appVersions = await appVersionInfo(publicDir, publicRelay, env);
  return {
    status: 200,
    body: {
      ok: true,
      service: "codex-relay-app-info",
      app_url: `${origin}/relay-chat.html`,
      public_url: publicRelay.primary_url,
      public_url_source: publicRelay.primary_source,
      stable_public_url: publicRelay.stable_url,
      temporary_tunnel_url: publicRelay.temporary_url,
      public_urls: publicRelay.urls,
      app_versions: appVersions,
      ready: status.body.ready,
      counts: status.body.counts,
      desktop_online: status.body.desktop_online,
      safety: safety()
    }
  };
}

async function appVersionInfo(publicDir, publicRelay, env) {
  const local = await appVersionFromFile(path.join(publicDir, "relay-chat.js"));
  const checkRemote = String(env.CHECK_REMOTE_APP_VERSIONS || "1") !== "0";
  const timeoutMs = Math.max(250, Math.min(5000, Number(env.VERSION_FETCH_TIMEOUT_MS || 1200)));
  const [stable, temporary] = await Promise.all([
    publicRelay.stable_url && checkRemote
      ? appVersionFromUrl(scriptUrlForAppUrl(publicRelay.stable_url), timeoutMs)
      : appVersionUnavailable(publicRelay.stable_url, checkRemote ? "not_configured" : "disabled"),
    publicRelay.temporary_url && checkRemote
      ? appVersionFromUrl(scriptUrlForAppUrl(publicRelay.temporary_url), timeoutMs)
      : appVersionUnavailable(publicRelay.temporary_url, checkRemote ? "not_configured" : "disabled")
  ]);
  const stableCurrent = Boolean(local.version && stable.version && stable.version === local.version);
  const temporaryCurrent = Boolean(local.version && temporary.version && temporary.version === local.version);
  return {
    local,
    stable_public: stable,
    temporary_tunnel: temporary,
    stable_current: stableCurrent,
    temporary_current: temporaryCurrent,
    recommended_source: stableCurrent ? "stable" : temporaryCurrent ? "temporary" : "lan",
    latest_version: local.version || null
  };
}

async function appVersionFromFile(filePath) {
  try {
    const text = await fs.readFile(filePath, "utf8");
    return { ok: true, url: null, version: extractAppVersion(text), source: "local" };
  } catch (error) {
    return { ok: false, url: null, version: null, source: "local", error: error.message || String(error) };
  }
}

async function appVersionFromUrl(url, timeoutMs) {
  if (!url) return appVersionUnavailable(url, "not_configured");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    const text = await response.text();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return { ok: true, url, version: extractAppVersion(text), source: "remote" };
  } catch (error) {
    return { ok: false, url, version: null, source: "remote", error: error.name === "AbortError" ? `timeout after ${timeoutMs}ms` : error.message || String(error) };
  } finally {
    clearTimeout(timer);
  }
}

function appVersionUnavailable(url, reason) {
  return { ok: false, url: url ? scriptUrlForAppUrl(url) : null, version: null, source: "remote", error: reason };
}

function scriptUrlForAppUrl(value) {
  const base = relayBaseUrl(value);
  return base ? `${base}/relay-chat.js` : null;
}

function extractAppVersion(text) {
  const match = String(text || "").match(/APP_VERSION\s*=\s*["']([^"']+)["']/);
  return match ? match[1] : null;
}

async function readJsonIfExists(filePath) {
  if (!filePath) return null;
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    return null;
  }
}

function relayAppUrl(value) {
  const base = relayBaseUrl(value);
  return base ? `${base}/relay-chat.html` : null;
}

function relayBaseUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return text.replace(/\/relay-chat\.html(?:[?#].*)?$/i, "").replace(/\/$/, "");
}

function fileStore(statePath) {
  return {
    async readJson(_key, fallback) {
      try {
        return JSON.parse(await fs.readFile(statePath, "utf8"));
      } catch (error) {
        if (error.code === "ENOENT") return fallback;
        throw error;
      }
    },
    async writeJson(_key, value) {
      await fs.mkdir(path.dirname(statePath), { recursive: true });
      await cleanupStateTempFiles(statePath);
      const tempPath = `${statePath}.${Date.now()}.tmp`;
      try {
        await fs.writeFile(tempPath, `${JSON.stringify(value, null, 2)}\n`);
        await fs.rename(tempPath, statePath);
      } catch (error) {
        await fs.rm(tempPath, { force: true }).catch(() => {});
        await cleanupStateTempFiles(statePath, { maxAgeMs: 0 });
        throw error;
      }
    }
  };
}

async function cleanupStateTempFiles(statePath, options = {}) {
  const dir = path.dirname(statePath);
  const prefix = `${path.basename(statePath)}.`;
  const maxAgeMs = Number.isFinite(Number(options.maxAgeMs)) ? Number(options.maxAgeMs) : 10 * 60 * 1000;
  let entries = [];
  try {
    entries = await fs.readdir(dir);
  } catch (_error) {
    return;
  }
  const nowMs = Date.now();
  await Promise.all(entries
    .filter((name) => name.startsWith(prefix) && name.endsWith(".tmp"))
    .map(async (name) => {
      const filePath = path.join(dir, name);
      try {
        const stat = await fs.stat(filePath);
        if (maxAgeMs > 0 && nowMs - stat.mtimeMs < maxAgeMs) return;
        await fs.rm(filePath, { force: true });
      } catch (_error) {
        // Stale temp cleanup is best-effort; state writes should continue when possible.
      }
    }));
}

async function readJsonBody(req) {
  if (!["POST", "PUT", "PATCH"].includes(req.method || "")) return {};
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error("Request body is too large.");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

async function serveStatic(publicDir, pathname, req, res) {
  const relative = staticRelativePath(pathname);
  const normalized = path.normalize(relative).replace(/^(\.\.(\/|\\|$))+/, "");
  const filePath = path.join(publicDir, normalized);
  if (!filePath.startsWith(path.resolve(publicDir))) {
    sendText(res, 403, "Forbidden", req.method === "HEAD");
    return;
  }
  try {
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) throw Object.assign(new Error("Not found"), { code: "ENOENT" });
    res.writeHead(200, {
      "content-type": contentType(filePath),
      "content-length": stat.size,
      "cache-control": "no-store"
    });
    if (req.method !== "HEAD") res.end(await fs.readFile(filePath));
    else res.end();
  } catch (error) {
    if (error.code === "ENOENT") sendText(res, 404, "Not found", req.method === "HEAD");
    else throw error;
  }
}

function staticRelativePath(pathname) {
  if (pathname === "/") return "relay-chat.html";
  if (pathname === "/pairing") return "pairing.html";
  if (pathname === "/app") return "relay-chat.html";
  return decodeURIComponent(pathname.replace(/^\/+/, ""));
}

function sendJson(res, status, body, headOnly = false) {
  const payload = Buffer.from(`${JSON.stringify(body)}\n`);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": payload.length,
    "cache-control": "no-store"
  });
  res.end(headOnly ? undefined : payload);
}

function sendText(res, status, text, headOnly = false) {
  const payload = Buffer.from(text);
  res.writeHead(status, {
    "content-type": "text/plain; charset=utf-8",
    "content-length": payload.length,
    "cache-control": "no-store"
  });
  res.end(headOnly ? undefined : payload);
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js") return "text/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".json" || ext === ".webmanifest") return "application/manifest+json; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  return "application/octet-stream";
}

function requestOrigin(req) {
  const host = req.headers.host || "127.0.0.1:8798";
  return `http://${host}`;
}

function lanAppUrls(req) {
  const port = String(req.headers.host || "127.0.0.1:8798").split(":").at(-1) || "8798";
  return Object.values(os.networkInterfaces())
    .flat()
    .filter(Boolean)
    .filter((item) => item.family === "IPv4" && !item.internal)
    .map((item) => `http://${item.address}:${port}/relay-chat.html`);
}

function isLoopbackAddress(value) {
  const address = String(value || "");
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
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

if (process.argv[1] === modulePath) {
  const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
  const server = createStandaloneRelayServer();
  server.listen(port, "0.0.0.0", () => {
    console.log(JSON.stringify({
      ok: true,
      service: "codex-relay-cloud-standalone",
      url: `http://127.0.0.1:${port}`,
      pairing_configured: Boolean(process.env.RELAY_PAIRING_CODE),
      desktop_token_configured: Boolean(process.env.RELAY_DESKTOP_TOKEN || process.env.CLOUD_RELAY_DESKTOP_TOKEN),
      safety: safety()
    }));
  });
}
