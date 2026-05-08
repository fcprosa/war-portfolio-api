// /api/brief.js
// Aggregates positions, market scan, news, Kalshi, Polymarket, and PortWatch into a Claude prompt
import { runScan } from '../lib/scanner.js';
import { fetchAllNews } from '../lib/news.js';
import { fetchKalshiState } from '../lib/kalshi.js';
import { fetchPolymarketState } from '../lib/polymarket.js';
import { fetchPortwatchHormuz } from '../lib/portwatch.js';
import { getLatestBlob } from '../lib/state-helpers.js';
import { getWarDay } from '../lib/utils.js';

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET');

  try {
    const body = req.method === 'POST' ? await readBody(req) : {};
    const positions = body.positions || [];
    const cash = body.cash || '~$645';
    const warDay = getWarDay();

    // Load Blob state for prediction market positions and PortWatch manual fallback
    const blobState = await getLatestBlob();
    const predMarkets = body.predictionMarkets ?? blobState?.predictionMarkets ?? [];
    const portwatchManual = blobState?.portwatchManual ?? null;

    const kalshiPositions = predMarkets.filter(p => p.platform === 'kalshi');
    const polyPositions = predMarkets.filter(p => p.platform === 'polymarket');

    // Fetch all sources in parallel
    const [scanRes, newsRes, kalshiRes, polyRes, portwatchRes] = await Promise.allSettled([
      runScan(),
      fetchAllNews(),
      fetchKalshiState(kalshiPositions),
      fetchPolymarketState(polyPositions),
      fetchPortwatchHormuz(portwatchManual),
    ]);

    const scan = scanRes.status === 'fulfilled' ? scanRes.value : null;
    const news = newsRes.status === 'fulfilled' ? newsRes.value : null;
    const kalshi = kalshiRes.status === 'fulfilled' ? kalshiRes.value : null;
    const poly = polyRes.status === 'fulfilled' ? polyRes.value : null;
    const portwatch = portwatchRes.status === 'fulfilled' ? portwatchRes.value : null;

    if (scanRes.status === 'rejected') console.error('[brief] scan failed:', scanRes.reason?.message);
    if (newsRes.status === 'rejected') console.error('[brief] news failed:', newsRes.reason?.message);
    if (kalshiRes.status === 'rejected') console.error('[brief] kalshi failed:', kalshiRes.reason?.message);
    if (polyRes.status === 'rejected') console.error('[brief] polymarket failed:', polyRes.reason?.message);
    if (portwatchRes.status === 'rejected') console.error('[brief] portwatch failed:', portwatchRes.reason?.message);

    // Build structured prompt
    let prompt = `You are my personal war portfolio strategist. Today is ${new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}. US-Iran war Day ${warDay}. Hormuz closed. Stagflation regime active.

=== MY PORTFOLIO ===
Uninvested cash: ${cash}
`;

    if (positions.length > 0) {
      prompt += '\nPositions:\n';
      for (const p of positions) {
        prompt += `- ${p.display || p.sym}: ${p.shares} shares @ avg $${p.avgCost} | Current: ${p.currentPrice || 'unknown'} | P&L: ${p.pnl || 'unknown'} | Thesis: ${p.thesis}\n`;
      }
    }

    if (scan && scan.topGainers) {
      prompt += `\n=== LIVE MARKET SCAN (war thesis universe) ===\n`;
      prompt += `TOP GAINERS RIGHT NOW:\n`;
      for (const s of scan.topGainers.slice(0, 10)) {
        prompt += `- ${s.symbol} (${s.tag}): ${s.changePct > 0 ? '+' : ''}${s.changePct}% | $${s.price} | Vol spike: ${s.volumeSpike}x\n`;
      }
      prompt += `\nTOP LOSERS RIGHT NOW:\n`;
      for (const s of scan.topLosers.slice(0, 5)) {
        prompt += `- ${s.symbol} (${s.tag}): ${s.changePct}% | $${s.price}\n`;
      }

      const tags = Object.keys(scan.byTag);
      prompt += `\nSECTOR PERFORMANCE:\n`;
      for (const tag of tags) {
        const items = scan.byTag[tag];
        const avg = (items.reduce((s, i) => s + i.changePct, 0) / items.length).toFixed(2);
        prompt += `- ${tag.toUpperCase()}: avg ${avg > 0 ? '+' : ''}${avg}% (${items.map(i => i.symbol).join(', ')})\n`;
      }
    }

    if (news && news.articles) {
      prompt += `\n=== LIVE NEWS FEED (last ${news.articles.length} headlines) ===\n`;
      for (const a of news.articles.slice(0, 12)) {
        const time = new Date(a.publishedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        prompt += `[${time}] [${a.tag.toUpperCase()}] ${a.title} — ${a.source}\n`;
      }
    }

    prompt += buildPredictionMarketsSection(kalshi, poly);
    prompt += buildPortwatchSection(portwatch, portwatchManual);

    prompt += `
=== YOUR TASK ===
Based on everything above — my portfolio, the live market scan, and breaking news — give me:
1. What actually changed in the last hour that matters
2. Which of my positions are affected and how
3. Top 3 war thesis movers I should look at right now with a buy/hold/avoid verdict
4. Any sector showing unusual volume spike (>2x avg) that I'm not positioned in
5. One high-conviction action I should take in the next 30 minutes
Be blunt. No hedging. Treat me like a professional.`;

    return res.status(200).json({
      prompt,
      meta: {
        timestamp: new Date().toISOString(),
        warDay,
        scanTickers: scan?.all?.length || 0,
        newsHeadlines: news?.count || 0,
        positions: positions.length,
        kalshiPositions: kalshi?.positions?.length || 0,
        polyPositions: poly?.positions?.length || 0,
        portwatchSource: portwatch?.source || 'unavailable',
      }
    });

  } catch (err) {
    console.error('[brief] handler failed:', err.message);
    return res.status(500).json({ error: err.message });
  }
}

