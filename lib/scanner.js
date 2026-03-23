// /lib/scanner.js — core scanning logic (extracted from api/scan.js)

const UNIVERSE = [
  // ── OIL & GAS ──
  { sym: 'XOM',   name: 'ExxonMobil',        tag: 'oil' },
  { sym: 'CVX',   name: 'Chevron',            tag: 'oil' },
  { sym: 'SHEL',  name: 'Shell',              tag: 'oil' },
  { sym: 'COP',   name: 'ConocoPhillips',     tag: 'oil' },
  { sym: 'OXY',   name: 'Occidental',         tag: 'oil' },
  { sym: 'HAL',   name: 'Halliburton',        tag: 'oil' },
  { sym: 'SLB',   name: 'SLB',               tag: 'oil' },
  { sym: 'DVN',   name: 'Devon Energy',       tag: 'oil' },
  { sym: 'MRO',   name: 'Marathon Oil',       tag: 'oil' },
  { sym: 'PSX',   name: 'Phillips 66',        tag: 'oil' },
  { sym: 'VLO',   name: 'Valero',             tag: 'oil' },
  { sym: 'MPC',   name: 'Marathon Petroleum', tag: 'oil' },
  // ── TANKERS ──
  { sym: 'FRO',   name: 'Frontline',          tag: 'tanker' },
  { sym: 'STNG',  name: 'Scorpio Tankers',    tag: 'tanker' },
  { sym: 'DHT',   name: 'DHT Holdings',       tag: 'tanker' },
  { sym: 'TNK',   name: 'Teekay Tankers',     tag: 'tanker' },
  { sym: 'INSW',  name: 'Intl Seaways',       tag: 'tanker' },
  { sym: 'TK',    name: 'Teekay Corp',        tag: 'tanker' },
  { sym: 'ZIM',   name: 'ZIM Integrated',     tag: 'shipping' },
  { sym: 'SBLK',  name: 'Star Bulk',          tag: 'shipping' },
  { sym: 'GOGL',  name: 'Golden Ocean',       tag: 'shipping' },
  { sym: 'MATX',  name: 'Matson',             tag: 'shipping' },
  // ── US DEFENSE ──
  { sym: 'RTX',   name: 'Raytheon',           tag: 'defense-us' },
  { sym: 'LMT',   name: 'Lockheed Martin',    tag: 'defense-us' },
  { sym: 'NOC',   name: 'Northrop Grumman',   tag: 'defense-us' },
  { sym: 'GD',    name: 'General Dynamics',   tag: 'defense-us' },
  { sym: 'KTOS',  name: 'Kratos Defense',     tag: 'defense-us' },
  { sym: 'LDOS',  name: 'Leidos',             tag: 'defense-us' },
  { sym: 'CACI',  name: 'CACI Intl',          tag: 'defense-us' },
  { sym: 'AXON',  name: 'Axon Enterprise',    tag: 'defense-us' },
  { sym: 'HII',   name: 'Huntington Ingalls', tag: 'defense-us' },
  { sym: 'TDG',   name: 'TransDigm',          tag: 'defense-us' },
  // ── EU DEFENSE ──
  { sym: 'RHM.DE',  name: 'Rheinmetall',      tag: 'defense-eu' },
  { sym: 'BA.L',    name: 'BAE Systems',      tag: 'defense-eu' },
  { sym: 'HO.PA',   name: 'Thales',           tag: 'defense-eu' },
  { sym: 'LDO.MI',  name: 'Leonardo',         tag: 'defense-eu' },
  { sym: 'SAAB-B.ST', name: 'Saab',           tag: 'defense-eu' },
  { sym: 'AIR.PA',  name: 'Airbus',           tag: 'defense-eu' },
  // ── GOLD & PRECIOUS METALS ──
  { sym: 'GOLD',  name: 'Barrick Gold',       tag: 'gold' },
  { sym: 'NEM',   name: 'Newmont',            tag: 'gold' },
  { sym: 'WPM',   name: 'Wheaton Precious',   tag: 'gold' },
  { sym: 'FNV',   name: 'Franco-Nevada',      tag: 'gold' },
  { sym: 'AEM',   name: 'Agnico Eagle',       tag: 'gold' },
  { sym: 'KGC',   name: 'Kinross Gold',       tag: 'gold' },
  { sym: 'AGI',   name: 'Alamos Gold',        tag: 'gold' },
  { sym: 'OR',    name: 'Osisko Royalties',   tag: 'gold' },
  // ── FERTILIZERS ──
  { sym: 'CF',    name: 'CF Industries',      tag: 'fertilizer' },
  { sym: 'MOS',   name: 'Mosaic',             tag: 'fertilizer' },
  { sym: 'NTR',   name: 'Nutrien',            tag: 'fertilizer' },
  { sym: 'IPI',   name: 'Intrepid Potash',    tag: 'fertilizer' },
  // ── URANIUM ──
  { sym: 'CCJ',   name: 'Cameco',             tag: 'uranium' },
  { sym: 'UEC',   name: 'Uranium Energy',     tag: 'uranium' },
  { sym: 'NXE',   name: 'NexGen Energy',      tag: 'uranium' },
  { sym: 'DNN',   name: 'Denison Mines',      tag: 'uranium' },
  { sym: 'UUUU',  name: 'Energy Fuels',       tag: 'uranium' },
  // ── CYBER SECURITY ──
  { sym: 'CRWD',  name: 'CrowdStrike',        tag: 'cyber' },
  { sym: 'PANW',  name: 'Palo Alto',          tag: 'cyber' },
  { sym: 'FTNT',  name: 'Fortinet',           tag: 'cyber' },
  { sym: 'S',     name: 'SentinelOne',        tag: 'cyber' },
  { sym: 'CYBR',  name: 'CyberArk',           tag: 'cyber' },
  { sym: 'ZS',    name: 'Zscaler',            tag: 'cyber' },
  // ── AGRICULTURE ──
  { sym: 'ADM',   name: 'Archer-Daniels',     tag: 'agriculture' },
  { sym: 'BG',    name: 'Bunge Global',       tag: 'agriculture' },
  { sym: 'CTVA',  name: 'Corteva',            tag: 'agriculture' },
  { sym: 'DE',    name: 'Deere & Co',         tag: 'agriculture' },
  { sym: 'AGCO',  name: 'AGCO Corp',          tag: 'agriculture' },
  // ── COMMODITIES / MINING ──
  { sym: 'FCX',   name: 'Freeport-McMoRan',   tag: 'commodities' },
  { sym: 'RIO',   name: 'Rio Tinto',          tag: 'commodities' },
  { sym: 'BHP',   name: 'BHP Group',          tag: 'commodities' },
  { sym: 'AA',    name: 'Alcoa',              tag: 'commodities' },
  { sym: 'X',     name: 'US Steel',           tag: 'commodities' },
  { sym: 'CLF',   name: 'Cleveland-Cliffs',   tag: 'commodities' },
  { sym: 'MP',    name: 'MP Materials',       tag: 'commodities' },
  // ── WAR RISK INSURANCE ──
  { sym: 'RNR',   name: 'RenaissanceRe',      tag: 'insurance' },
  { sym: 'MKL',   name: 'Markel Group',       tag: 'insurance' },
  { sym: 'AIG',   name: 'AIG',               tag: 'insurance' },
  { sym: 'TRV',   name: 'Travelers',          tag: 'insurance' },
  { sym: 'HIG',   name: 'Hartford Financial', tag: 'insurance' },
  // ── TECH / AI (macro bellwether) ──
  { sym: 'NVDA',  name: 'Nvidia',             tag: 'tech' },
  { sym: 'MSFT',  name: 'Microsoft',          tag: 'tech' },
  { sym: 'AAPL',  name: 'Apple',              tag: 'tech' },
  { sym: 'GOOGL', name: 'Alphabet',           tag: 'tech' },
  { sym: 'META',  name: 'Meta',               tag: 'tech' },
  { sym: 'AMZN',  name: 'Amazon',             tag: 'tech' },
  { sym: 'TSLA',  name: 'Tesla',              tag: 'tech' },
  // ── FINANCIALS ──
  { sym: 'JPM',   name: 'JPMorgan',           tag: 'financials' },
  { sym: 'BAC',   name: 'Bank of America',    tag: 'financials' },
  { sym: 'WFC',   name: 'Wells Fargo',        tag: 'financials' },
  { sym: 'GS',    name: 'Goldman Sachs',      tag: 'financials' },
  { sym: 'MS',    name: 'Morgan Stanley',     tag: 'financials' },
  { sym: 'BRK-B', name: 'Berkshire',          tag: 'financials' },
  { sym: 'BX',    name: 'Blackstone',         tag: 'financials' },
  // ── CONSUMER / RETAIL ──
  { sym: 'WMT',   name: 'Walmart',            tag: 'consumer' },
  { sym: 'COST',  name: 'Costco',             tag: 'consumer' },
  { sym: 'PG',    name: 'P&G',               tag: 'consumer' },
  { sym: 'KO',    name: 'Coca-Cola',          tag: 'consumer' },
  { sym: 'MCD',   name: "McDonald's",         tag: 'consumer' },
  // ── HEALTHCARE ──
  { sym: 'JNJ',   name: 'Johnson & Johnson',  tag: 'healthcare' },
  { sym: 'UNH',   name: 'UnitedHealth',       tag: 'healthcare' },
  { sym: 'PFE',   name: 'Pfizer',             tag: 'healthcare' },
  { sym: 'ABBV',  name: 'AbbVie',             tag: 'healthcare' },
  { sym: 'MRK',   name: 'Merck',              tag: 'healthcare' },
  // ── AIRLINES (war losers) ──
  { sym: 'DAL',   name: 'Delta Airlines',     tag: 'airlines' },
  { sym: 'UAL',   name: 'United Airlines',    tag: 'airlines' },
  { sym: 'AAL',   name: 'American Airlines',  tag: 'airlines' },
  { sym: 'LUV',   name: 'Southwest',          tag: 'airlines' },
  // ── UTILITIES ──
  { sym: 'NEE',   name: 'NextEra Energy',     tag: 'utilities' },
  { sym: 'DUK',   name: 'Duke Energy',        tag: 'utilities' },
  { sym: 'SO',    name: 'Southern Company',   tag: 'utilities' },
  // ── ETF MACRO SIGNALS ──
  { sym: 'GLD',   name: 'SPDR Gold ETF',      tag: 'etf' },
  { sym: 'USO',   name: 'Oil ETF',            tag: 'etf' },
  { sym: 'XLE',   name: 'Energy Sector ETF',  tag: 'etf' },
  { sym: 'XLF',   name: 'Financials ETF',     tag: 'etf' },
  { sym: 'ITA',   name: 'Defense ETF',        tag: 'etf' },
  { sym: 'COPX',  name: 'Copper Miners ETF',  tag: 'etf' },
];

