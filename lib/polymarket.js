// /lib/polymarket.js — Polymarket CLOB prediction market data fetcher

const CLOB_API = 'https://clob.polymarket.com/markets';
const GAMMA_API = 'https://gamma-api.polymarket.com/markets';

async function fetchMarket(conditionId) {
  // Try CLOB API first
  try {
    const r = await fetch(`${CLOB_API}/${conditionId}`, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; WarPortfolio/1.0)',
      },
      signal: AbortSignal.timeout(8000),
    });
    if (r.ok) {
      const data = await r.json();
      return { data, source: 'clob' };
    }
    // 401/403 means auth required
    if (r.status === 401 || r.status === 403) {
      return { data: null, source: 'auth_required' };
    }
    console.error(`[polymarket] CLOB returned ${r.status} for ${conditionId}`);
  } catch (err) {
    console.error(`[polymarket] CLOB fetch error for ${conditionId}:`, err.message);
  }

  // Try Gamma (public read-only) API as fallback
  try {
    const r = await fetch(`${GAMMA_API}?condition_id=${conditionId}`, {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(8000),
    });
    if (r.ok) {
      const data = await r.json();
      const market = Array.isArray(data) ? data[0] : data;
      return { data: market, source: 'gamma' };
    }
    console.error(`[polymarket] Gamma returned ${r.status} for ${conditionId}`);
  } catch (err) {
    console.error(`[polymarket] Gamma fetch error for ${conditionId}:`, err.message);
  }

  return { data: null, source: 'failed' };
}

function extractPrices(data, source) {
  if (!data) return { yesPrice: null, noPrice: null, volume24h: null, title: null, closeTime: null };

  let yesPrice = null;
  let noPrice = null;
  let volume24h = null;
  let title = null;
  let closeTime = null;

  if (source === 'clob') {
    // CLOB returns tokens array with YES/NO outcome prices
    const tokens = data.tokens || [];
    const yes = tokens.find(t => t.outcome?.toLowerCase() === 'yes');
    const no = tokens.find(t => t.outcome?.toLowerCase() === 'no');
    yesPrice = yes?.price ?? null;
    noPrice = no?.price ?? null;
    volume24h = data.volume_24hr ?? data.volume ?? null;
    title = data.question ?? data.title ?? null;
    closeTime = data.end_date_iso ?? data.end_date ?? null;
  } else if (source === 'gamma') {
    // Gamma returns outcome prices differently
    yesPrice = data.outcomePrices
      ? parseFloat(JSON.parse(data.outcomePrices || '[]')[0] ?? null)
      : data.bestBid ?? null;
    noPrice = yesPrice !== null ? parseFloat((1 - yesPrice).toFixed(4)) : null;
    volume24h = data.volume24hr ?? data.volume ?? null;
    title = data.question ?? data.title ?? null;
    closeTime = data.endDate ?? data.end_date ?? null;
  }

  return { yesPrice, noPrice, volume24h, title, closeTime };
}

export async function fetchPolymarketState(positions) {
  if (!positions || positions.length === 0) {
    return { positions: [], marketsScanned: 0, fetchedAt: new Date().toISOString(), apiNote: null };
  }

  let anyAuthRequired = false;

  const results = await Promise.allSettled(
    positions.map(async (pos) => {
      const { data, source } = await fetchMarket(pos.ticker);

      if (source === 'auth_required') {
        anyAuthRequired = true;
        return {
          ...pos,
          currentYesPrice: null,
          currentNoPrice: null,
          currentMarketValue: null,
          unrealizedPnl: null,
          unrealizedPnlPct: null,
          volume24h: null,
          title: null,
          closeTime: null,
        };
      }

      const { yesPrice, noPrice, volume24h, title, closeTime } = extractPrices(data, source);

      const costBasis = pos.avgCost * pos.contracts;
      const currentPrice = pos.side === 'NO' ? noPrice : yesPrice;
      const currentMarketValue = currentPrice !== null
        ? parseFloat((pos.contracts * currentPrice).toFixed(2))
        : null;
      const unrealizedPnl = currentMarketValue !== null
        ? parseFloat((currentMarketValue - costBasis).toFixed(2))
        : null;
      const unrealizedPnlPct = unrealizedPnl !== null && costBasis > 0
        ? parseFloat(((unrealizedPnl / costBasis) * 100).toFixed(2))
        : null;

      return {
        ...pos,
        currentYesPrice: yesPrice,
        currentNoPrice: noPrice,
        currentMarketValue,
        unrealizedPnl,
        unrealizedPnlPct,
        volume24h,
        title,
        closeTime,
      };
    })
  );

  const enrichedPositions = results.map((r, i) =>
    r.status === 'fulfilled' ? r.value : {
      ...positions[i],
      currentYesPrice: null,
      currentNoPrice: null,
      currentMarketValue: null,
      unrealizedPnl: null,
      unrealizedPnlPct: null,
      volume24h: null,
      title: null,
      closeTime: null,
    }
  );

  return {
    positions: enrichedPositions,
    marketsScanned: positions.length,
    fetchedAt: new Date().toISOString(),
    apiNote: anyAuthRequired ? 'Polymarket CLOB auth required for some markets — prices may be unavailable' : null,
  };
}
