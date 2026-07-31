# GitHub App Patterns For Codex Bridge

Last reviewed: 2026-06-16

This app should stay boring in the infrastructure layer and clear in the phone UI. The useful pattern across strong GitHub examples is not "more framework"; it is a small installable web app, a stable queue boundary, conservative cache updates, and visible connection state.

GitHub metadata checked on 2026-06-16:

- `pwa-builder/pwa-starter`: 1,312 stars, updated 2026-06-13.
- `vite-pwa/vite-plugin-pwa`: 4,185 stars, updated 2026-06-15.
- `GoogleChrome/workbox`: 12,956 stars, updated 2026-06-15.
- `tauri-apps/tauri`: 107,925 stars, updated 2026-06-15.
- `schickling/awesome-local-first`: 150 stars, reachable as a curated local-first reference list.

## Reference Projects

| Project | What To Borrow | Why It Matters Here |
| --- | --- | --- |
| [pwa-builder/pwa-starter](https://github.com/pwa-builder/pwa-starter) | Installable PWA baseline: manifest, mobile-first shell, app-like launch. | Codex Bridge should be usable from a phone home screen without App Store review or KYC. |
| [vite-pwa/vite-plugin-pwa](https://github.com/vite-pwa/vite-plugin-pwa) | Explicit service-worker update model and build-time asset handling. | The first failure mode we hit was stale phone UI. Cache behavior must be intentional. |
| [GoogleChrome/workbox](https://github.com/GoogleChrome/workbox) | Proven service-worker caching strategies and offline fallback patterns. | API requests must never be cached; shell assets can use network-first for quick fixes. |
| [tauri-apps/tauri](https://github.com/tauri-apps/tauri) | Future desktop wrapper direction with a small native shell around web UI. | If we later need tray controls or signed desktop distribution, Tauri is a cleaner path than UI automation. |
| [schickling/awesome-local-first](https://github.com/schickling/awesome-local-first) and [local-first-web](https://github.com/local-first-web) | Local-first mindset: user data and control remain close to the device. | The bridge should keep secrets and actual local work on the Mac; cloud only relays text. |

## Design Decisions For This MVP

1. Use a PWA first, not a native mobile app.
   - Faster to iterate.
   - No App Store account, review, or KYC.
   - Can be opened by URL and installed to the phone home screen.

2. Use a cloud queue plus local long-running bridge.
   - The phone can send messages over cellular through the public relay.
   - The Mac keeps the worker, filesystem access, reports, and task execution.
   - Cloud never sees exchange credentials and never places orders.

3. Make pairing a first-screen action.
   - The previous page hid the pairing panel too deeply.
   - The new MVP puts device name, Relay pairing code, and connection CTA before secondary controls.

4. Prefer visible state over hidden magic.
   - The phone page shows cloud API, desktop bridge, local execution, and safety state.
   - Pairing commands print app URLs and code in one JSON object.
   - The in-app self-check sends a real diagnostic command and waits for desktop worker回写.
   - The desktop pairing page exposes LAN/public URLs and the pairing code only from the Mac's loopback interface, with an offline QR code for the LAN URL.

5. Treat service-worker caching as a production risk.
   - Shell assets are network-first.
   - `/api/relay/*` is excluded from service-worker handling.
   - Netlify headers mark HTML/CSS/JS/SW as `no-cache, no-store, must-revalidate`.

## Stability Rules

- Do not run live orders from this app. Keep `live_orders_enabled=false`.
- Do not place exchange, broker, wallet, or withdrawal credentials in client code.
- Do not cache relay API responses.
- Do not rely on the iCloud project directory for launchd runtime. Use the stable runtime under `~/Library/Application Support/CodexRelayCloud`.
- Keep both local and public e2e checks:
  - Local: `node scripts/cloud-relay-install.cjs e2e '诊断'`
  - Public: `node scripts/cloud-relay-netlify-bridge.cjs e2e '报告'`

## Future UI Iterations

- Add QR code pairing for the public URL after Netlify publish is unblocked.
- Add a "copy pairing code" button on the desktop helper page, not the public phone page.
- Add a small message timeline filter: all, reports, diagnostics, monitor tasks.
- Add better install prompts for iOS Safari and Android Chrome.
- Add a read-only health page for the desktop bridge.
