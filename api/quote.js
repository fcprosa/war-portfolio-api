export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  const { symbol } = req.query;
  if (!symbol) return res.status(400).json({ error: 'symbol required' });

  const enc = encodeURIComponent(symbol);

  // Primary: v7 quote endpoint
  try {
    const urls = [
      `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${enc}`,
      `https://query2.finance.yahoo.com/v7/finance/quote?symbols=${enc}`,
    ];
    for (const url of urls) {
      try {
        const r = await fetch(url, {
          headers: {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://finance.yahoo.com',
          },
          signal: AbortSignal.timeout(7000),
        });
        if (!r.ok) continue;
        const d = await r.json();
        const q = d?.quoteResponse?.result?.[0];
        if (!q?.regularMarketPrice) continue;
        return res.status(200).json({
          symbol,
          price: q.regularMarketPrice,
          chgPct: q.regularMarketChangePercent,
          currency: q.currency,
          marketState: q.marketState,
        });
      } catch (err) {
        console.error(`[quote] v7 ${symbol} failed:`, err.message);
      }
    }
  } catch (err) {
    console.error(`[quote] ${symbol} v7 outer failed:`, err.message);
  }

  // Fallback: v8 chart endpoint
  const chartUrls = [
    `https://query1.finance.yahoo.com/v8/finance/chart/${enc}?interval=1d&range=1d`,
    `https://query2.finance.yahoo.com/v8/finance/chart/${enc}?interval=1d&range=1d`,
  ];
  for (const url of chartUrls) {
    try {
      const r = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        },
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
    } catch (err) {
      console.error(`[quote] v8 ${symbol} failed:`, err.message);
    }
  }

  return res.status(502).json({ error: 'fetch failed for ' + symbol });
}
