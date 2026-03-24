// /api/history.js — OHLCV historical data for charting
export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  res.setHeader('Cache-Control', 's-maxage=900, stale-while-revalidate=300');

  const { symbol, range, interval } = req.query;
  if (!symbol) return res.status(400).json({ error: 'symbol required' });

  const r = range || '1mo';
  const i = interval || '1d';
  const enc = encodeURIComponent(symbol);

  const urls = [
    `https://query1.finance.yahoo.com/v8/finance/chart/${enc}?range=${r}&interval=${i}&includePrePost=false`,
    `https://query2.finance.yahoo.com/v8/finance/chart/${enc}?range=${r}&interval=${i}&includePrePost=false`,
  ];

  for (const url of urls) {
    try {
      const resp = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) continue;
      const d = await resp.json();
      const result = d?.chart?.result?.[0];
      if (!result) continue;

      const timestamps = result.timestamp || [];
      const quote = result.indicators?.quote?.[0] || {};

      const candles = timestamps.map((t, idx) => ({
        time: t,
        open: quote.open?.[idx] != null ? parseFloat(quote.open[idx].toFixed(2)) : null,
        high: quote.high?.[idx] != null ? parseFloat(quote.high[idx].toFixed(2)) : null,
        low: quote.low?.[idx] != null ? parseFloat(quote.low[idx].toFixed(2)) : null,
        close: quote.close?.[idx] != null ? parseFloat(quote.close[idx].toFixed(2)) : null,
        volume: quote.volume?.[idx] || 0,
      })).filter(c => c.open !== null && c.close !== null);

      return res.status(200).json({ symbol, range: r, interval: i, currency: result.meta?.currency || 'USD', candles });
    } catch (err) {
      console.error(`[history] ${symbol} failed:`, err.message);
    }
  }
  return res.status(502).json({ error: 'history fetch failed for ' + symbol });
}
