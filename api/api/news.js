const NEWS_KEY = process.env.NEWS_API_KEY;

const WAR_QUERIES = [
  'Hormuz OR Iran war oil',
  'Fed Powell interest rates inflation',
  'EU rearmament defense spending',
  'gold stagflation commodities',
  'private credit banking crisis',
];

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  if (!NEWS_KEY) return res.status(500).json({ error: 'NEWS_API_KEY not configured' });

  try {
    // Fetch with combined query to stay within free tier limits (1 call)
    const q = encodeURIComponent(
      'Hormuz OR "Iran war" OR "Fed rate" OR "EU rearmament" OR stagflation OR "private credit" OR "oil price"'
    );
    const url = `https://newsapi.org/v2/everything?q=${q}&language=en&sortBy=publishedAt&pageSize=20&apiKey=${NEWS_KEY}`;

    const r = await fetch(url, {
      signal: AbortSignal.timeout(8000),
    });

    if (!r.ok) throw new Error(`NewsAPI error ${r.status}`);
    const data = await r.json();

    if (data.status !== 'ok') throw new Error(data.message || 'NewsAPI error');

    const articles = (data.articles || [])
      .filter(a => a.title && !a.title.includes('[Removed]'))
      .map(a => ({
        title: a.title,
        source: a.source?.name || 'Unknown',
        url: a.url,
        publishedAt: a.publishedAt,
        description: a.description?.slice(0, 120) || '',
      }))
      .slice(0, 15);

    // Tag articles by theme
    const tagged = articles.map(a => {
      const text = (a.title + ' ' + a.description).toLowerCase();
      let tag = 'macro';
      if (text.includes('hormuz') || text.includes('iran') || text.includes('oil') || text.includes('crude')) tag = 'oil-war';
      else if (text.includes('fed') || text.includes('powell') || text.includes('rate') || text.includes('inflation')) tag = 'fed';
      else if (text.includes('defense') || text.includes('rearmament') || text.includes('military') || text.includes('nato')) tag = 'defense';
      else if (text.includes('gold') || text.includes('commodity') || text.includes('stagflation')) tag = 'gold';
      else if (text.includes('credit') || text.includes('bank') || text.includes('financial')) tag = 'credit';
      return { ...a, tag };
    });

    return res.status(200).json({
      timestamp: new Date().toISOString(),
      count: tagged.length,
      articles: tagged,
    });

  } catch (err) {
    return res.status(502).json({ error: err.message });
  }
}
