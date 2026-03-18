const FMP_KEY = process.env.FMP_API_KEY;

// War thesis sector tags
const WAR_TAGS = {
  // Energy
  'SHEL': 'oil', 'XOM': 'oil', 'CVX': 'oil', 'TTE': 'oil', 'EQNR': 'oil', 'BP': 'oil',
  'COP': 'oil', 'OXY': 'oil', 'PXD': 'oil', 'DVN': 'oil', 'HAL': 'oil', 'SLB': 'oil',
  // Tankers / Shipping
  'FRO': 'tanker', 'STNG': 'tanker', 'DHT': 'tanker', 'TNK': 'tanker', 'INSW': 'tanker',
  'ZIM': 'shipping', 'SBLK': 'shipping', 'GOGL': 'shipping',
  // Defense US
  'RTX': 'defense', 'LMT': 'defense', 'NOC': 'defense', 'GD': 'defense', 'BA': 'defense',
  'LDOS': 'defense', 'KTOS': 'defense', 'CACI': 'defense', 'MRCY': 'defense',
  // Gold
  'GOLD': 'gold', 'NEM': 'gold', 'AEM': 'gold', 'WPM': 'gold', 'FNV': 'gold',
  'KGC': 'gold', 'AGI': 'gold', 'OR': 'gold',
  // Fertilizers
  'CF': 'fertilizer', 'MOS': 'fertilizer', 'NTR': 'fertilizer', 'IPI': 'fertilizer',
  // Uranium
  'CCJ': 'uranium', 'UEC': 'uranium', 'DNN': 'uranium', 'NXE': 'uranium',
  // Cyber
  'CRWD': 'cyber', 'PANW': 'cyber', 'S': 'cyber', 'FTNT': 'cyber',
  // Commodities
  'FCX': 'commodities', 'GLEN': 'commodities', 'BHP': 'commodities', 'RIO': 'commodities',
  // Insurance / War risk
  'AIG': 'insurance', 'MKL': 'insurance', 'RNR': 'insurance',
  // Ag
  'ADM': 'agriculture', 'BG': 'agriculture', 'CTVA': 'agriculture',
};

const SCAN_UNIVERSE = Object.keys(WAR_TAGS);

// Also always include these exchanges for bulk scan
const BULK_EXCHANGES = ['NYSE', 'NASDAQ'];

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  if (!FMP_KEY) return res.status(500).json({ error: 'FMP_API_KEY not configured' });

  try {
    // Fetch bulk quotes for our war universe in one call
    const tickers = SCAN_UNIVERSE.join(',');
    const url = `https://financialmodelingprep.com/api/v3/quote/${tickers}?apikey=${FMP_KEY}`;

    const r = await fetch(url, {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(10000),
    });

    if (!r.ok) throw new Error(`FMP error ${r.status}`);
    const data = await r.json();

    if (!Array.isArray(data)) throw new Error('Unexpected FMP response');

    // Enrich with war tags and sort by % change
    const enriched = data
      .filter(q => q.price && q.changesPercentage != null)
      .map(q => ({
        symbol: q.symbol,
        name: q.name,
        price: q.price,
        change: q.change,
        changePct: parseFloat(q.changesPercentage.toFixed(2)),
        volume: q.volume,
        avgVolume: q.avgVolume,
        marketCap: q.marketCap,
        tag: WAR_TAGS[q.symbol] || 'other',
        volumeSpike: q.avgVolume > 0 ? parseFloat((q.volume / q.avgVolume).toFixed(2)) : 1,
      }))
      .sort((a, b) => b.changePct - a.changePct);

    // Top movers per category
    const byTag = {};
    for (const item of enriched) {
      if (!byTag[item.tag]) byTag[item.tag] = [];
      byTag[item.tag].push(item);
    }

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      topGainers: enriched.slice(0, 10),
      topLosers: [...enriched].sort((a, b) => a.changePct - b.changePct).slice(0, 5),
      byTag,
      all: enriched,
    });

  } catch (err) {
    return res.status(502).json({ error: err.message });
  }
}
