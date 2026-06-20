const $ = (selector) => document.querySelector(selector);

const els = {
  state: $("#pairingState"),
  notice: $("#pairingNotice"),
  lanQr: $("#lanQr"),
  qrCaption: $("#qrCaption"),
  lanUrl: $("#lanUrl"),
  pairingCode: $("#pairingCodeText"),
  publicUrl: $("#publicUrl"),
  publicUrlHelp: $("#publicUrlHelp"),
  tunnelUrl: $("#tunnelUrl"),
  tunnelUrlHelp: $("#tunnelUrlHelp"),
  apiStatus: $("#apiStatus"),
  desktopStatus: $("#desktopStatus"),
  deviceCount: $("#deviceCount"),
  safetyStatus: $("#safetyStatus"),
  copyButtons: document.querySelectorAll("[data-copy-target]")
};

window.addEventListener("load", boot);
els.copyButtons.forEach((button) => button.addEventListener("click", () => copyText(button)));

async function boot() {
  try {
    const payload = await fetchJson("/api/relay/local/pairing");
    setState("online");
    const lanUrl = payload.lan_app_urls?.[0] || payload.app_url || "";
    els.lanUrl.textContent = lanUrl || "--";
    renderQr(lanUrl);
    els.pairingCode.textContent = payload.pairing_code || "--";
    els.publicUrl.textContent = payload.stable_public_url || payload.public_url || "未配置稳定公网入口";
    els.publicUrlHelp.textContent = payload.stable_public_url
      ? "固定入口，适合手机收藏。"
      : "暂无固定入口，可先用 LAN 或临时隧道。";
    els.tunnelUrl.textContent = payload.temporary_tunnel_url || "未连接临时隧道";
    els.tunnelUrlHelp.textContent = payload.temporary_tunnel_url
      ? "会在 tunnel 重连后变化，不建议收藏。"
      : "localhost.run tunnel 当前不可用。";
    els.apiStatus.textContent = payload.ready?.mobile_registration ? "READY" : "NEEDS CODE";
    els.desktopStatus.textContent = payload.desktop_token_configured ? "READY" : "NO TOKEN";
    els.deviceCount.textContent = String(payload.counts?.registered_devices ?? "--");
    els.safetyStatus.textContent = payload.safety?.live_orders_enabled ? "下单开启" : "禁用下单";
    els.notice.textContent = "本机 relay 在线。手机打开 LAN 地址后输入配对码即可连接。";
  } catch (error) {
    setState("offline");
    els.notice.textContent = friendlyError(error);
    els.apiStatus.textContent = "OFFLINE";
    els.desktopStatus.textContent = "--";
  }
}

function renderQr(value) {
  if (!value) {
    els.lanQr.textContent = "没有可用 LAN 地址";
    return;
  }
  if (typeof qrcode !== "function") {
    els.lanQr.textContent = value;
    els.qrCaption.textContent = "二维码脚本不可用，请复制 LAN 地址。";
    return;
  }
  const qr = qrcode(0, "M");
  qr.addData(value);
  qr.make();
  els.lanQr.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 3 });
  els.qrCaption.textContent = "手机扫码打开 LAN 页面，再输入配对码。";
}

async function copyText(button) {
  const target = document.getElementById(button.dataset.copyTarget || "");
  const value = target?.textContent?.trim();
  if (!value || value === "--" || /^未/.test(value)) return;
  await navigator.clipboard?.writeText(value).catch(() => {});
  const original = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = original; }, 1200);
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { "content-type": "application/json" } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) throw new Error(body.error || "Pairing request failed");
  return body;
}

function setState(value) {
  els.state.textContent = value;
  els.state.dataset.state = value;
}

function friendlyError(error) {
  const message = error?.message || String(error);
  if (/only available|127\.0\.0\.1|loopback/i.test(message)) return "配对详情只能在电脑本机用 127.0.0.1 打开。";
  if (/failed to fetch|network/i.test(message)) return "无法连接本机 relay，请检查 launchd 服务是否在线。";
  return message;
}
