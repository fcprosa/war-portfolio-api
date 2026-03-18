// /api/brief.js
// Aggregates positions, market scan, and news into a single structured prompt for Claude

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET');

  try {
    const body = req.method === 'POST' ? await readBody(req) : {};
    const positions = body.positions || [];
    const cash = body.cash || '~$700';

    const base = 'https://war-portfolio-api.vercel.app/api';

    // Fetch scan + news in parallel
    const [scanRes, newsRes] = await Promise.allSettled([
      fetch(`${base}/scan`).then(r => r.json()),
      fetch(`${base}/news`).then(r => r.json()),
    ]);

    const scan = scanRes.status === 'fulfilled' ? scanRes.value : null;
    const news = newsRes.status === 'fulfilled' ? newsRes.value : null;

    // Build structured prompt
    let prompt = `You are my personal war portfolio strategist. Today is ${new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}. US-Iran war Day 18. Hormuz closed. Stagflation regime active.

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

      // Sector summary
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
        scanTickers: scan?.all?.length || 0,
        newsHeadlines: news?.count || 0,
        positions: positions.length,
      }
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
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
