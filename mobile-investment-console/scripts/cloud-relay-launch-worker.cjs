#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");

const installDir = path.resolve(__dirname, "..");
const secretPath = process.env.CLOUD_RELAY_SECRET_PATH || path.join(installDir, "config", "secrets.json");
const secrets = readJson(secretPath);

process.env.RELAY_DESKTOP_TOKEN = process.env.RELAY_DESKTOP_TOKEN || secrets.desktop_token || "";
process.env.CLOUD_RELAY_BASE_URL = process.env.CLOUD_RELAY_BASE_URL || "http://127.0.0.1:8798";
process.env.CLOUD_RELAY_WORKER_STATE = process.env.CLOUD_RELAY_WORKER_STATE || path.join(installDir, "state", "worker-state.json");
process.env.CLOUD_RELAY_WORKER_EVENTS = process.env.CLOUD_RELAY_WORKER_EVENTS || path.join(installDir, "logs", "worker-events.jsonl");
process.env.STANDALONE_RELAY_REPO_ROOT = process.env.STANDALONE_RELAY_REPO_ROOT || secrets.repo_root || installDir;

if (!process.argv.includes("--loop")) process.argv.push("--loop");
require("./cloud-relay-worker.cjs");

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(`Unable to read relay secret file at ${filePath}: ${error.message || error}`);
  }
}
