#!/usr/bin/env node

/**
 * Public, read-only Polymarket sports CLOB snapshot.
 *
 * Resolves an event through Gamma, selects one market/outcome token, reads the
 * public order book and public market fee parameters, then estimates a
 * depth-weighted buy using a fixed all-in cash budget. It never authenticates,
 * signs, writes, or submits an order.
 *
 * Example:
 *   node public_sports_clob_snapshot.mjs \
 *     --market-slug col-stj1-bohe-2026-07-16-bohe \
 *     --outcome Yes \
 *     --cash 5
 *
 * Sports event pages commonly represent each 1X2 side as a separate binary
 * market. In that shape the team/draw name belongs in --market-title-contains
 * and the selected token is usually the explicit "Yes" outcome.
 */

const GAMMA_ROOT = "https://gamma-api.polymarket.com";
const CLOB_ROOT = "https://clob.polymarket.com";
const SNAPSHOT_SCHEMA_VERSION = "1.1";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    const name = key.slice(2);
    const next = argv[i + 1];
    args[name] = next && !next.startsWith("--") ? argv[++i] : true;
  }
  return args;
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function round(value, places = 6) {
  if (!Number.isFinite(value)) return null;
  return Number(value.toFixed(places));
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function retryablePublicGetError(error) {
  const detail = String(
    error?.cause?.code || error?.cause?.message || error?.message || error || "",
  );
  return /ECONNRESET|EAI_AGAIN|ETIMEDOUT|UND_ERR_CONNECT_TIMEOUT|fetch failed|socket|HTTP (?:429|502|503|504)/i.test(
    detail,
  );
}

async function fetchJson(url, maxAttempts = 4) {
  let lastError;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const requestedAt = new Date().toISOString();
    try {
      const response = await fetch(url, {
        headers: { accept: "application/json", "user-agent": "public-paper-research/1.1" },
        signal: AbortSignal.timeout(15000),
      });
      const receivedAt = new Date().toISOString();
      const bodyText = await response.text();
      if (!response.ok) throw new Error(`HTTP ${response.status} from ${url}`);

      let body;
      try {
        body = JSON.parse(bodyText);
      } catch {
        throw new Error(`Non-JSON response from ${url}: HTTP ${response.status}`);
      }
      return { url, requestedAt, receivedAt, attemptsUsed: attempt, body };
    } catch (error) {
      lastError = error;
      if (attempt === maxAttempts || !retryablePublicGetError(error)) break;
      await sleep(250 * 2 ** (attempt - 1));
    }
  }

  const cause =
    lastError?.cause?.code ||
    lastError?.cause?.message ||
    lastError?.message ||
    String(lastError);
  throw new Error(`Public GET failed for ${url}: ${cause}`);
}

async function tryFetchJson(url) {
  try {
    return { ok: true, ...(await fetchJson(url)) };
  } catch (error) {
    return { ok: false, url, error: String(error?.message || error) };
  }
}

function selectToken(event, titleNeedle, outcomeNeedle) {
  const markets = Array.isArray(event?.markets) ? event.markets : [];
  const titleQuery = String(titleNeedle || "").trim().toLowerCase();
  const outcomeQuery = String(outcomeNeedle || "").trim().toLowerCase();

  for (const market of markets) {
    const title = String(market.question || market.title || market.groupItemTitle || "");
    if (titleQuery && !title.toLowerCase().includes(titleQuery)) continue;

    const outcomes = asArray(market.outcomes);
    const tokenIds = asArray(market.clobTokenIds);
    if (!outcomes.length || outcomes.length !== tokenIds.length) continue;

    const index = outcomes.findIndex((outcome) =>
      String(outcome).toLowerCase().includes(outcomeQuery),
    );
    if (index < 0) continue;

    return {
      market,
      marketTitle: title,
      allOutcomes: outcomes,
      outcome: outcomes[index],
      tokenId: String(tokenIds[index]),
      conditionId: market.conditionId || market.condition_id || null,
    };
  }
  return null;
}

function normalizeLevels(levels, side) {
  const normalized = (Array.isArray(levels) ? levels : [])
    .map((level) => ({
      price: finiteNumber(level.price),
      shares: finiteNumber(level.size),
    }))
    .filter((level) => level.price > 0 && level.price < 1 && level.shares > 0);
  normalized.sort((a, b) => (side === "ask" ? a.price - b.price : b.price - a.price));
  return normalized;
}

