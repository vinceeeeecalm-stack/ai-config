import { getDeployStore, getStore } from "@netlify/blobs";
import { handleRelayRequest } from "../../src/cloud-relay-core.mjs";

function relayBlobStore() {
  const netlifyEnv = (globalThis as any).Netlify?.env;
  const get = (key: string) => String(netlifyEnv?.get?.(key) || "");
  const storeName = get("RELAY_BLOB_STORE") || "mobile-relay";
  const context = (globalThis as any).Netlify?.context?.deploy?.context;
  if (context === "production") return getStore(storeName, { consistency: "strong" });
  return getDeployStore(storeName);
}

function relayEnv() {
  const netlifyEnv = (globalThis as any).Netlify?.env;
  const get = (key: string) => String(netlifyEnv?.get?.(key) || "");
  return {
    RELAY_PAIRING_CODE: get("RELAY_PAIRING_CODE"),
    RELAY_DESKTOP_TOKEN: get("RELAY_DESKTOP_TOKEN")
  };
}

function relayStoreAdapter() {
  const store = relayBlobStore();
  return {
    async readJson(key: string, fallback: unknown) {
      const value = await store.get(key, { type: "json" });
      return value === null ? fallback : value;
    },
    async writeJson(key: string, value: unknown) {
      await store.setJSON(key, value);
    }
  };
}

function headersObject(request: Request) {
  return Object.fromEntries(request.headers.entries());
}

function queryObject(url: URL) {
  return Object.fromEntries(url.searchParams.entries());
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" }
  });
}

export default async (request: Request) => {
  try {
    const url = new URL(request.url);
    const body = request.method === "GET" ? {} : await request.json().catch(() => ({}));
    const result = await handleRelayRequest({
      method: request.method,
      path: url.pathname,
      query: queryObject(url),
      headers: headersObject(request),
      body,
      store: relayStoreAdapter(),
      env: relayEnv()
    });
    return jsonResponse(result.status, result.body);
  } catch (error: any) {
    return jsonResponse(error.statusCode || 500, {
      ok: false,
      service: "codex-relay-cloud",
      error: error.message || "Relay function failed."
    });
  }
};

export const config = {
  path: ["/api/relay", "/api/relay/*"]
};
