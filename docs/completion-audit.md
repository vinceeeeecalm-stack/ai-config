# Codex Relay Completion Audit

Last audited: 2026-06-21

## Current Objective

The current goal is to fix the mobile relay app after a poor phone experience:

1. Keep the page simple.
2. Make daily use stable, including repeated mobile messages and replies.
3. Allow manual wake/retry from the phone and complete replies when possible, including recovery after the Mac wakes.

## Requirement Evidence

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Minimal phone UI | Local, LAN, and tunnel pages serve the WeChat-like `public/relay-chat.html` shell at version `2026.06.21.9`; the current Netlify deploy still serves version `2026.06.21.8` until account credits allow redeploy. The phone page includes `状态`, `唤醒`, `清除旧登录`, top-bar queue/sync indicators, transcript recovery, duplicate-click protection, reset support via `?reset=1`, and cloud token revocation on reset. | Complete |
| Daily repeated use | Server API requests are serialized in `scripts/cloud-relay-standalone-server.mjs`; the phone UI sends through a client-side queue in `public/relay-chat.js`; mobile posts include `client_message_id` idempotency; failed sends are kept in a capped local outbox and retried after network recovery; same-phone re-pairing sends a stable client instance id and replaces the old active token; delayed `唤醒`/`诊断` replies update the self-check panel from timeout warning back to pass when the worker reply eventually arrives; completed receipt polling now clears stale pending state even if the terminal reply was already rendered; `状态` now reports active/total/disabled device counts instead of a misleading all-device total; bridge/e2e temporary device tokens are revoked after use, and desktop-only cleanup can disable historical verification devices without touching real phones; `GET /api/relay/mobile/transcript` restores recent bidirectional history after refresh; `GET /api/relay/mobile/message-status` lets the phone show queued/consumed/working/completed receipt states for its own messages; `POST /api/relay/mobile/device/revoke` disables reset device tokens; relay state compaction keeps pending work, active devices, and recent history while bounding old messages/replies/commands/devices; `node scripts/cloud-relay-netlify-bridge.cjs continuity` sends five sequential public messages and requires every one to complete with a terminal worker reply; tests include concurrent 6-message preservation, duplicate client message protection, token revoke, same-instance replacement, temporary-device cleanup, transcript device scoping, receipt lifecycle, outbox retry, and compaction retention. | Complete |
| Local/LAN round trip | `node scripts/cloud-relay-doctor.cjs` passes `local_status`; local queue is `queued_commands=0`, `unresolved_commands=0`; local worker heartbeat is fresh. | Complete |
| Public Netlify UI and API | Production Netlify deploy `6a3770f49529294af075ee70` is ready. Static `relay-chat.js` currently serves version `2026.06.21.8`, and `/api/relay/status` returns `ok: true`. A later deploy attempt `6a37729c8ad80d76f3966a44` was skipped by Netlify because account credit usage was exceeded, so the local frontend version `2026.06.21.9`, expanded cleanup-name matcher, and latest source docs are installed locally but not published to Netlify yet. The old `mobile-relay` function name is now compatibility-wrapped to the current implementation. | Complete with deploy-credit note |
| Public queue path | `node scripts/cloud-relay-netlify-bridge.cjs e2e '状态'` processes a real public message through the local worker and returns worker status `ok`; public status shows `queued_commands=0`. `node scripts/cloud-relay-netlify-bridge.cjs reconcile` closes stale consumed pre-fix residue with audit-only history replies. | Complete for queue/recovery |
| Idle sleep mitigation | `cloud-relay-awake-install.cjs` installs `com.codex.relay.cloud.awake`, running `caffeinate -ims`. `pmset -g assertions` shows `PreventSystemSleep`, `PreventUserIdleSystemSleep`, and `PreventDiskIdle` owned by `caffeinate`. | Complete for open-lid idle sleep |
| Lid-closed physical wake | A phone web page cannot physically wake a Mac that is already lid-closed or in deep sleep. Current evidence shows battery `womp=0`; LaunchAgents and tunnels do not run while the Mac is asleep. | Blocked by macOS/hardware/external hosting |
| No live trading | All status and test outputs keep `live_orders_enabled=false`, `withdrawals_enabled=false`, and no exchange credentials in the phone path. | Complete |

## Latest Verification

Run from:

```sh
cd "/Users/vincentpan/Documents/investing/mobile-investment-console"
npm run check
npm test
cd "/Users/vincentpan/Library/Application Support/CodexRelayCloud"
node scripts/cloud-relay-doctor.cjs --e2e
```

Observed result:

