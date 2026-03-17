export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  const { symbol } = req.query;
  if (!symbol) return res.status(400).json({ error: 'symbol required' });

  const enc = encodeURIComponent(symbol);
  const urls = [
    `https://query1.finance.yahoo.com/v8/finance/chart/${enc}?interval=1d&range=1d`,
    `https://query2.finance.yahoo.com/v8/finance/chart/${enc}?interval=1d&range=1d`,
  ];

  for (const url of urls) {
    try {
      const r = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        }
      });
      if (!r.ok) continue;
      const d = await r.json();
      const meta = d.chart.result[0].meta;
      const prev = meta.chartPreviousClose || meta.previousClose || meta.regularMarketPreviousClose;
      const price = meta.regularMarketPrice;
      const chgPct = prev ? ((price - prev) / prev * 100) : (meta.regularMarketChangePercent || 0);
      return res.status(200).json({
        symbol,
        price,
        chgPct,
        currency: meta.currency,
        marketState: meta.marketState,
      });
    } catch {}
  }

  return res.status(502).json({ error: 'fetch failed for ' + symbol });
}