function extractFeeParameters(marketInfo) {
  if (!marketInfo?.ok) {
    return {
      complete: false,
      reason: "CLOB market-info request failed",
      sourceError: marketInfo?.error || "unknown error",
    };
  }
  const fd = marketInfo.body?.fd;
  const rate = finiteNumber(fd?.r);
  const exponent = finiteNumber(fd?.e);
  const takerOnly = typeof fd?.to === "boolean" ? fd.to : null;

  if (rate === null || rate < 0 || exponent === null || takerOnly === null) {
    return {
      complete: false,
      reason: "Market fee descriptor fd.r/fd.e/fd.to is incomplete",
      raw: fd ?? null,
    };
  }
  if (exponent !== 1) {
    return {
      complete: false,
      reason: `Unsupported fee exponent ${exponent}; current documented p*(1-p) formula requires e=1 and no guessed conversion is allowed`,
      rate,
      exponent,
      takerOnly,
      raw: fd,
    };
  }
  return { complete: true, rate, exponent, takerOnly, raw: fd };
}

// Current CLOB V2 documentation: fee = shares * r * p * (1-p), represented by e=1.
function feePerShare(price, feeParameters) {
  if (!feeParameters?.complete) return null;
  return feeParameters.rate * price * (1 - price);
}

function walkBuyBook(asks, cashBudget, feeParameters) {
  if (!feeParameters?.complete) {
    return { complete: false, reason: "Authoritative fee parameters unavailable" };
  }
  let cashRemaining = cashBudget;
  let shares = 0;
  let notional = 0;
  let platformFee = 0;
  let worstPrice = null;

  for (const level of asks) {
    if (cashRemaining <= 1e-9) break;
    const perShareFee = feePerShare(level.price, feeParameters);
    const allInPerShare = level.price + perShareFee;
    const takeShares = Math.min(level.shares, cashRemaining / allInPerShare);
    if (takeShares <= 0) continue;
    const levelNotional = takeShares * level.price;
    const levelFee = takeShares * perShareFee;
    shares += takeShares;
    notional += levelNotional;
    platformFee += levelFee;
    cashRemaining -= levelNotional + levelFee;
    worstPrice = level.price;
  }

  const totalCashUsed = notional + platformFee;
  const fullyFillable = cashRemaining <= Math.max(0.000001, cashBudget * 0.000001);
  return {
    complete: true,
    fullyFillable,
    cash_budget_usd: round(cashBudget),
    total_cash_used_usd: round(totalCashUsed),
    cash_unfilled_usd: round(Math.max(0, cashRemaining)),
    shares: round(shares),
    notional_usd: round(notional),
    platform_fee_usd: round(platformFee),
    weighted_average_price_ex_fee: shares ? round(notional / shares) : null,
    weighted_average_cash_per_share_incl_fee: shares ? round(totalCashUsed / shares) : null,
    worst_ask_consumed: round(worstPrice),
  };
}