```text
npm run check: pass
npm test: 22/22 pass
node scripts/cloud-relay-doctor.cjs --e2e: 10/10 pass
node scripts/cloud-relay-netlify-bridge.cjs continuity: pass, sent_count=5, completed_count=5
local queue: queued_commands=0, unresolved_commands=0, active_devices=21
public queue: queued_commands=0, unresolved_commands=0, active_devices=8
tunnel URL: https://9e42ba58559c17.lhr.life/relay-chat.html
awake helper: launchd_loaded=true, mode=caffeinate -ims
```

Additional E2E checks:

```sh
cd "/Users/vincentpan/Library/Application Support/CodexRelayCloud"
node scripts/cloud-relay-localhostrun-install.cjs e2e '诊断'
node scripts/cloud-relay-netlify-bridge.cjs e2e '状态'
node scripts/cloud-relay-netlify-bridge.cjs continuity
node /private/tmp/relay-playwright/relay-transcript-check.mjs
node /private/tmp/relay-playwright/relay-public-transcript-check.mjs
node /private/tmp/relay-playwright/relay-dedupe-click-check.mjs
node /private/tmp/relay-playwright/relay-revoke-reset-check.mjs
node /private/tmp/relay-playwright/relay-public-revoke-reset-check.mjs
node /private/tmp/relay-playwright/relay-ui-check.mjs
node /private/tmp/relay-playwright/relay-instance-replace-check.mjs
node /private/tmp/relay-playwright/relay-public-instance-replace-check.mjs
node /private/tmp/relay-playwright/relay-outbox-retry-check.mjs
node /private/tmp/relay-playwright/relay-receipt-local-check.mjs
node /private/tmp/relay-playwright/relay-receipt-public-check.mjs
node /private/tmp/relay-playwright/relay-public-chat-continuity-check.mjs
node scripts/cloud-relay-netlify-bridge.cjs cleanup-devices 0
```

The Netlify path returned worker status `ok`. `doctor --e2e` now includes local phone-to-worker e2e, public phone-to-local-worker e2e, and public continuity. The Netlify bridge continuity command sends `状态`, `报告`, `诊断`, `状态`, and `报告` through the public relay and requires all five public receipts to reach `completed` with terminal worker replies. The local and public Playwright transcript checks sent `状态`, waited for cloud and worker replies, refreshed the page, and verified the mobile message plus both replies were restored from `/api/relay/mobile/transcript`. The duplicate-click check double-clicked `状态` and verified one outgoing mobile message, one cloud reply, and one worker reply. The local and public reset revoke checks clicked `重置这台手机`, verified local token removal, and confirmed the old token receives 401. The UI check verified mobile and desktop viewports render version `2026.06.21.9` with no console issues and a worker reply. The instance replacement checks reused the same browser/client instance id locally and through the public API, re-paired, and verified the old token returns 401 while the new token remains valid. The outbox check simulated offline send, confirmed one local queued item, restored the network, and verified the item was sent and removed. The local and public receipt checks registered temporary devices, sent `状态`, observed `queued` then `completed`, and confirmed no token-like values are exposed. The public chat continuity check opens the real Netlify phone page, pairs it, sends `状态` and `报告`, and verifies both cloud acknowledgements and terminal worker replies render with zero console issues. The cleanup command disables historical verification devices using the desktop token only. The doctor run also verifies the temporary tunnel is healthy.

## Current Entry Points

- Stable Netlify UI/API: `https://codex-bridge-relay.netlify.app/relay-chat.html`
- Stable Netlify reset URL: `https://codex-bridge-relay.netlify.app/relay-chat.html?reset=1`
- Current public tunnel UI: `https://9e42ba58559c17.lhr.life/relay-chat.html`
- LAN UI: `http://192.168.0.115:8798/relay-chat.html`
- Local pairing page: `http://127.0.0.1:8798/pairing`
- Local/tunnel pairing code: `relay-2ac288ea`
- Netlify pairing code: `pair_J9O6Z9H9N5njyEaaCFrqV5SF`

## Remaining Blockers

The only product-behavior part not fully achievable in this architecture is physically waking a fully asleep or lid-closed Mac from a phone web page.

To make that requirement literally true, one of these external conditions is needed:

- macOS/hardware clamshell setup that keeps the Mac awake while closed;
- reliable Wake-on-LAN / network wake configured outside the browser app;
- a real cloud worker that can complete the requested reply without the Mac.

The implemented app handles the practical open-lid case and the wake-after-sleep recovery case: it queues messages, keeps the Mac awake while possible, and completes replies after the Mac is awake again.

The current deployment blocker is Netlify account credit usage. Local code and the installed Mac runtime include the latest expanded cleanup matcher, but publishing that exact final server version requires Netlify credits to be available again. The current live Netlify deploy is still healthy and already has the desktop-only cleanup endpoint used to reduce public active devices.