export function buildPredictionMarketsSection(kalshi, poly) {
  const hasKalshi = kalshi?.positions?.length > 0;
  const hasPoly = poly?.positions?.length > 0;
  if (!hasKalshi && !hasPoly) return '';

  let section = `
══════════════════════════════════════
PREDICTION MARKET POSITIONS
══════════════════════════════════════
`;

  if (hasKalshi) {
    section += '\nKALSHI:\n';
    for (const p of kalshi.positions) {
      const price = p.side === 'NO' ? p.currentNoPrice : p.currentYesPrice;
      const priceStr = price !== null ? `$${price.toFixed(4)}` : 'price unavailable';
      const mvStr = p.currentMarketValue !== null ? `$${p.currentMarketValue.toFixed(2)}` : 'N/A';
      const pnlStr = p.unrealizedPnlPct !== null ? `${p.unrealizedPnlPct >= 0 ? '+' : ''}${p.unrealizedPnlPct.toFixed(2)}%` : 'N/A';
      const resolves = p.closeTime ? new Date(p.closeTime).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : 'unknown';
      section += `→ ${p.ticker} | ${p.contracts} contracts ${p.side} @ avg $${p.avgCost} | Now: ${priceStr}\n`;
      section += `  Market value: ${mvStr} | Unrealized P&L: ${pnlStr}\n`;
      section += `  Resolves: ${resolves}\n`;
      section += `  Thesis: ${p.thesis}\n`;
    }
    if (kalshi.apiNote) section += `  Note: ${kalshi.apiNote}\n`;
  }

  if (hasPoly) {
    section += '\nPOLYMARKET:\n';
    for (const p of poly.positions) {
      const price = p.side === 'NO' ? p.currentNoPrice : p.currentYesPrice;
      const priceStr = price !== null ? `$${price.toFixed(4)}` : 'price unavailable';
      const mvStr = p.currentMarketValue !== null ? `$${p.currentMarketValue.toFixed(2)}` : 'N/A';
      const pnlStr = p.unrealizedPnlPct !== null ? `${p.unrealizedPnlPct >= 0 ? '+' : ''}${p.unrealizedPnlPct.toFixed(2)}%` : 'N/A';
      const resolves = p.closeTime ? new Date(p.closeTime).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : 'unknown';
      section += `→ ${p.ticker} | ${p.contracts} contracts ${p.side} @ avg $${p.avgCost} | Now: ${priceStr}\n`;
      section += `  Market value: ${mvStr} | Unrealized P&L: ${pnlStr}\n`;
      section += `  Resolves: ${resolves}\n`;
      section += `  Thesis: ${p.thesis}\n`;
    }
    if (poly.apiNote) section += `  Note: ${poly.apiNote}\n`;
  }

  return section;
}

export function buildPortwatchSection(portwatch, manualFallback) {
  let section = `
══════════════════════════════════════
HORMUZ TRANSIT DATA — IMF PORTWATCH
══════════════════════════════════════
`;

  if (!portwatch || portwatch.source === 'unavailable') {
    const lastKnown = manualFallback?.ma7day ?? null;
    section += lastKnown !== null
      ? `PortWatch data unavailable. Last known manual value: ${lastKnown} transit calls (as of ${manualFallback.asOf || 'unknown'})\n`
      : `PortWatch data unavailable. No manual fallback set.\n`;
    return section;
  }

  section += `7-day MA: ${portwatch.ma7day !== null ? portwatch.ma7day : 'N/A'} transit calls`;
  if (portwatch.source === 'macromicro') section += ` (threshold for Kalshi NO resolution: 60)`;
  section += `\n`;
  if (portwatch.daily !== null && portwatch.daily !== portwatch.ma7day) {
    section += `Daily latest: ${portwatch.daily}\n`;
  }
  section += `As of: ${portwatch.asOf || 'unknown'} | Source: ${portwatch.source}\n`;
  if (portwatch.warning) section += `⚠ ${portwatch.warning}\n`;

  return section;
}

async function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => data += chunk);
    req.on('end', () => {
      try { resolve(JSON.parse(data)); }
      catch { resolve({}); }
    });
    req.on('error', reject);
  });
}
