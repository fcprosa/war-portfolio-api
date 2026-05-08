// /lib/kalshi.js — Kalshi prediction market data fetcher

const KALSHI_API = 'https://external-api.kalshi.com/trade-api/v2/markets';

async function fetchMarket(ticker) {
  try {
    const r = await fetch(`${KALSHI_API}/${ticker}`, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; WarPortfolio/1.0)',
      },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) {
      console.error(`[kalshi] market ${ticker} returned ${r.status}`);
      return null;
    }
    const data = await r.json();
    return data.market || data;
  } catch (err) {
    console.error(`[kalshi] fetch failed for ${ticker}:`, err.message);
    return null;
  }
}

export async function fetchKalshiState(positions) {
  if (!positions || positions.length === 0) {
    return { positions: [], marketsScanned: 0, fetchedAt: new Date().toISOString() };
  }

  const results = await Promise.allSettled(
    positions.map(async (pos) => {
      const market = await fetchMarket(pos.ticker);
      if (!market) {
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

      const yesAsk = market.yes_ask_dollars != null ? parseFloat(market.yes_ask_dollars) : null;
      const yesBid = market.yes_bid_dollars != null ? parseFloat(market.yes_bid_dollars) : null;
      const noAsk = market.no_ask_dollars != null ? parseFloat(market.no_ask_dollars) : null;
      const noBid = market.no_bid_dollars != null ? parseFloat(market.no_bid_dollars) : null;

      const currentYesPrice = yesAsk;
      const currentNoPrice = noAsk;

      const exitPrice = pos.side === 'NO' ? noBid : yesBid;
      const currentMarketValue = exitPrice != null
        ? parseFloat((pos.contracts * exitPrice).toFixed(2))
        : null;

      const costBasis = pos.contracts * pos.avgCost;
      const unrealizedPnl = currentMarketValue != null
        ? parseFloat((currentMarketValue - costBasis).toFixed(2))
        : null;
      const unrealizedPnlPct = unrealizedPnl != null
        ? parseFloat(((unrealizedPnl / costBasis) * 100).toFixed(2))
        : null;

      const volume24h = market.volume_24h_fp != null ? parseFloat(market.volume_24h_fp) : null;

      return {
        ...pos,
        currentYesPrice,
        currentNoPrice,
        currentMarketValue,
        unrealizedPnl,
        unrealizedPnlPct,
        volume24h,
        title: market.title ?? market.subtitle ?? null,
        closeTime: market.close_time ?? market.expiration_time ?? null,
      };
    })
  );

  const enrichedPositions = results.map((r, i) =>
    r.status === 'fulfilled' ? r.value : { ...positions[i], currentYesPrice: null, currentNoPrice: null, currentMarketValue: null, unrealizedPnl: null, unrealizedPnlPct: null, volume24h: null, title: null, closeTime: null }
  );

  return {
    positions: enrichedPositions,
    marketsScanned: positions.length,
    fetchedAt: new Date().toISOString(),
  };
}