function bestBidExit(bestBid, feeParameters) {
  if (bestBid === null || !feeParameters?.complete) return null;
  const fee = feePerShare(bestBid, feeParameters);
  return {
    gross_bid: round(bestBid),
    taker_fee_per_share: round(fee),
    net_cash_per_share: round(bestBid - fee),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const eventSlug = String(args["event-slug"] || "").trim();
  const marketSlug = String(args["market-slug"] || "").trim();
  const titleNeedle = String(args["market-title-contains"] || "").trim();
  const outcomeNeedle = String(args.outcome || "").trim();
  const cashBudget = finiteNumber(args.cash ?? 5);

  if (
    (!eventSlug && !marketSlug) ||
    (eventSlug && marketSlug) ||
    !outcomeNeedle ||
    cashBudget === null ||
    cashBudget <= 0
  ) {
    throw new Error(
      "Required: exactly one of --market-slug <slug> or --event-slug <slug>, plus --outcome <text> [--market-title-contains <text>] [--cash 5]",
    );
  }

  const gammaUrl = marketSlug
    ? `${GAMMA_ROOT}/markets/slug/${encodeURIComponent(marketSlug)}`
    : `${GAMMA_ROOT}/events/slug/${encodeURIComponent(eventSlug)}`;
  const gammaResponse = await fetchJson(gammaUrl);
  const gammaContainer = marketSlug ? { markets: [gammaResponse.body] } : gammaResponse.body;
  const selected = selectToken(gammaContainer, titleNeedle, outcomeNeedle);
  if (!selected) throw new Error("No matching market/outcome token found in Gamma event");

  const bookUrl = `${CLOB_ROOT}/book?token_id=${encodeURIComponent(selected.tokenId)}`;
  const bookResponse = await fetchJson(bookUrl);
  const asks = normalizeLevels(bookResponse.body?.asks, "ask");
  const bids = normalizeLevels(bookResponse.body?.bids, "bid");
  const bestAsk = asks[0]?.price ?? null;
  const bestBid = bids[0]?.price ?? null;

  const conditionId = selected.conditionId || bookResponse.body?.market || null;
  const marketInfoUrl = conditionId
    ? `${CLOB_ROOT}/clob-markets/${encodeURIComponent(conditionId)}`
    : null;
  const feeRateUrl = `${CLOB_ROOT}/fee-rate?token_id=${encodeURIComponent(selected.tokenId)}`;
  const [marketInfo, feeRateCheck] = await Promise.all([
    marketInfoUrl
      ? tryFetchJson(marketInfoUrl)
      : Promise.resolve({ ok: false, url: null, error: "condition_id unavailable" }),
    tryFetchJson(feeRateUrl),
  ]);
  const fees = extractFeeParameters(marketInfo);
  const contractRules = String(
    selected.market?.description || selected.market?.rules || gammaResponse.body?.description || "",
  ).trim();
  const resolutionSourceField = String(
    selected.market?.resolutionSource || gammaResponse.body?.resolutionSource || "",
  ).trim();
  const resolutionSourceEmbeddedInRules = /(?:primary\s+)?resolution\s+source/i.test(
    contractRules,
  );
  const resolutionSource = resolutionSourceField ||
    (resolutionSourceEmbeddedInRules ? "Embedded explicitly in contract rules text" : "");
  const contractGateComplete = Boolean(
    selected.marketTitle &&
      selected.allOutcomes.length >= 2 &&
      contractRules &&
      resolutionSource &&
      conditionId,
  );
  const buyFill = walkBuyBook(asks, cashBudget, fees);
  const exitAtBestBid = bestBidExit(bestBid, fees);
  const spread = bestAsk !== null && bestBid !== null ? bestAsk - bestBid : null;
  const impact =
    buyFill.complete && buyFill.fullyFillable && bestAsk !== null
      ? buyFill.weighted_average_price_ex_fee - bestAsk
      : null;

  const timestampsPresent = Boolean(
    gammaResponse.receivedAt && bookResponse.receivedAt && marketInfo?.receivedAt,
  );
  const executableQuoteComplete = Boolean(
    bestAsk !== null &&
      bestBid !== null &&
      fees.complete &&
      buyFill.complete &&
      buyFill.fullyFillable &&
      timestampsPresent,
  );

  const output = {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    generated_at_utc: new Date().toISOString(),
    safety: {
      paper_only: true,
      live_orders_enabled: false,
      private_api_used: false,
      real_money_execution_authorized: false,
      public_get_requests_only: true,
    },
    selection: {
      event_slug: eventSlug || null,
      market_slug: selected.market?.slug || marketSlug || null,
      requested_market_title_contains: titleNeedle || null,
      requested_outcome_contains: outcomeNeedle,
      market_title: selected.marketTitle,
      condition_id: conditionId,
      all_outcomes: selected.allOutcomes,
      outcome: selected.outcome,
      token_id: selected.tokenId,
      game_start_time: marketInfo?.body?.gst ?? selected.market?.gameStartTime ?? null,
    },
    contract_gate: {
      complete: contractGateComplete,
      question: selected.marketTitle,
      rules_text: contractRules || null,
      resolution_source: resolutionSource || null,
      resolution_source_field: resolutionSourceField || null,
      resolution_source_embedded_in_rules: resolutionSourceEmbeddedInRules,
      outcome_count: selected.allOutcomes.length,
      note: contractGateComplete
        ? "Raw contract fields present; semantic review is still required before a paper fill."
        : "Missing question, outcomes, rules/description, resolution source, or condition ID.",
    },
    executable_quote: {
      complete: executableQuoteComplete,
      incomplete_reason: executableQuoteComplete
        ? null
        : "One or more of bid, ask, $-depth, authoritative fee data, or timestamp is missing",
      best_bid: round(bestBid),
      best_ask: round(bestAsk),
      spread: round(spread),
      ask_levels: asks.length,
      bid_levels: bids.length,
      buy_fill_all_in_cash: buyFill,
      buy_price_impact_ex_fee: round(impact),
      immediate_exit_at_best_bid: exitAtBestBid,
    },
    fees: {
      authoritative_market_descriptor: fees,
      fee_rate_endpoint_cross_check: feeRateCheck.ok ? feeRateCheck.body : null,
      fee_rate_endpoint_error: feeRateCheck.ok ? null : feeRateCheck.error,
      note: "Fee-rate endpoint is a cross-check only; calculations require market fd.r/fd.e/fd.to.",
    },
    paper_entry_gate: {
      complete: Boolean(contractGateComplete && executableQuoteComplete),
      contract_gate_complete: contractGateComplete,
      executable_quote_complete: executableQuoteComplete,
      semantic_contract_review_required: true,
    },
    source_timestamps: {
      gamma_market_or_event_received_at: gammaResponse.receivedAt,
      clob_book_received_at: bookResponse.receivedAt,
      clob_market_info_received_at: marketInfo?.receivedAt ?? null,
      fee_rate_check_received_at: feeRateCheck?.receivedAt ?? null,
    },
    sources: {
      gamma_market_or_event: gammaUrl,
      clob_book: bookUrl,
      clob_market_info: marketInfoUrl,
      clob_fee_rate: feeRateUrl,
    },
  };

  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