// Task 2: batch fetch via v7 quote endpoint (20 symbols per request)
async function fetchBatch(symbols) {
  const encoded = symbols.map(s => encodeURIComponent(s)).join(',');
  const urls = [
    `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encoded}`,
    `https://query2.finance.yahoo.com/v7/finance/quote?symbols=${encoded}`,
  ];
  for (const url of urls) {
    try {
      const r = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!r.ok) continue;
      const d = await r.json();
      const results = d?.quoteResponse?.result || [];
      return results.map(q => ({
        symbol: q.symbol,
        price: q.regularMarketPrice,
        chgPct: q.regularMarketChangePercent,
        currency: q.currency,
        volume: q.regularMarketVolume,
        avgVolume: q.averageDailyVolume3Month,
      }));
    } catch (err) {
      console.error(`[scan] batch fetch failed:`, err.message);
    }
  }
  return [];
}

// Fallback: v8 chart endpoint for a single symbol
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
      return { symbol: sym, price, chgPct, currency: meta.currency, volume: null, avgVolume: null };
    } catch (err) {
      console.error(`[scan] ${sym} failed:`, err.message);
    }
  }
  return null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function runScan() {
  const BATCH_SIZE = 20;
  const allQuotes = [];

  // Try batch fetch first
  let batchFailed = false;
  for (let i = 0; i < UNIVERSE.length; i += BATCH_SIZE) {
    const batch = UNIVERSE.slice(i, i + BATCH_SIZE);
    const symbols = batch.map(b => b.sym);
    const quotes = await fetchBatch(symbols);
    if (quotes.length === 0) { batchFailed = true; break; }
    allQuotes.push(...quotes);
    if (i + BATCH_SIZE < UNIVERSE.length) await sleep(150);
  }

  // Fallback to per-symbol v8 if batch failed
  if (batchFailed) {
    allQuotes.length = 0;
    for (let i = 0; i < UNIVERSE.length; i += BATCH_SIZE) {
      const batch = UNIVERSE.slice(i, i + BATCH_SIZE);
      const batchResults = await Promise.all(batch.map(item => fetchOne(item.sym)));
      allQuotes.push(...batchResults.filter(Boolean));
      if (i + BATCH_SIZE < UNIVERSE.length) await sleep(200);
    }
  }

  // Map quotes back to UNIVERSE metadata
  const quoteMap = {};
  for (const q of allQuotes) {
    if (q && q.symbol) quoteMap[q.symbol] = q;
  }

  const results = [];
  for (const item of UNIVERSE) {
    const q = quoteMap[item.sym];
    if (!q || q.price == null) continue;
    results.push({
      symbol: item.sym,
      name: item.name,
      tag: item.tag,
      price: parseFloat(q.price.toFixed(2)),
      currency: q.currency || 'USD',
      changePct: parseFloat((q.chgPct || 0).toFixed(2)),
      volumeSpike: q.volume && q.avgVolume ? parseFloat((q.volume / q.avgVolume).toFixed(1)) : null,
    });
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

  const sectorSummary = Object.entries(byTag).map(([tag, items]) => ({
    tag,
    avg: parseFloat((items.reduce((s, i) => s + i.changePct, 0) / items.length).toFixed(2)),
    count: items.length,
    top: items[0]?.symbol,
    bottom: items[items.length - 1]?.symbol,
  })).sort((a, b) => b.avg - a.avg);

  return {
    timestamp: new Date().toISOString(),
    topGainers: sorted.slice(0, 10),
    topLosers: [...sorted].reverse().slice(0, 5),
    byTag,
    sectorSummary,
    all: sorted,
    fetched: valid.length,
    total: UNIVERSE.length,
  };
}
