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
  versionSummary: $("#versionSummary"),
  versionHelp: $("#versionHelp"),
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
      ? publicVersionHelp(payload.app_versions?.stable_public, payload.app_versions?.local)
      : "暂无固定入口，可先用 LAN 或临时隧道。";
    els.tunnelUrl.textContent = payload.temporary_tunnel_url || "未连接临时隧道";
    els.tunnelUrlHelp.textContent = payload.temporary_tunnel_url
      ? tunnelVersionHelp(payload.app_versions?.temporary_tunnel, payload.app_versions?.local)
      : "localhost.run tunnel 当前不可用。";
    renderVersionSummary(payload.app_versions);
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

function renderVersionSummary(versions) {
  const local = versionLabel(versions?.local);
  const stable = versionLabel(versions?.stable_public);
  const tunnel = versionLabel(versions?.temporary_tunnel);
  els.versionSummary.textContent = `本机 ${local} / 稳定 ${stable} / 隧道 ${tunnel}`;
  if (versions?.recommended_source === "stable") {
    els.versionHelp.textContent = "稳定公网入口已经是当前版本，适合手机收藏。";
  } else if (versions?.recommended_source === "temporary") {
    els.versionHelp.textContent = "稳定公网入口不是当前版本；要用最新版，先用 LAN 或临时隧道。";
  } else {
    els.versionHelp.textContent = "本机/LAN 是当前版本；公网入口可能未发布或检测失败。";
  }
}

function publicVersionHelp(remote, local) {
  if (remote?.version && local?.version && remote.version !== local.version) {
    return `固定入口，但当前为 ${remote.version}，落后本机 ${local.version}。`;
  }
  if (remote?.version) return `固定入口，版本 ${remote.version}。`;
  return "固定入口，版本未能确认。";
}

function tunnelVersionHelp(remote, local) {
  if (remote?.version && local?.version && remote.version === local.version) {
    return `临时入口，当前版本 ${remote.version}。`;
  }
  if (remote?.version) return `临时入口，版本 ${remote.version}。`;
  return "会随 tunnel 重连变化，只适合临时排障。";
}

function versionLabel(value) {
  return value?.version || "--";
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
