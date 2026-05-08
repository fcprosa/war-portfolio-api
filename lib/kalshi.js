// /lib/kalshi.js — Kalshi prediction market data fetcher

const KALSHI_API = 'https://api.elections.kalshi.com/trade-api/v2/markets';

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

      // Kalshi prices are in cents (0-99), normalize to 0-1 range
      const yesPrice = (market.yes_ask ?? market.yes_bid ?? market.last_price ?? null);
      const noPrice = yesPrice !== null ? parseFloat((1 - yesPrice).toFixed(4)) : null;
      const yesPriceNorm = yesPrice !== null ? parseFloat(yesPrice.toFixed(4)) : null;

      const costBasis = pos.side === 'NO'
        ? pos.avgCost * pos.contracts
        : pos.avgCost * pos.contracts;

      const currentPrice = pos.side === 'NO' ? noPrice : yesPriceNorm;
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
        currentYesPrice: yesPriceNorm,
        currentNoPrice: noPrice,
        currentMarketValue,
        unrealizedPnl,
        unrealizedPnlPct,
        volume24h: market.volume_24h ?? market.volume ?? null,
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
