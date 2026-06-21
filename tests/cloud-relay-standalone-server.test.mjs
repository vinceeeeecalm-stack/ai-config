import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createStandaloneRelayServer } from "../scripts/cloud-relay-standalone-server.mjs";

test("standalone cloud relay serves the PWA and relay API", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-standalone-"));
  const staleTempPath = path.join(tempDir, "state.json.stale.tmp");
  await fs.writeFile(staleTempPath, "stale");
  const staleTime = new Date(Date.now() - 60 * 60 * 1000);
  await fs.utimes(staleTempPath, staleTime, staleTime);
  const localhostrunStatusPath = path.join(tempDir, "localhostrun-status.json");
  await fs.writeFile(localhostrunStatusPath, JSON.stringify({
    public_url: "https://temporary-test.lhr.life/relay-chat.html"
  }));
  const server = createStandaloneRelayServer({
    statePath: path.join(tempDir, "state.json"),
    env: {
      RELAY_PAIRING_CODE: "pair-123",
      RELAY_DESKTOP_TOKEN: "desk-123",
      PUBLIC_RELAY_URL: "https://stable-test.netlify.app",
      CHECK_REMOTE_APP_VERSIONS: "0",
      LOCALHOSTRUN_STATUS_PATH: localhostrunStatusPath
    }
  });
  const baseUrl = await listen(server);
  try {
    const page = await fetch(`${baseUrl}/relay-chat.html`);
    assert.equal(page.status, 200);
    const pageText = await page.text();
    assert.match(pageText, /Codex|relay/i);
    assert.match(pageText, /连接自检/);

    const pairingPage = await fetch(`${baseUrl}/pairing`);
    assert.equal(pairingPage.status, 200);
    const pairingPageText = await pairingPage.text();
    assert.match(pairingPageText, /桌面配对台/);
    assert.match(pairingPageText, /qrcode-generator/);

    const pairing = await get(`${baseUrl}/api/relay/local/pairing`);
    assert.equal(pairing.ok, true);
    assert.equal(pairing.pairing_code, "pair-123");
    assert.equal(pairing.desktop_token_configured, true);
    assert.equal(pairing.public_url, "https://stable-test.netlify.app/relay-chat.html");
    assert.equal(pairing.public_url_source, "stable");
    assert.equal(pairing.stable_public_url, "https://stable-test.netlify.app/relay-chat.html");
    assert.equal(pairing.temporary_tunnel_url, "https://temporary-test.lhr.life/relay-chat.html");
    assert.equal(pairing.app_versions.local.version, "2026.06.21.11");
    assert.equal(pairing.app_versions.stable_public.error, "disabled");
    assert.equal(pairing.app_versions.temporary_tunnel.error, "disabled");
    assert.equal(pairing.app_versions.recommended_source, "lan");
    assert.equal(JSON.stringify(pairing).includes("desk-123"), false);

    const appInfo = await get(`${baseUrl}/api/relay/app-info`);
    assert.equal(appInfo.ok, true);
    assert.equal(appInfo.service, "codex-relay-app-info");
    assert.equal(appInfo.app_versions.local.version, "2026.06.21.11");
    assert.equal(appInfo.app_versions.recommended_source, "lan");
    assert.equal(appInfo.stable_public_url, "https://stable-test.netlify.app/relay-chat.html");
    assert.equal("pairing_code" in appInfo, false);
    assert.equal("desktop_token_configured" in appInfo, false);
    assert.equal(JSON.stringify(appInfo).includes("pair-123"), false);
    assert.equal(JSON.stringify(appInfo).includes("desk-123"), false);

    const registered = await post(`${baseUrl}/api/relay/devices/register`, {
      display_name: "test phone",
      pairing_code: "pair-123"
    });
    assert.equal(registered.ok, true);
    assert.match(registered.token, /^mob_/);
    assert.equal(await exists(staleTempPath), false);

    const sent = await post(`${baseUrl}/api/relay/mobile/messages`, { text: "\u4fe1\u53f7 BTC" }, registered.token);
    assert.equal(sent.ok, true);

    const commands = await get(`${baseUrl}/api/relay/desktop/commands`, "desk-123");
    assert.equal(commands.commands.length, 1);
    assert.equal(commands.commands[0].type, "current_signal_probe");
    assert.deepEqual(commands.commands[0].symbols, ["BTCUSDT"]);

    const reply = await post(`${baseUrl}/api/relay/desktop/replies`, {
      command_id: commands.commands[0].command_id,
      text: "worker ok",
      worker_status: "dry_run_recorded"
    }, "desk-123");
    assert.equal(reply.ok, true);

    const inbox = await get(`${baseUrl}/api/relay/mobile/messages`, registered.token);
    assert.equal(inbox.messages.at(-1).text, "worker ok");
    assert.equal(inbox.messages.at(-1).worker_status, "dry_run_recorded");
  } finally {
    await close(server);
  }
});

test("standalone cloud relay serializes concurrent mobile writes", async () => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cloud-relay-standalone-concurrent-"));
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
    const texts = ["状态", "诊断", "报告", "信号 BTC", "周目标 BTC SOL", "多源 BTC SOL"];
    await Promise.all(texts.map((text) => post(`${baseUrl}/api/relay/mobile/messages`, { text }, registered.token)));

    const status = await get(`${baseUrl}/api/relay/status`);
    assert.equal(status.counts.mobile_messages, texts.length);
    assert.equal(status.counts.total_commands, texts.length);

    const polled = await get(`${baseUrl}/api/relay/desktop/poll`, "desk-123");
    assert.deepEqual(polled.messages.map((message) => message.text).sort(), texts.slice().sort());
  } finally {
    await close(server);
  }
});

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
  const headers = token ? { authorization: `Bearer ${token}` } : {};
  const response = await fetch(url, { headers });
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

async function exists(filePath) {
  try {
    await fs.stat(filePath);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}
