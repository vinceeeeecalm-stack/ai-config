#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");

const installDir = path.resolve(__dirname, "..");
const secretPath = process.env.CLOUD_RELAY_SECRET_PATH || path.join(installDir, "config", "secrets.json");
const secrets = readJson(secretPath);

process.env.RELAY_PAIRING_CODE = process.env.RELAY_PAIRING_CODE || secrets.pairing_code || "";
process.env.RELAY_DESKTOP_TOKEN = process.env.RELAY_DESKTOP_TOKEN || secrets.desktop_token || "";
process.env.CLOUD_RELAY_STATE_PATH = process.env.CLOUD_RELAY_STATE_PATH || path.join(installDir, "state", "relay-state.json");
process.env.CLOUD_RELAY_PUBLIC_DIR = process.env.CLOUD_RELAY_PUBLIC_DIR || path.join(installDir, "public");
process.env.CLOUD_RELAY_PORT = process.env.CLOUD_RELAY_PORT || "8798";
process.env.PORT = process.env.PORT || process.env.CLOUD_RELAY_PORT;
process.env.PUBLIC_RELAY_URL = process.env.PUBLIC_RELAY_URL || readJson(path.join(installDir, "config", "netlify-bridge.json"), {}).public_relay_url || "";

import("./cloud-relay-standalone-server.mjs").then(({ createStandaloneRelayServer }) => {
  const port = Number(process.env.CLOUD_RELAY_PORT || process.env.PORT || 8798);
  const server = createStandaloneRelayServer({
    publicDir: process.env.CLOUD_RELAY_PUBLIC_DIR,
    statePath: process.env.CLOUD_RELAY_STATE_PATH,
    env: {
      RELAY_PAIRING_CODE: process.env.RELAY_PAIRING_CODE,
      RELAY_DESKTOP_TOKEN: process.env.RELAY_DESKTOP_TOKEN,
      PUBLIC_RELAY_URL: process.env.PUBLIC_RELAY_URL
    }
  });
  server.listen(port, "0.0.0.0", () => {
    console.log(JSON.stringify({
      ok: true,
      service: "codex-relay-cloud-launch-server",
      url: `http://127.0.0.1:${port}`,
      install_dir: installDir,
      state_path: process.env.CLOUD_RELAY_STATE_PATH,
      safety: safety()
    }));
  });
}).catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw new Error(`Unable to read relay secret file at ${filePath}: ${error.message || error}`);
  }
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
