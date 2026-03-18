// /api/scan.js
// War thesis universe scanner — Yahoo Finance, same source as quote.js

const WAR_UNIVERSE = [
  { sym: 'SHEL',   name: 'Shell',            tag: 'oil' },
  { sym: 'XOM',    name: 'ExxonMobil',       tag: 'oil' },
  { sym: 'CVX',    name: 'Chevron',          tag: 'oil' },
  { sym: 'COP',    name: 'ConocoPhillips',   tag: 'oil' },
  { sym: 'OXY',    name: 'Occidental',       tag: 'oil' },
  { sym: 'HAL',    name: 'Halliburton',      tag: 'oil' },
  { sym: 'SLB',    name: 'SLB',              tag: 'oil' },
  { sym: 'FRO',    name: 'Frontline',        tag: 'tanker' },
  { sym: 'STNG',   name: 'Scorpio Tankers',  tag: 'tanker' },
  { sym: 'DHT',    name: 'DHT Holdings',     tag: 'tanker' },
  { sym: 'TNK',    name: 'Teekay Tankers',   tag: 'tanker' },
  { sym: 'INSW',   name: 'Intl Seaways',     tag: 'tanker' },
  { sym: 'ZIM',    name: 'ZIM Integrated',   tag: 'shipping' },
  { sym: 'SBLK',   name: 'Star Bulk',        tag: 'shipping' },
  { sym: 'RTX',    name: 'Raytheon',         tag: 'defense-us' },
  { sym: 'LMT',    name: 'Lockheed Martin',  tag: 'defense-us' },
  { sym: 'NOC',    name: 'Northrop Grumman', tag: 'defense-us' },
  { sym: 'GD',     name: 'General Dynamics', tag: 'defense-us' },
  { sym: 'KTOS',   name: 'Kratos Defense',   tag: 'defense-us' },
  { sym: 'RHM.DE', name: 'Rheinmetall',      tag: 'defense-eu' },
  { sym: 'BA.L',   name: 'BAE Systems',      tag: 'defense-eu' },
  { sym: 'HO.PA',  name: 'Thales',           tag: 'defense-eu' },
  { sym: 'LDO.MI', name: 'Leonardo',         tag: 'defense-eu' },
  { sym: 'GOLD',   name: 'Barrick Gold',     tag: 'gold' },
  { sym: 'NEM',    name: 'Newmont',          tag: 'gold' },
  { sym: 'WPM',    name: 'Wheaton Precious', tag: 'gold' },
  { sym: 'FNV',    name: 'Franco-Nevada',    tag: 'gold' },
  { sym: 'AEM',    name: 'Agnico Eagle',     tag: 'gold' },
  { sym: 'CF',     name: 'CF Industries',    tag: 'fertilizer' },
  { sym: 'MOS',    name: 'Mosaic',           tag: 'fertilizer' },
  { sym: 'NTR',    name: 'Nutrien',          tag: 'fertilizer' },
  { sym: 'CCJ',    name: 'Cameco',           tag: 'uranium' },
  { sym: 'UEC',    name: 'Uranium Energy',   tag: 'uranium' },
  { sym: 'NXE',    name: 'NexGen Energy',    tag: 'uranium' },
  { sym: 'CRWD',   name: 'CrowdStrike',      tag: 'cyber' },
  { sym: 'PANW',   name: 'Palo Alto',        tag: 'cyber' },
  { sym: 'FTNT',   name: 'Fortinet',         tag: 'cyber' },
  { sym: 'ADM',    name: 'Archer-Daniels',   tag: 'agriculture' },
  { sym: 'BG',     name: 'Bunge Global',     tag: 'agriculture' },
  { sym: 'FCX',    name: 'Freeport-McMoRan', tag: 'commodities' },
  { sym: 'RIO',    name: 'Rio Tinto',        tag: 'commodities' },
  { sym: 'RNR',    name: 'RenaissanceRe',    tag: 'insurance' },
  { sym: 'MKL',    name: 'Markel Group',     tag: 'insurance' },
];

async function fetchOne(sym) {
  const enc = encodeURIComponent(sym);
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
        },
        signal: AbortSignal.timeout(7000),
      });
      if (!r.ok) continue;
      const d = await r.json();
      const meta = d?.chart?.result?.[0]?.meta;
      if (!meta?.regularMarketPrice) continue;
      const price = meta.regularMarketPrice;
      const prev = meta.chartPreviousClose || meta.previousClose || meta.regularMarketPreviousClose;
      const chgPct = prev ? ((price - prev) / prev * 100) : (meta.regularMarketChangePercent || 0);
      return { price, chgPct, currency: meta.currency };
    } catch {}
  }
  return null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  try {
    const BATCH_SIZE = 10;
    const results = [];

    for (let i = 0; i < WAR_UNIVERSE.length; i += BATCH_SIZE) {
      const batch = WAR_UNIVERSE.slice(i, i + BATCH_SIZE);
      const batchResults = await Promise.all(
        batch.map(async (item) => {
          const q = await fetchOne(item.sym);
          if (!q) return null;
          return {
            symbol: item.sym,
            name: item.name,
            tag: item.tag,
            price: parseFloat(q.price.toFixed(2)),
            currency: q.currency || 'USD',
            changePct: parseFloat(q.chgPct.toFixed(2)),
          };
        })
      );
      results.push(...batchResults);
      if (i + BATCH_SIZE < WAR_UNIVERSE.length) await sleep(250);
    }

    const valid = results.filter(Boolean);
    const sorted = [...valid].sort((a, b) => b.changePct - a.changePct);

    const byTag = {};
    for (const item of valid) {
      if (!byTag[item.tag]) byTag[item.tag] = [];
      byTag[item.tag].push(item);
    }
    for (const tag of Object.keys(byTag)) {
      byTag[tag].sort((a, b) => b.changePct - a.changePct);
    }

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      topGainers: sorted.slice(0, 10),
      topLosers: [...sorted].reverse().slice(0, 5),
      byTag,
      all: sorted,
      fetched: valid.length,
      total: WAR_UNIVERSE.length,
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
